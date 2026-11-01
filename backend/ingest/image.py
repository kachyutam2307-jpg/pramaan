"""Image -> claim extractor. Uses Gemini's multimodal endpoint to
read a screenshot of a social-media post / news headline / WhatsApp
forward and extract the single most checkable claim."""
from __future__ import annotations

import json
from typing import Optional

from backend.llm import client as llm
from backend.reason.prompt import IMAGE_CLAIM_PROMPT
from backend.utils.logging import get_logger

log = get_logger("ingest.image")


async def extract_claim_from_image(file_bytes: bytes) -> str:
    """Send the image to Gemini with the IMAGE_CLAIM_PROMPT, parse the
    JSON, return the extracted claim text. If the image has no claim,
    returns 'not_a_claim' so the pipeline returns Insufficient Evidence."""
    if not file_bytes:
        return ""

    # Detect MIME type from first bytes (PNG / JPEG magic numbers)
    mime = _detect_mime(file_bytes)
    log.info(f"image ingest | bytes={len(file_bytes)} | mime={mime}")

    raw = await llm.call_vision(IMAGE_CLAIM_PROMPT, file_bytes, mime_type=mime)
    parsed = llm.parse_json_response(raw)
    claim = parsed.get("claim")
    if not claim:
        log.info(f"image: not a claim ({parsed.get('reason')})")
        return "not_a_claim"
    return claim


def _detect_mime(file_bytes: bytes) -> str:
    """Sniff the first bytes to detect image format."""
    if file_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if file_bytes.startswith(b"GIF8"):
        return "image/gif"
    if file_bytes.startswith(b"RIFF") and b"WEBP" in file_bytes[:12]:
        return "image/webp"
    return "image/png"  # safe default
