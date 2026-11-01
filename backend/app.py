"""FastAPI app. Three endpoints:
  - POST /verify (spec section 5)
  - POST /explain (spec section 5)
  - GET  /health

Hard 30s timeout per endpoint per spec.
"""
from __future__ import annotations

import asyncio
import uuid
import base64
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.config import ENDPOINT_TIMEOUT_SEC
from backend.schemas import (
    VerifyRequest, VerifyResponse, ExplainRequest, ExplainResponse,
    HealthResponse, ErrorPayload,
)
from backend.utils.logging import get_logger
from backend.utils.audit import AuditTrail

log = get_logger("app")

app = FastAPI(
    title="Pramaan",
    description="Civic verification + explainer agent. No mock data — every verdict hits a live source.",
    version="0.1.0",
)

# CORS — let Streamlit (different origin) call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(asyncio.TimeoutError)
async def timeout_handler(request: Request, exc: asyncio.TimeoutError):
    """Catch the 30s endpoint timeout and return a clean 504."""
    return JSONResponse(
        status_code=504,
        content=ErrorPayload(detail="Request exceeded 30s timeout.", request_id=None).model_dump(),
    )


@app.get("/")
async def root():
    """Tiny landing so visiting the API root doesn't 404."""
    return {"name": "Pramaan", "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse)
async def health():
    """Quick liveness check. Doesn't call out to sources (that would
    be too slow for a health probe) — just confirms the app is up."""
    return HealthResponse(status="ok", sources_up={"pib": True, "factcheck": True, "myscheme": False, "datagov": False})


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    """Main /verify endpoint. Hard 30s timeout per spec."""
    request_id = str(uuid.uuid4())
    # Validate file presence for image/audio/pdf input
    if req.needs_file() and not req.file_b64:
        raise HTTPException(400, f"input_type={req.input_type} requires file_b64")
    if req.input_type == "text" and not (req.text or "").strip():
        raise HTTPException(400, "input_type=text requires non-empty `text` field")

    # Build the raw input string for the verify pipeline.
    # For text input it's the text itself. For image/audio/pdf, the
    # ingest module extracts text first (Phase 2). For Phase 1 we only
    # wire text input through; other types are wired in Phase 2.
    raw_input = req.text or ""
    if req.input_type != "text":
        try:
            raw_input = await _ingest(req.input_type, req.file_b64 or "")
        except Exception as e:
            log.error(f"ingest failed: {e}")
            raise HTTPException(400, f"ingest failed: {e}")

    try:
        result = await asyncio.wait_for(
            _run_verify(raw_input, request_id, req.language, req.include_risk),
            timeout=ENDPOINT_TIMEOUT_SEC,
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(504, f"verify timed out after {ENDPOINT_TIMEOUT_SEC}s | request_id={request_id}")
    except Exception as e:
        log.exception("verify pipeline failed")
        raise HTTPException(500, f"verify failed: {type(e).__name__}: {e} | request_id={request_id}")


@app.post("/explain", response_model=ExplainResponse)
async def explain(req: ExplainRequest):
    """/explain endpoint. Phase 3."""
    from backend.reason.explain import run_explain_pipeline
    request_id = str(uuid.uuid4())
    if req.input_type == "text" and not (req.text or "").strip():
        raise HTTPException(400, "input_type=text requires non-empty `text` field")
    if req.input_type == "pdf" and not req.file_b64:
        raise HTTPException(400, "input_type=pdf requires file_b64")

    if req.input_type == "pdf":
        try:
            text = await _ingest_pdf(req.file_b64 or "")
        except Exception as e:
            raise HTTPException(400, f"pdf ingest failed: {e}")
    else:
        text = req.text or ""

    try:
        result = await asyncio.wait_for(
            run_explain_pipeline(text, request_id, req.language, req.generate_audio),
            timeout=ENDPOINT_TIMEOUT_SEC,
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(504, f"explain timed out after {ENDPOINT_TIMEOUT_SEC}s | request_id={request_id}")
    except Exception as e:
        log.exception("explain pipeline failed")
        raise HTTPException(500, f"explain failed: {type(e).__name__}: {e} | request_id={request_id}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run_verify(raw_input, request_id, language, include_risk):
    """Wrapper so we can mock in tests."""
    from backend.reason.verify import run_verify_pipeline
    return await run_verify_pipeline(
        raw_input=raw_input,
        request_id=request_id,
        language=language,
        include_risk=include_risk,
    )


async def _ingest(input_type: str, file_b64: str) -> str:
    """Dispatch to the right ingest module (Phase 2)."""
    file_bytes = base64.b64decode(file_b64) if file_b64 else b""
    if input_type == "image":
        from backend.ingest.image import extract_claim_from_image
        return await extract_claim_from_image(file_bytes)
    if input_type == "audio":
        from backend.ingest.audio import transcribe_audio
        return await transcribe_audio(file_bytes)
    if input_type == "pdf":
        from backend.ingest.pdf import extract_text_from_pdf
        return extract_text_from_pdf(file_bytes)
    raise ValueError(f"unknown input_type: {input_type}")


async def _ingest_pdf(file_b64: str) -> str:
    file_bytes = base64.b64decode(file_b64) if file_b64 else b""
    from backend.ingest.pdf import extract_text_from_pdf
    return extract_text_from_pdf(file_bytes)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
