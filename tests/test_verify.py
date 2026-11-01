"""Smoke test for the /verify pipeline.

Two test classes:
  1. Without GEMINI_API_KEY — tests the pipeline up to the LLM call.
     Confirms: source fetch works, RAG works, guardrails work, the
     pipeline returns Insufficient Evidence gracefully when LLM is
     unreachable.
  2. With GEMINI_API_KEY — runs a real end-to-end verify on a real
     claim. Skipped automatically if no key. Set RUN_LIVE_LLM_TESTS=1
     to opt in.

Run: pytest tests/test_verify.py -v
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from httpx import AsyncClient, ASGITransport

from backend.app import app
from backend.fetch import enabled_fetchers, build_search_queries
from backend.fetch.sources import gather_sources
from backend.rag.chunker import chunk_pages
from backend.rag.index import retrieve


# --- Live source + RAG layer (no LLM, no API key needed) ---

@pytest.mark.asyncio
async def test_enabled_fetchers_have_at_least_two():
    """Spec requires at least 2 enabled sources."""
    f = enabled_fetchers()
    assert len(f) >= 2, f"need >=2 sources, got {list(f)}"


@pytest.mark.asyncio
async def test_gather_sources_returns_real_pages():
    """Live-fetch from at least one source and confirm we get real text."""
    fetchers = enabled_fetchers()
    results = await gather_sources("modi kisan scheme", fetchers)
    total_pages = sum(len(p) for p in results.values())
    assert total_pages >= 1, f"got 0 pages from any source: {results}"
    # Confirm pages have real text
    for name, pages in results.items():
        for p in pages:
            assert len(p.text) > 80, f"{name} returned a page with no text"
            assert p.url.startswith("http"), f"{name} returned a page with no URL"
            assert p.fetched_at is not None


def test_build_search_queries_returns_strings():
    """Queries should be non-empty strings."""
    queries = build_search_queries("PM Modi announced ₹2000 for farmers", ["Modi", "PM Kisan"])
    assert len(queries) >= 1
    assert all(isinstance(q, str) and q for q in queries)


def test_chunker_splits_long_text():
    """Long text should be chunked into multiple pieces."""
    long_text = "This is a test sentence. " * 200  # ~4400 chars
    from backend.rag.chunker import chunk_text
    chunks = chunk_text(long_text)
    assert len(chunks) >= 2
    assert all(len(c) > 50 for c in chunks)


def test_retrieve_returns_scored_chunks():
    """RAG retrieve should return chunks with score >= 0.30 (threshold).
    Chunks below threshold are filtered out — better to return nothing
    than irrelevant results."""
    chunks = [
        {"text": "PM Kisan scheme gives ₹6000 per year to small farmers.", "source_name": "PIB",
         "url": "https://pib.gov.in/x", "title": "PM Kisan", "fetched_at": "2024-01-01T00:00:00Z",
         "snippet": "PM Kisan"},
        {"text": "The weather in Delhi is sunny today.", "source_name": "PIB",
         "url": "https://pib.gov.in/y", "title": "Weather", "fetched_at": "2024-01-01T00:00:00Z",
         "snippet": "Weather"},
    ]
    retrieved = retrieve("What is the PM Kisan scheme?", chunks, top_k=2)
    # PM Kisan chunk should be retrieved (high similarity)
    assert len(retrieved) >= 1
    assert "score" in retrieved[0]
    assert 0.30 <= retrieved[0]["score"] <= 1.0
    # PM Kisan chunk should rank highest
    assert "PM Kisan" in retrieved[0]["text"]
    # Weather chunk should be filtered out by the similarity threshold
    weather = [c for c in retrieved if "weather" in c["text"].lower()]
    assert len(weather) == 0, "weather chunk should be filtered out by threshold"


def test_retrieve_returns_empty_when_nothing_relevant():
    """When no chunk is relevant to the claim, retrieve returns [] —
    the honest 'no relevant sources' signal."""
    chunks = [
        {"text": "PM Kisan scheme gives ₹6000 per year to small farmers.", "source_name": "PIB",
         "url": "https://pib.gov.in/x", "title": "PM Kisan", "fetched_at": "2024-01-01T00:00:00Z",
         "snippet": "PM Kisan"},
        {"text": "The weather in Delhi is sunny today.", "source_name": "PIB",
         "url": "https://pib.gov.in/y", "title": "Weather", "fetched_at": "2024-01-01T00:00:00Z",
         "snippet": "Weather"},
    ]
    # Unrelated claim — should return [] because no chunk is similar enough
    retrieved = retrieve("Gyanesh kumar has been arrested", chunks, top_k=2)
    assert retrieved == [], f"expected [] for irrelevant claim, got {len(retrieved)} chunks"


# --- Full pipeline smoke test (requires GEMINI_API_KEY) ---

@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY") or not os.getenv("RUN_LIVE_LLM_TESTS"),
    reason="needs GEMINI_API_KEY + RUN_LIVE_LLM_TESTS=1",
)
async def test_verify_pipeline_end_to_end():
    """Live end-to-end verify against a real claim. This calls the LLM
    and consumes API quota. Skipped unless RUN_LIVE_LLM_TESTS=1."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/verify", json={
            "input_type": "text",
            "text": "PM Modi announced ₹10 lakh cash payment for every farmer in India",
            "language": "en",
            "include_risk": True,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] in ("Supported", "Contradicted", "Insufficient Evidence")
        assert data["confidence"] in ("low", "medium", "high")
        assert isinstance(data["citations"], list)
        assert isinstance(data["audit_trail"], list)
        assert len(data["audit_trail"]) >= 3
        # If verdict != Insufficient, we MUST have citations (guardrail)
        if data["verdict"] != "Insufficient Evidence":
            assert len(data["citations"]) >= 1
            for c in data["citations"]:
                assert c["url"].startswith("http")
                assert c["fetched_at"]


# --- Without LLM, the pipeline should return Insufficient Evidence ---

@pytest.mark.asyncio
async def test_verify_pipeline_handles_missing_llm_gracefully():
    """When GEMINI_API_KEY is not set, the verify pipeline should
    return Insufficient Evidence rather than crash."""
    if os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY is set — this test is for the no-key path")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/verify", json={
            "input_type": "text",
            "text": "Some random claim about PM Modi",
            "language": "en",
            "include_risk": False,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] == "Insufficient Evidence"
        assert data["confidence"] == "low"
        assert data["citations"] == []
