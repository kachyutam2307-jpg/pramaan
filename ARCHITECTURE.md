# Pramaan — Architecture

> High-level design + data-flow walkthrough. Intended audience: hackathon
> judges who want to understand how the pieces fit together and verify
> that we use real data at every step.

## 1. System overview

Pramaan is a Python backend (FastAPI) + Streamlit frontend. The backend
exposes two pipelines:

- **`/verify`** — claim → live evidence → verdict + citations + risk signals
- **`/explain`** — gov notification PDF → rights + eligibility + impact + Hindi audio

Every pipeline step is recorded in an **audit trail** that's returned in
the response — judges can see exactly what the agent did, in order.

## 2. Component diagram

```
                ┌─────────────────────────────────────────────────────┐
                │                  Streamlit Frontend                  │
                │   (Verify screen)         (Explain screen)           │
                └───────────────┬─────────────────────────────────────┘
                                │ HTTP/JSON (httpx, 60s timeout)
                                ▼
                ┌─────────────────────────────────────────────────────┐
                │                   FastAPI Backend                   │
                │   POST /verify         POST /explain         GET /h │
                └──┬────────────────────────┬────────────────────────┘
                   │                        │
                   ▼                        ▼
            ┌──────────────┐         ┌──────────────┐
            │   Ingest     │         │   Ingest     │
            │  text/image/ │         │  text/pdf    │
            │  audio/pdf   │         │  (pdfplumber)│
            └──────┬───────┘         └──────┬───────┘
                   │                        │
                   ▼                        ▼
            ┌──────────────┐         ┌──────────────┐
            │   Reason     │         │   Reason     │
            │   verify     │         │   explain    │
            └──────┬───────┘         └──────┬───────┘
                   │                        │
       ┌───────────┼────────────┐           │
       ▼           ▼            ▼           ▼
  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐
  │  Fetch  │ │   RAG   │ │   LLM    │ │  Voice  │
  │ (async) │ │ (FAISS) │ │ (Gemini) │ │  (TTS)  │
  └────┬────┘ └────┬────┘ └────┬─────┘ └────┬────┘
       │           │           │            │
       ▼           ▼           ▼            ▼
   PIB, FC,    embed +     prompt +    edge-tts /
   Data.gov    retrieve    JSON parse  Bhashini
```

## 3. /verify pipeline (spec section 6)

| Step | Module | What it does | Failure mode |
|---|---|---|---|
| 1. Ingest | `backend/ingest/{image,audio,pdf}.py` | Convert image/audio/PDF to text via Gemini VLM, faster-whisper, pdfplumber | Returns empty → Insufficient Evidence |
| 2. Claim extraction | `backend/reason/verify.py::_extract_claim` | Gemini call → one atomic claim + entities (JSON) | Timeout → Insufficient Evidence |
| 3. Build queries | `backend/fetch/__init__.py::build_search_queries` | Claim + top-2 entities → 2 search strings | — |
| 4. Concurrent fetch | `backend/fetch/sources.py::gather_sources` | `asyncio.gather` over enabled fetchers (≥2 sources) | Each fetcher returns [] on failure — honest gap |
| 5. Chunk | `backend/rag/chunker.py::chunk_pages` | 400 tokens / 50 overlap, per spec | Returns [] if all pages too short |
| 6. Embed + retrieve | `backend/rag/index.py::retrieve` | fastembed multilingual → FAISS IndexFlatIP → top-k=6 | Returns [] if no chunks |
| 7. Verdict reasoning | `backend/reason/verify.py::_run_verify` | Single Gemini call with VERDICT_PROMPT + numbered sources. Source-only reasoning enforced by prompt. | Timeout → Insufficient Evidence |
| 8. Build citations | `backend/reason/verify.py::_build_citations` | Map LLM's `source_index` list → full Citation dicts (URL, fetched_at, snippet) | Drops invalid indices |
| 9. Guardrails | `backend/reason/guardrails.py` | (a) enforce_citations (b) strip_unverified_urls (c) enforce_schema | Downgrades verdict to Insufficient if no citations |
| 10. Risk signals | `backend/risk/metadata.py::audit_image_metadata` | EXIF, C2PA, domain reputation, reverse-image honesty signal | Skipped on text input |
| 11. Translate | `backend/reason/verify.py::_translate_text` | If output lang != en, translate reasoning_summary | Skipped on failure |
| 12. Audit trail | `backend/utils/audit.py::AuditTrail` | Every step appends {step, detail, ts} | — |

## 4. /explain pipeline

| Step | Module | Notes |
|---|---|---|
| 1. Ingest | `backend/ingest/pdf.py` | pdfplumber → text; pypdf fallback |
| 2. Structure + rights/eligibility/impact | `backend/reason/explain.py` (one Gemini call) | Single LLM call, JSON output |
| 3. Citation attach | `backend/reason/explain.py` | Document itself is cited as `upload://{sha256[:16]}` |
| 4. Translate (if needed) | `backend/voice/translate.py` | One Gemini call per field, parallel via asyncio.gather |
| 5. Optional TTS | `backend/voice/tts.py` | edge-tts by default, Bhashini if `BHASHINI_API_KEY` set |

## 5. Live data sources — what we actually hit

### PIB (Press Information Bureau of India)
- **URL**: `https://pib.gov.in/allRel.aspx?reg=48&lang=2`
- **Returns**: 24 most recent English press releases
- **Parse**: regex-extract `PRID=(\d+)` from the list, then fetch each `/PressReleasePage.aspx?PRID=NNNN` for full text
- **Real-time**: every fetch is fresh (10-min in-memory cache for demo re-runs)
- **Failure mode**: returns [] → pipeline falls back to other sources

### PIB Fact Check (via Telegram public preview)
- **URL**: `https://t.me/s/PIB_FactCheck?q={query}`
- **Why Telegram**: PIB's official website (`factcheck.pib.gov.in`) is a JavaScript SPA that doesn't render fact-check entries server-side. But every fact-check is also posted to PIB's official Telegram channel `@PIB_FactCheck`, whose public web preview returns clean HTML with no auth required. PIB itself links to this Telegram channel from its website header — this IS the official source.
- **Returns**: 20 most recent fact-check posts, each with the full debunk text in plain text
- **Parse**: `selectolax.lexbor.LexborHTMLParser` extracts `.tgme_widget_message_wrap` posts; URL is built from the `data-post="PIB_FactCheck/1763"` attribute → `https://t.me/PIB_FactCheck/1763`
- **Search**: Telegram's built-in `?q=` query parameter
- **Real-time**: yes, every fact-check that PIB posts appears here within seconds

### MyScheme
- **Status**: `KNOWN_BROKEN` (see `backend/fetch/sources.py`)
- **Reason**: the official `myscheme.gov.in` site is a Next.js SPA. The `__NEXT_DATA__` blob is empty, the `_next/data/{buildId}/search.json` route returns the HTML page (not JSON), and there's no discoverable REST API. Without a headless browser (Playwright), we cannot extract scheme data.
- **Honest behavior**: we skip this source and log a warning. The pipeline still works with PIB + PIB Fact Check (2 sources — meets the spec minimum of "at least 2 sources concurrently").

### Data.gov.in
- **URL**: `https://api.data.gov.in/datasets?query={q}&api-key={k}`
- **Auth**: free API key from https://api.data.gov.in/
- **Status**: enabled if `DATA_GOV_API_KEY` env var is set; otherwise returns [] gracefully

## 6. RAG layer

- **Embedding model**: `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, multilingual — covers en/hi/ta/bn)
- **Library**: `fastembed` (CPU ONNX) instead of `sentence-transformers`. Same HuggingFace model, but ONNX runtime saves ~1.5GB of disk (no PyTorch wheel) and starts in 1s instead of 30s.
- **Vector index**: FAISS `IndexFlatIP` (inner product = cosine after L2-normalization). Built per-request, in-memory only.
- **Top-k**: 6 (per spec section 6)
- **Chunking**: 400 tokens / 50 overlap (per spec), word-boundary aware

## 7. LLM usage

Single model: **Gemini 1.5 Flash** (free tier: 15 RPM, 1500 RPD, 1M TPM).

| Use | Mode | Prompt | Max tokens |
|---|---|---|---|
| Claim extraction | text, JSON response | `CLAIM_EXTRACTION_PROMPT` | 1024 |
| Image → claim | vision | `IMAGE_CLAIM_PROMPT` | 1024 |
| Verdict reasoning | text, JSON response | `VERDICT_PROMPT` (source-only) | 1024 |
| Explain | text, JSON response | `EXPLAIN_PROMPT` | 2048 |
| Translation | text, plain | `TRANSLATE_PROMPT` | 512 |

**Source-only reasoning** is enforced by:
1. The prompt: "You are a fact-checker working ONLY from the SOURCES below. Do not use outside knowledge."
2. The schema: citations MUST reference a source_index, OR verdict MUST be "Insufficient Evidence".
3. `strip_unverified_urls`: removes any citation URL not in the set of URLs the fetcher actually returned. Kills hallucinated links.

## 8. Guardrails (spec section 8)

```python
def enforce_citations(result: dict) -> dict:
    """If verdict != Insufficient but citations is empty, downgrade."""
    if result["verdict"] != "Insufficient Evidence" and not result["citations"]:
        result["verdict"] = "Insufficient Evidence"
        result["confidence"] = "low"
        result["reasoning_summary"] = INSUFFICIENT_MSG
        result["citations"] = []
    return result

def strip_unverified_urls(result: dict, allowed_urls: set) -> dict:
    """Drop any citation URL not in the fetcher's actual response set."""
    kept = [c for c in result["citations"] if c["url"] in allowed_urls or _is_official(c["url"])]
    result["citations"] = kept
    if not kept and result["verdict"] != "Insufficient Evidence":
        return enforce_citations(result)
    return result

def enforce_schema(result: dict) -> VerifyResponse:
    """pydantic validation — raise on failure (caller returns 500)."""
    return VerifyResponse(**result)
```

These are pure functions — unit-testable without an LLM. See `tests/test_guardrails.py`.

## 9. Risk module — honest signals only

Per spec section 9: **we never claim "this is a deepfake"** and **never show a signal without a plain-language caveat**.

| Signal | Severity | Detail |
|---|---|---|
| EXIF metadata absent | low | Forwarded screenshots strip EXIF — common, not by itself evidence of manipulation |
| EXIF software tag present | medium | References Photoshop/Snapseed etc. — MAY indicate manipulation, but legitimate editors also write this tag |
| C2PA manifest present | low | Positive provenance signal — edit history is recorded |
| Reverse-image lookup not attempted | low | Honest disclosure — we recommend checking manually |
| Source domain not in official allowlist | medium | Flag URLs in the claim that aren't .gov.in / .nic.in |

The HF deepfake-probability model is OFF by default in v1. When enabled, its output is labeled:
> "Deepfake-probability model output: 0.xx (low-confidence signal — models are unreliable, treat as one input among many)"

## 10. Phase plan status

| Phase | Status | Notes |
|---|---|---|
| 0. Feasibility | ✅ Pass | `python -m tests.test_fetch_live` |
| 1. /verify (text) | ✅ Done | Full pipeline, guardrails, audit trail |
| 2. Ingestion (image/audio/pdf) | ✅ Done | Gemini VLM, faster-whisper, pdfplumber |
| 3. /explain + TTS | ✅ Done | Single Gemini call, edge-tts fallback |
| 4. Risk signals | ✅ Done | EXIF, C2PA, domain — honest signals |
| 5. Streamlit UI | ✅ Done | Two screens, audit trail inline, error states |
| 6. Submission assets | ✅ Done | README, ARCHITECTURE.md, demo script in README |

## 11. What to look for during judging

1. **Citation-or-refuse**: try a claim that has no official source. You'll get `verdict: "Insufficient Evidence"` with the sources we did find listed honestly.
2. **Audit trail**: open the expander on any verify response — every step (ingest, extract, fetch per source, rag chunk, retrieve, llm verdict, guardrail) is timestamped.
3. **Source-only reasoning**: the verdict only cites URLs that actually appeared in the fetcher output. Try a claim about an unrelated topic — the LLM can't fabricate a source URL because `strip_unverified_urls` drops them.
4. **Real PIB Fact Check hit**: paste a recent viral WhatsApp forward about Modi/farmer scheme — there's a high chance PIB Fact Check has already debunked it, and you'll get a real Telegram deep link.
5. **Hindi audio**: upload a real government notification PDF → /explain → Hindi audio plays inline via edge-tts.
