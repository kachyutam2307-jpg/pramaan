"""Gemini LLM client wrapper. One multimodal model handles vision
(screenshot OCR), text reasoning (claim extraction, verdict), and
translation. All calls go through `call_gemini` which enforces the
hard timeout and parses JSON output.

Models: tries GEMINI_MODEL_FALLBACKS in order. If one 404s (Google
deprecates models), the next is tried automatically. This is what
keeps the app working when Google sunsets gemini-1.5-flash.
"""
from __future__ import annotations

import json
import os
from typing import Optional, Any
from pathlib import Path

from backend.config import GEMINI_API_KEY, GEMINI_MODEL_VISION, GEMINI_MODEL_TEXT, GEMINI_MODEL_FALLBACKS
from backend.utils.logging import get_logger

log = get_logger("llm.gemini")

# Lazy-loaded so tests can run without the API key
_client = None
# Cache which model actually worked — so subsequent calls don't waste
# time trying the broken models again.
_working_text_model: Optional[str] = None
_working_vision_model: Optional[str] = None


def _ensure_client():
    """Lazy-init the Gemini client. Raises if GEMINI_API_KEY missing."""
    global _client
    if _client is not None:
        return _client
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    _client = genai
    log.info(f"gemini client ready | default_model={GEMINI_MODEL_TEXT}")
    return _client


def _try_models_text(prompt: str, *, max_tokens: int, temperature: float, json_mode: bool = True) -> str:
    """Try each candidate text model in order until one works. Caches
    the working model so subsequent calls skip the broken ones."""
    global _working_text_model
    client = _ensure_client()

    # Build the candidate list: configured model first, then fallbacks
    candidates = []
    if _working_text_model:
        candidates.append(_working_text_model)
    if GEMINI_MODEL_TEXT not in candidates:
        candidates.append(GEMINI_MODEL_TEXT)
    for m in GEMINI_MODEL_FALLBACKS:
        if m not in candidates:
            candidates.append(m)

    last_err = None
    for model_name in candidates:
        try:
            model = client.GenerativeModel(model_name)
            kwargs = dict(max_output_tokens=max_tokens, temperature=temperature)
            if json_mode:
                kwargs["response_mime_type"] = "application/json"
            resp = model.generate_content(prompt, generation_config=client.types.GenerationConfig(**kwargs))
            text = resp.text or ""
            if not text:
                raise RuntimeError("empty response")
            # Success — cache this model for future calls
            if _working_text_model != model_name:
                log.info(f"text model | using {model_name} (was {_working_text_model})")
                _working_text_model = model_name
            return text
        except Exception as e:
            msg = str(e)
            log.info(f"model {model_name} failed: {type(e).__name__}: {msg[:80]}")
            last_err = e
            continue
    # All models failed
    raise last_err if last_err else RuntimeError("all text models failed")


def _try_models_vision(prompt: str, image_bytes: bytes, *, mime_type: str, max_tokens: int) -> str:
    """Try each candidate vision model in order until one works."""
    global _working_vision_model
    client = _ensure_client()

    candidates = []
    if _working_vision_model:
        candidates.append(_working_vision_model)
    if GEMINI_MODEL_VISION not in candidates:
        candidates.append(GEMINI_MODEL_VISION)
    for m in GEMINI_MODEL_FALLBACKS:
        if m not in candidates:
            candidates.append(m)

    last_err = None
    for model_name in candidates:
        try:
            model = client.GenerativeModel(model_name)
            parts = [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": image_bytes}},
            ]
            resp = model.generate_content(
                parts,
                generation_config=client.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.1,
                ),
            )
            text = resp.text or ""
            if not text:
                raise RuntimeError("empty response (vision)")
            if _working_vision_model != model_name:
                log.info(f"vision model | using {model_name} (was {_working_vision_model})")
                _working_vision_model = model_name
            return text
        except Exception as e:
            log.info(f"vision model {model_name} failed: {type(e).__name__}")
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("all vision models failed")


def call_text(prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
    """Send a text-only prompt. Returns the model's text response.
    Tries multiple models in order — raises if all fail."""
    return _try_models_text(prompt, max_tokens=max_tokens, temperature=temperature, json_mode=True)


def call_vision(prompt: str, image_bytes: bytes, *,
                mime_type: str = "image/png",
                max_tokens: int = 1024) -> str:
    """Send a prompt + image. Used for screenshot OCR / claim extraction."""
    return _try_models_vision(prompt, image_bytes, mime_type=mime_type, max_tokens=max_tokens)


def parse_json_response(text: str) -> dict:
    """Best-effort JSON parser for LLM output. Strips code fences and
    extracts the outer JSON object."""
    s = text.strip()
    if s.startswith("```"):
        # Strip ```json or ``` fences
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3].strip()
    # Find the outermost {...}
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    return json.loads(s)