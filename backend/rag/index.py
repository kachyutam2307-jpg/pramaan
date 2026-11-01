"""Per-request FAISS index. Builds an in-memory index for the chunks
retrieved in this /verify call, then runs top-k similarity search
against the claim embedding. Nothing is persisted — every request
gets a fresh index built from live-fetched pages.

IMPORTANT: applies a similarity threshold. If the best chunk scores
below 0.30 (cosine similarity), we return [] — because that means
NONE of the fetched sources are actually about the claim. Better to
return an honest "no relevant sources" than to surface irrelevant
links as if they were evidence.
"""
from __future__ import annotations

from typing import List
import numpy as np

import faiss

from backend.config import RETRIEVE_TOP_K
from backend.rag.embed import embed_one, embed_texts
from backend.utils.logging import get_logger

log = get_logger("rag.index")

# Cosine similarity cutoff. 0.30 is conservative — anything below this
# is almost certainly irrelevant. Tune up to 0.40 for stricter matching.
SIMILARITY_THRESHOLD = 0.30


def retrieve(claim: str, chunks: List[dict], top_k: int = RETRIEVE_TOP_K) -> List[dict]:
    """Build a per-request FAISS index over `chunks`, then return the
    top_k most similar chunks to `claim` — but ONLY those scoring >=
    SIMILARITY_THRESHOLD. If the best chunk is below the threshold,
    returns [] (honest "no relevant sources" signal).

    `chunks` is the output of rag.chunker.chunk_pages — list of dicts
    with keys: text, source_name, url, title, fetched_at, snippet.
    """
    if not chunks or not claim.strip():
        return []

    # 1) Embed the claim
    claim_vec = np.array([embed_one(claim)], dtype=np.float32)

    # 2) Embed all chunks
    chunk_texts = [c["text"] for c in chunks]
    chunk_vecs = np.array(embed_texts(chunk_texts), dtype=np.float32)
    if chunk_vecs.size == 0:
        return []

    # 3) Build FAISS index (L2 normalized + inner product = cosine)
    dim = chunk_vecs.shape[1]
    faiss.normalize_L2(chunk_vecs)
    faiss.normalize_L2(claim_vec)
    index = faiss.IndexFlatIP(dim)
    index.add(chunk_vecs)

    # 4) Top-k search
    k = min(top_k, len(chunks))
    scores, indices = index.search(claim_vec, k)
    top_score = float(scores[0][0]) if len(scores[0]) > 0 else 0.0
    log.info(f"faiss | chunks={len(chunks)} | k={k} | top_score={top_score:.3f} | threshold={SIMILARITY_THRESHOLD}")

    # 5) Honest filter: if the BEST chunk is below threshold, return []
    # This is the key fix — prevents surfacing irrelevant sources when
    # none of the fetched pages are actually about the claim.
    if top_score < SIMILARITY_THRESHOLD:
        log.info(f"top_score {top_score:.3f} < threshold {SIMILARITY_THRESHOLD} — returning no retrievals (honest)")
        return []

    # 6) Build output — only chunks above threshold
    out: List[dict] = []
    for i, idx in enumerate(indices[0]):
        if idx < 0:
            continue
        score = float(scores[0][i])
        if score < SIMILARITY_THRESHOLD:
            continue  # drop low-similarity chunks
        chunk = dict(chunks[idx])  # copy
        chunk["score"] = score
        out.append(chunk)
    log.info(f"retrieve | returned {len(out)} relevant chunks (of {len(chunks)} fetched)")
    return out