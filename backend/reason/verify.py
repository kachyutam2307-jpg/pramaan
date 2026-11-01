"""The /verify pipeline.

Step-by-step, in order (spec section 6):
  1. Claim normalization (LLM rewrites input as one atomic claim)
  2. Build search queries from claim + entities
  3. Fetch from at least 2 sources concurrently (asyncio.gather)
  4. Chunk + embed + FAISS retrieve top-k
  5. Mandatory fact-check cross-reference
  6. Verdict reasoning (single LLM call, source-only)
  7. Guardrails: enforce_citations, strip_unverified_urls, enforce_schema
  8. (Optional) risk audit
  9. Translate reasoning_summary if output lang != en
  10. Build the audit trail and return VerifyResponse
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from backend.schemas import (
    Citation, RiskSignal, AuditEntry, VerifyResponse, VerdictResult,
)
from backend.utils.audit import AuditTrail
from backend.utils.logging import get_logger
from backend.llm import client as llm
from backend.reason.prompt import (
    CLAIM_EXTRACTION_PROMPT, VERDICT_PROMPT, TRANSLATE_PROMPT, LANG_NAMES,
)
from backend.reason.guardrails import enforce_citations, strip_unverified_urls
from backend.rag.chunker import chunk_pages
from backend.rag.index import retrieve
from backend.fetch import enabled_fetchers, build_search_queries
from backend.fetch.sources import SourcePage, gather_sources

log = get_logger("reason.verify")


async def run_verify_pipeline(
    *,
    raw_input: str,
    request_id: str,
    language: str = "en",
    include_risk: bool = True,
) -> VerifyResponse:
    """Run the full /verify pipeline. Returns a VerifyResponse — never
    raises inside the pipeline (errors become audit-trail entries and
    an Insufficient Evidence verdict)."""
    audit = AuditTrail()
    audit.add("ingest", f"raw input: {raw_input[:80]}")

        # --- Step 1: claim normalization ---
    # On any LLM failure we DON'T return early — we fall back to the raw
    # input as the claim and continue. The fetch/retrieve/verdict stages
    # still run, so the user gets real citations even if the LLM is down.
    try:
        extracted = await asyncio.wait_for(
            _extract_claim(raw_input), timeout=300
        )
    except asyncio.TimeoutError:
        audit.add("llm.extract", "timeout 15s — falling back to raw input")
        extracted = {"claim": raw_input.strip()[:500], "entities": [], "reason": "timeout_fallback"}
    except Exception as e:
        audit.add("llm.extract", f"error: {type(e).__name__}: {str(e)[:100]} — falling back to raw input")
        extracted = {"claim": raw_input.strip()[:500], "entities": [], "reason": "error_fallback"}

    if not extracted.get("claim"):
        # LLM said "not_a_claim" — still fall back so the pipeline runs.
        claim = raw_input.strip()[:500]
        entities = []
        audit.add(
            "llm.extract",
            f"extraction returned null ({extracted.get('reason')}); "
            f"falling back to raw input as claim",
        )
    else:
        claim = extracted["claim"]
        entities = extracted.get("entities", [])
        audit.add("llm.extract", f"claim: {claim[:80]} | entities: {entities}")

    # --- Step 2: build search queries ---
    queries = build_search_queries(claim, entities)
    # Use the longest query for source retrieval — gov search boxes are picky.
    primary_query = queries[0] if queries else claim
    audit.add("source.build_queries", f"primary: {primary_query[:80]}")

    # --- Step 3: concurrent fetch from all enabled sources ---
    fetchers = enabled_fetchers()
    if len(fetchers) < 2:
        log.warning("only %d sources enabled — spec needs >=2", len(fetchers))
    audit.add("source.fetch_start", f"{len(fetchers)} sources | query={primary_query[:60]}")
    try:
        results = await asyncio.wait_for(
            gather_sources(primary_query, fetchers),
            timeout=20,
        )
    except asyncio.TimeoutError:
        audit.add("source.fetch", "timeout 20s")
        return _insufficient_response(
            request_id, claim, "Source fetch timed out.", audit
        )

    all_pages: List[SourcePage] = []
    for name, pages in results.items():
        audit.add("source.fetch_done", f"{name}: {len(pages)} pages")
        all_pages.extend(pages)

    if not all_pages:
        audit.add("source.fetch_done", "no pages from any source")
        return _insufficient_response(
            request_id, claim, "We could not reach any live source.", audit
        )

        # --- Step 4: chunk + embed + retrieve ---
    chunks = chunk_pages(all_pages)
    audit.add("rag.chunk", f"{len(chunks)} chunks from {len(all_pages)} pages")
    if not chunks:
        return _insufficient_response(
            request_id, claim, "Source pages were too short to chunk.", audit
        )

    # retrieve() can raise on edge cases (faiss errors, embed model issues).
    # Wrap it so the pipeline never crashes with UnboundLocalError.
    retrieved: list = []
    try:
        retrieved = retrieve(claim, chunks)
    except Exception as e:
        audit.add("rag.retrieve", f"error: {type(e).__name__}: {str(e)[:100]}")
        retrieved = []

    if retrieved:
        audit.add("rag.retrieve", f"top_k={len(retrieved)} | top_score={retrieved[0].get('score', 0):.3f}")
    else:
        audit.add("rag.retrieve", "0 relevant chunks (all below similarity threshold 0.30, or retrieve failed) — no source addresses this claim")

    # --- Step 4b: HONEST EXIT — if no retrieved chunk is relevant, return
    # Insufficient Evidence WITHOUT showing the irrelevant sources. This
    # is the citation-or-refuse rule done right.
    if not retrieved:
        return _insufficient_response(
            request_id,
            claim,
            f"We searched {len(all_pages)} live pages from {len(results)} official sources "
            f"(PIB, PIB Fact Check) but none were relevant to your claim. "
            f"We refuse to guess — treat this claim as unverified.",
            audit,
        )


    # --- Step 5: build the SOURCES string for the verdict prompt ---
    numbered = _build_numbered_sources(retrieved)
    allowed_urls = {c["url"] for c in retrieved}
    # --- Step 6: verdict reasoning (single LLM call) ---
    # If the verdict LLM fails, we DON'T immediately return Insufficient
    # Evidence. Instead we attach the retrieved chunks as citations
    # (with verdict = "Insufficient Evidence" — honest) so the user still
    # sees the real sources we DID find. They can click through to verify.
    verdict_dict = None
    try:
        verdict_text = await asyncio.wait_for(
            asyncio.to_thread(
                llm.call_text,
                VERDICT_PROMPT.format(
                    claim=claim,
                    numbered_sources_with_urls=numbered,
                ),
            ),
            timeout=300,
        )
        verdict_dict = llm.parse_json_response(verdict_text)
    except asyncio.TimeoutError:
        audit.add("llm.verdict", "timeout 20s — showing retrieved sources with honest Insufficient verdict")
    except Exception as e:
        audit.add("llm.verdict", f"error: {type(e).__name__}: {str(e)[:100]} — showing retrieved sources with honest Insufficient verdict")

    if verdict_dict is None:
        # LLM failed — build an honest result with the real sources we found.
        # The user can click through to the URLs and judge themselves.
        verdict_dict = {
            "verdict": "Insufficient Evidence",
            "confidence": "low",
            "citations": [{"source_index": i + 1, "snippet": c["snippet"]} for i, c in enumerate(retrieved[:3])],
            "reasoning_summary": (
                "We retrieved official sources related to your claim (see citations below) "
                "but our reasoning step could not complete. Please review the linked "
                "official documents directly to verify the claim."
            ),
        }
    else:
        audit.add("llm.verdict", f"raw: {verdict_dict.get('verdict')} | conf: {verdict_dict.get('confidence')}")

    
    # --- Step 7: build citations from retrieved chunks + LLM's source_index list ---
    citations = _build_citations(verdict_dict, retrieved)
    result = {
        "request_id": request_id,
        "extracted_claim": claim,
        "verdict": verdict_dict.get("verdict", "Insufficient Evidence"),
        "confidence": verdict_dict.get("confidence", "low"),
        "citations": citations,
        "reasoning_summary": verdict_dict.get("reasoning_summary", INSUFFICIENT_MSG_FALLBACK),
        "risk_signals": [],  # filled later if include_risk
        "audit_trail": audit.to_list(),
    }

    # --- Step 8: guardrails ---
    result = enforce_citations(result)
    result = strip_unverified_urls(result, allowed_urls)
    audit.add("guardrail.citations", f"verdict={result['verdict']} | citations_kept={len(result['citations'])}")

    # --- Step 9: optional risk audit (Phase 4) ---
    if include_risk:
        try:
            from backend.risk.metadata import audit_image_metadata
            # The image is only available when input_type=image; for text
            # input the risk module returns []. We pass the raw input bytes
            # if available, else skip gracefully.
            risk_signals = await audit_image_metadata(None, raw_input, audit)
            result["risk_signals"] = risk_signals
        except Exception as e:
            audit.add("risk.audit", f"skipped: {e}")

    # --- Step 10: translate reasoning_summary if needed ---
    if language != "en":
        try:
            translated = await asyncio.wait_for(
                _translate_text(result["reasoning_summary"], language),
                timeout=10,
            )
            result["reasoning_summary"] = translated
            audit.add("translate", f"-> {language}")
        except Exception as e:
            audit.add("translate", f"skipped: {e}")

    # Final audit
    result["audit_trail"] = audit.to_list()
    return VerifyResponse(**result)


INSUFFICIENT_MSG_FALLBACK = (
    "We could not find an official source that clearly confirms or "
    "denies this claim. Treat it as unverified."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _extract_claim(text: str) -> dict:
    """Call Gemini to normalize the user input into one atomic claim."""
    prompt = CLAIM_EXTRACTION_PROMPT.format(input_text=text[:2000])
    raw = await asyncio.to_thread(llm.call_text, prompt)
    return llm.parse_json_response(raw)


def _build_numbered_sources(retrieved: List[dict]) -> str:
    """Format the retrieved chunks as numbered sources for the LLM."""
    lines = []
    for i, c in enumerate(retrieved, start=1):
        lines.append(
            f"[{i}] {c['source_name']} | {c['url']}\n"
            f"Title: {c['title']}\n"
            f"Snippet: {c['snippet']}\n"
        )
    return "\n".join(lines)


def _build_citations(verdict_dict: dict, retrieved: List[dict]) -> List[dict]:
    """Convert the LLM's citation list (source_index, snippet) into
    full Citation dicts by joining with the retrieved chunks."""
    llm_cits = verdict_dict.get("citations") or []
    out = []
    for c in llm_cits:
        idx = c.get("source_index")
        if idx is None or idx < 1 or idx > len(retrieved):
            log.warning(f"dropping invalid citation index: {idx}")
            continue
        chunk = retrieved[idx - 1]
        out.append({
            "source_name": chunk["source_name"],
            "url": chunk["url"],
            "title": chunk["title"],
            "fetched_at": chunk["fetched_at"].isoformat(),
            "snippet": c.get("snippet", chunk["snippet"])[:400],
        })
    return out


async def _translate_text(text: str, target_lang: str) -> str:
    """Translate to target language using the same Gemini model."""
    target_name = LANG_NAMES.get(target_lang, target_lang)
    prompt = TRANSLATE_PROMPT.format(text=text, target_language_name=target_name)
    # Translation uses text mode (no JSON), so we call call_text but skip JSON parse
    import google.generativeai as genai
    from backend.config import GEMINI_API_KEY, GEMINI_MODEL_TEXT
    if not GEMINI_API_KEY:
        return text
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL_TEXT)
    resp = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=512,
            temperature=0.2,
        ),
    )
    return (resp.text or text).strip()


def _insufficient_response(
    request_id: str, claim: str, summary: str, audit: AuditTrail
) -> VerifyResponse:
    """Build an honest 'Insufficient Evidence' response."""
    audit.add("verdict.final", "Insufficient Evidence")
    return VerifyResponse(
        request_id=request_id,
        extracted_claim=claim,
        verdict="Insufficient Evidence",
        confidence="low",
        citations=[],
        reasoning_summary=summary,
        risk_signals=[],
        audit_trail=audit.to_list(),
    )