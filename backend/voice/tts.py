"""TTS — text-to-speech. Two backends:
  - "bhashini": free Indic TTS if BHASHINI_API_KEY is set (best Hindi voice)
  - "edge":    edge-tts (Microsoft's free online TTS, no key required)

Default is "edge" (free, no key, decent Hindi voice)."""
from __future__ import annotations

import base64
import io
from typing import Optional

from backend.config import TTS_BACKEND, BHASHINI_API_KEY
from backend.utils.logging import get_logger

log = get_logger("voice.tts")

# Map our 4 supported output langs to edge-tts voice IDs.
_EDGE_VOICES = {
    "en": "en-IN-NeerjaNeural",
    "hi": "hi-IN-MadhurNeural",
    "ta": "ta-IN-PallaviNeural",
    "bn": "bn-IN-TanishaaNeural",
}


async def synthesize_speech(text: str, language: str = "hi") -> Optional[str]:
    """Convert text -> base64-encoded mp3. Returns None on failure.
    Audio is base64 so it can be embedded in the JSON response and
    played inline by the Streamlit frontend."""
    if not text.strip():
        return None
    if TTS_BACKEND == "bhashini" and BHASHINI_API_KEY:
        try:
            return await _bhashini_tts(text, language)
        except Exception as e:
            log.warning(f"bhashini TTS failed: {e}, falling back to edge-tts")
    return await _edge_tts(text, language)


async def _edge_tts(text: str, language: str) -> Optional[str]:
    """Free Microsoft edge-tts. No API key needed."""
    import edge_tts
    voice = _EDGE_VOICES.get(language, _EDGE_VOICES["hi"])
    log.info(f"edge-tts | voice={voice} | text={len(text)} chars")
    # edge_tts.Communicate is async, returns audio chunks via stream()
    buf = io.BytesIO()
    async for chunk in edge_tts.Communicate(text, voice):
        if chunk["data"]:
            buf.write(chunk["data"])
    if buf.tell() == 0:
        log.warning("edge-tts produced no audio")
        return None
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def _bhashini_tts(text: str, language: str) -> Optional[str]:
    """Bhashini unified API. Documentation:
    https://bhashini.gitbook.io/bhashini-translation-apis/tts

    Two-step:
      1. POST to /GetTTSConfig to get a service URL
      2. POST the text + service URL to synthesize
    """
    import httpx
    base = "https://dhruva-api.bhashini.gov.in/services"
    headers = {"Authorization": BHASHINI_API_KEY, "Content-Type": "application/json"}
    payload = {"pipelineTasks": [{"taskType": "tts", "language": {"sourceLanguage": language}}], "pipelineInConfig": {}}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{base}/pipeline", json=payload, headers=headers)
        r.raise_for_status()
        config = r.json()
        # The response includes a service URL for the TTS call
        tts_service = config.get("pipelineResponseConfig", [{}])[0].get("config", [{}])[0].get("serviceId")
        # Submit synthesis
        synth_payload = {
            "pipelineTasks": [{"taskType": "tts", "config": {"language": {"sourceLanguage": language}, "serviceId": tts_service}}],
            "inputData": [{"source": text}],
        }
        r2 = await c.post(f"{base}/pipeline", json=synth_payload, headers=headers)
        r2.raise_for_status()
        audio_b64 = r2.json().get("pipelineResponse", [{}])[0].get("audio", [{}])[0].get("audioContent")
        return audio_b64
