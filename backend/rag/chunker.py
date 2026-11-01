"""Text chunker. Splits long source documents into RAG-sized chunks
(400 tokens with 50-token overlap, per spec section 6)."""
from __future__ import annotations

from typing import List

from backend.config import CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS

# Rough estimate: 1 token ≈ 4 chars for English, ≈ 2 chars for Hindi.
# Use 3 as a compromise. Not perfect — the chunker only needs to
# produce "good enough" chunks, not exact token counts.
_CHARS_PER_TOKEN = 3


def chunk_text(text: str, *, chunk_tokens: int = CHUNK_SIZE_TOKENS,
               overlap_tokens: int = CHUNK_OVERLAP_TOKENS) -> List[str]:
    """Split `text` into overlapping chunks. Word-boundary aware so
    we don't cut mid-word. Returns [] for empty input."""
    if not text or not text.strip():
        return []

    words = text.split()
    chunk_size = chunk_tokens * _CHARS_PER_TOKEN  # in words (approx)
    overlap = overlap_tokens * _CHARS_PER_TOKEN

    # If text is short enough, return as one chunk
    total_chars = sum(len(w) + 1 for w in words)
    if total_chars <= chunk_size:
        return [text.strip()]

    chunks: List[str] = []
    start = 0
    while start < len(words):
        end = start + chunk_size // 4  # words avg 4 chars
        if end >= len(words):
            end = len(words)
        chunk = " ".join(words[start:end]).strip()
        if len(chunk) >= 80:  # discard tiny tail chunks
            chunks.append(chunk)
        if end >= len(words):
            break
        # Move forward by (chunk_size - overlap)
        step = max(1, (chunk_size - overlap) // 4)
        start += step
    return chunks


def chunk_pages(pages) -> List[dict]:
    """Take a list of SourcePage and return list of {text, source_name,
    url, title, fetched_at, snippet} dicts — one per chunk."""
    out: List[dict] = []
    for p in pages:
        for chunk in chunk_text(p.text):
            out.append({
                "text": chunk,
                "source_name": p.source_name,
                "url": p.url,
                "title": p.title,
                "fetched_at": p.fetched_at,
                "snippet": chunk[:300],
            })
    return out
