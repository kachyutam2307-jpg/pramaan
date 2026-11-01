"""Translation. Wrapper that uses Gemini (same model, free tier)
for translation to keep things simple. The translate.py module exists
for spec compliance — TTS module uses it indirectly.

If BHASHINI_API_KEY is set, falls back to Bhashini's free Indic
translation API for higher quality."""
from __future__ import annotations

from typing import Optional
import httpx

from backend.config import BHASHINI_API_KEY
from backend.reason.prompt import TRANSLATE_PROMPT, LANG_NAMES
from backend.utils.logging import get_logger

log = get_logger("voice.translate")


async def translate(text: str, target_lang: str) -> str:
    """Translate `text` to `target_lang`. Returns original text on failure."""
    if not text or target_lang == "en":
        return text
    if BHASHINI_API_KEY:
        try:
            return await _bhashini_translate(text, target_lang)
        except Exception as e:
            log.warning(f"bhashini translate failed: {e}, falling back to LLM")
    return await _llm_translate(text, target_lang)


async def _llm_translate(text: str, target_lang: str) -> str:
    """Use Gemini to translate. Free, no extra key."""
    from backend.llm import client as llm
    from backend.config import GEMINI_API_KEY, GEMINI_MODEL_TEXT
    if not GEMINI_API_KEY:
        return text
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    target_name = LANG_NAMES.get(target_lang, target_lang)
    prompt = TRANSLATE_PROMPT.format(text=text, target_language_name=target_name)
    model = genai.GenerativeModel(GEMINI_MODEL_TEXT)
    resp = await _async_gen(model, prompt)
    return (resp or text).strip()


async def _async_gen(model, prompt):
    """Run Gemini's sync generate_content in a thread."""
    import asyncio
    from backend.config import GEMINI_MODEL_TEXT
    import google.generativeai as genai
    r = await asyncio.to_thread(
        lambda: model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=1024, temperature=0.2,
            ),
        ).text
    )
    return r


async def _bhashini_translate(text: str, target_lang: str) -> str:
    """Bhashini unified translation API."""
    base = "https://dhruva-api.bhashini.gov.in/services"
    headers = {"Authorization": BHASHINI_API_KEY, "Content-Type": "application/json"}
    # Source language auto-detection via "auto" — Bhashini supports it for some models
    source = "en"
    payload = {
        "pipelineTasks": [{"taskType": "translation",
                            "config": {"language": {"sourceLanguage": source, "targetLanguage": target_lang}}}],
        "inputData": [{"source": text}],
    }
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{base}/pipeline", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        out = data.get("pipelineResponse", [{}])[0].get("output", [{}])[0].get("target", "")
        return out
