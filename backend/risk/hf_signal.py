"""Optional HF model signal. OFF by default in v1 because deepfake-
probability models are unreliable on their own (spec section 9).

When enabled (HF_TOKEN set + USE_HF_DEEPFAKE=true), this module queries
a Hugging Face deepfake-detection model and returns the probability
as a low-confidence signal — never a definitive verdict."""
from __future__ import annotations

import os
from typing import Optional

from backend.config import HF_TOKEN
from backend.utils.logging import get_logger

log = get_logger("risk.hf_signal")

# A small, fast deepfake-detection model. The output is treated as a
# single low-confidence signal among many.
_MODEL_ID = "Organika/sdxl-detector"  # placeholder, override via env


def query_hf_model(image_bytes: bytes) -> Optional[float]:
    """Query the HF model and return a probability [0,1]. Returns
    None if HF_TOKEN missing or the model fails."""
    if not HF_TOKEN:
        log.info("hf signal skipped — HF_TOKEN not set")
        return None
    try:
        import httpx
        r = httpx.post(
            f"https://api-inference.huggingface.co/models/{_MODEL_ID}",
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            content=image_bytes,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        # HF image-classification models return [{score, label}, ...]
        if isinstance(data, list) and data:
            fake_label = next((d for d in data if "fake" in d.get("label", "").lower()), None)
            if fake_label:
                return float(fake_label["score"])
            return float(data[0].get("score", 0))
        return None
    except Exception as e:
        log.warning(f"hf signal failed: {e}")
        return None
