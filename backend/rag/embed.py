"""Embedding model loader. Uses fastembed (CPU ONNX) so we don't need
PyTorch — saves ~1.5GB of disk + startup time. Multilingual model
covers English + Hindi + Tamil + Bengali."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from fastembed import TextEmbedding

from backend.config import EMBED_MODEL_NAME
from backend.utils.logging import get_logger

log = get_logger("rag.embed")

# Map the spec's sentence-transformers names to fastembed equivalents.
# Fastembed serves the same Hugging Face model files via ONNX.
_MODEL_MAP = {
    "paraphrase-multilingual-MiniLM-L12-v2":
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "all-MiniLM-L6-v2": "sentence-transformers/all-MiniLM-L6-v2",
}


@lru_cache(maxsize=1)
def get_embedder() -> TextEmbedding:
    """Return a singleton TextEmbedder. Loads once per process."""
    name = _MODEL_MAP.get(EMBED_MODEL_NAME, EMBED_MODEL_NAME)
    log.info(f"loading embedder | model={name}")
    return TextEmbedding(model_name=name)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts. Returns a list of float vectors.
    Empty input -> empty list. The fastembed library is synchronous
    so we don't need async here."""
    if not texts:
        return []
    model = get_embedder()
    # fastembed returns a generator of numpy arrays — convert to lists
    return [list(v) for v in model.embed(texts)]


def embed_one(text: str) -> List[float]:
    """Convenience for a single text. Returns a 384-dim vector."""
    return embed_texts([text])[0]
