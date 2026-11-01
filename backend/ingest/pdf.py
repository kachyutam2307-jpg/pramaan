"""PDF text extractor. Uses pdfplumber for layout-aware extraction.
Falls back to pypdf if pdfplumber fails."""
from __future__ import annotations

import io
from typing import Optional

from backend.utils.logging import get_logger

log = get_logger("ingest.pdf")


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Pull plain text out of a PDF. Tries pdfplumber first, then pypdf.
    Returns '' on failure — caller will see no text and route to
    Insufficient Evidence."""
    if not file_bytes:
        return ""
    text = _try_pdfplumber(file_bytes) or _try_pypdf(file_bytes)
    if not text:
        log.warning("pdf extract: 0 chars from both extractors")
    return text


def _try_pdfplumber(file_bytes: bytes) -> Optional[str]:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            parts = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t:
                    parts.append(t)
            return "\n\n".join(parts).strip()
    except Exception as e:
        log.info(f"pdfplumber failed: {e}")
        return None


def _try_pypdf(file_bytes: bytes) -> Optional[str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        parts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            if t:
                parts.append(t)
        return "\n\n".join(parts).strip()
    except Exception as e:
        log.info(f"pypdf failed: {e}")
        return None
