"""Audio -> transcript. Uses faster-whisper (small model, CPU).
Returns plain text. The verify pipeline then runs claim extraction
on the transcript via Gemini.

If faster-whisper isn't installed (e.g. on Streamlit Cloud without
the wheel), the function returns '' — caller will see no transcript
and route to Insufficient Evidence. Honest failure, no fake text.
"""
from __future__ import annotations

import io
import os
import tempfile
from typing import Optional

from backend.utils.logging import get_logger

log = get_logger("ingest.audio")

# Cached model — loaded once per process. The "small" model is 480MB
# but produces decent Hindi transcripts. "base" is 145MB if disk is tight.
_WHISPER_MODEL = None
_WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "small")


async def transcribe_audio(file_bytes: bytes) -> str:
    """Transcribe an audio file (mp3, m4a, wav) using faster-whisper.
    Returns the transcript text. Returns '' on failure."""
    if not file_bytes:
        return ""
    model = _get_model()
    if model is None:
        log.warning("whisper model not available — install faster-whisper")
        return ""
    # faster-whisper needs a file path; write to a temp file.
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        log.info(f"transcribing {len(file_bytes)}B audio | model={_WHISPER_MODEL_NAME}")
        segments, info = model.transcribe(tmp_path, beam_size=1)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info(f"transcript: {len(text)} chars | lang={info.language}")
        return text
    except Exception as e:
        log.error(f"transcription failed: {e}")
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _get_model():
    """Lazy-load the whisper model."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    try:
        from faster_whisper import WhisperModel
        # CPU mode, int8 quantization for speed on free tier
        _WHISPER_MODEL = WhisperModel(_WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
        log.info(f"whisper model loaded: {_WHISPER_MODEL_NAME}")
        return _WHISPER_MODEL
    except ImportError:
        log.warning("faster_whisper not installed — audio input will fail gracefully")
        return None
    except Exception as e:
        log.warning(f"whisper model load failed: {e}")
        return None
