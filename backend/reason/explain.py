"""/explain pipeline.

Takes a government notification PDF or text and returns:
  - document_title, issuing_authority, effective_date
  - plain_summary (3-5 sentences, no legalese)
  - rights, eligibility, impact (short arrays of plain sentences)
  - citations (the original document, since it IS the source)
  - optional translated output + Hindi audio (TTS)

Pipeline per spec section 6 (/explain):
  1. Ingest (pdf or text) — already done by app.py
  2. Structure extraction (LLM: title, authority, date)
  3. Rights/eligibility/impact in one LLM call
  4. Citation attach (the doc itself is the citation)
  5. Translate + optional TTS
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

from backend.schemas import ExplainResponse, Citation
from backend.utils.audit import AuditTrail
from backend.utils.logging import get_logger
from backend.llm import client as llm
from backend.reason.prompt import EXPLAIN_PROMPT, TRANSLATE_PROMPT, LANG_NAMES

log = get_logger("reason.explain")


async def run_explain_pipeline(
    text: str,
    request_id: str,
    language: str = "en",
    generate_audio: bool = False,
) -> ExplainResponse:
    """Run the /explain pipeline. Never raises inside — returns an
    ExplainResponse with whatever fields the LLM managed to produce,
    honest empty fields when something failed."""
    audit = AuditTrail()
    audit.add("ingest", f"document text: {len(text)} chars")
    if not text.strip() or len(text) < 80:
        audit.add("ingest", "document too short to explain")
        return ExplainResponse(
            request_id=request_id,
            document_title="(empty document)",
            plain_summary="The document you provided is too short or empty. Please upload a longer PDF or paste more text.",
            rights=[], eligibility=[], impact=[], citations=[],
            audit_trail=audit.to_list(),
        )

    # --- Step 2 + 3: one LLM call for structure + rights/eligibility/impact ---
    prompt = EXPLAIN_PROMPT.format(document_text=text[:8000])
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(llm.call_text, prompt),
            timeout=25,
        )
        parsed = llm.parse_json_response(raw)
        audit.add("llm.explain", f"title={parsed.get('document_title', '')[:60]}")
    except asyncio.TimeoutError:
        audit.add("llm.explain", "timeout 25s")
        return ExplainResponse(
            request_id=request_id,
            document_title="(explanation failed)",
            plain_summary="The explainer call timed out. Please try again.",
            rights=[], eligibility=[], impact=[], citations=[],
            audit_trail=audit.to_list(),
        )
    except Exception as e:
        audit.add("llm.explain", f"error: {e}")
        return ExplainResponse(
            request_id=request_id,
            document_title="(explanation failed)",
            plain_summary=f"Could not parse the document: {type(e).__name__}",
            rights=[], eligibility=[], impact=[], citations=[],
            audit_trail=audit.to_list(),
        )

    # --- Step 4: citation attach ---
    # The document itself IS the citation. Hash the doc for traceability.
    doc_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    citations = [Citation(
        source_name="User-submitted document",
        url=f"upload://{doc_hash}",
        title=parsed.get("document_title", "User document"),
        fetched_at=datetime.now(timezone.utc),
        snippet=text[:300].replace("\n", " "),
    )]
    audit.add("citation", f"doc_hash={doc_hash}")

    # --- Step 5: optional translate ---
    rights = parsed.get("rights", [])
    eligibility = parsed.get("eligibility", [])
    impact = parsed.get("impact", [])
    summary = parsed.get("plain_summary", "")
    if language != "en":
        try:
            summary, rights, eligibility, impact = await asyncio.wait_for(
                _translate_explain_fields(summary, rights, eligibility, impact, language),
                timeout=15,
            )
            audit.add("translate", f"-> {language}")
        except Exception as e:
            audit.add("translate", f"skipped: {e}")

    # --- Step 6: optional TTS ---
    audio_b64 = None
    if generate_audio:
        try:
            from backend.voice.tts import synthesize_speech
            audio_b64 = await asyncio.wait_for(
                synthesize_speech(summary, language),
                timeout=20,
            )
            audit.add("tts", f"audio={len(audio_b64)} chars b64 | lang={language}")
        except Exception as e:
            audit.add("tts", f"skipped: {e}")

    return ExplainResponse(
        request_id=request_id,
        document_title=parsed.get("document_title", "Document"),
        plain_summary=summary,
        rights=rights,
        eligibility=eligibility,
        impact=impact,
        citations=citations,
        audio_b64=audio_b64,
        audit_trail=audit.to_list(),
    )


async def _translate_explain_fields(summary, rights, eligibility, impact, target_lang):
    """Translate each field to the target language. One LLM call per
    field to keep prompts simple (avoids the LLM truncating arrays)."""
    target_name = LANG_NAMES.get(target_lang, target_lang)
    async def _t(text: str) -> str:
        if not text or not text.strip():
            return text
        prompt = TRANSLATE_PROMPT.format(text=text, target_language_name=target_name)
        import google.generativeai as genai
        from backend.config import GEMINI_API_KEY, GEMINI_MODEL_TEXT
        if not GEMINI_API_KEY:
            return text
        genai.configure(api_key=GEMINI_API_KEY)
        m = genai.GenerativeModel(GEMINI_MODEL_TEXT)
        r = m.generate_content(prompt, generation_config=genai.types.GenerationConfig(
            max_output_tokens=512, temperature=0.2,
        ))
        return (r.text or text).strip()
    new_summary = await _t(summary)
    new_rights = await asyncio.gather(*[_t(r) for r in rights])
    new_elig = await asyncio.gather(*[_t(e) for e in eligibility])
    new_impact = await asyncio.gather(*[_t(i) for i in impact])
    return new_summary, list(new_rights), list(new_elig), list(new_impact)
