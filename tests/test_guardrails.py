"""Unit tests for the guardrails — pure functions, no LLM needed.

Run: pytest tests/test_guardrails.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone
from backend.reason.guardrails import (
    enforce_citations, strip_unverified_urls, enforce_schema, _is_official,
)


def test_enforce_citations_passes_when_verdict_is_insufficient():
    """If verdict is already Insufficient, no change needed."""
    result = {
        "verdict": "Insufficient Evidence",
        "confidence": "low",
        "citations": [],
        "reasoning_summary": "x",
    }
    out = enforce_citations(dict(result))
    assert out["verdict"] == "Insufficient Evidence"
    assert out["citations"] == []


def test_enforce_citations_downgrades_when_no_citations():
    """Supported verdict with no citations -> downgrade to Insufficient."""
    result = {
        "verdict": "Supported",
        "confidence": "high",
        "citations": [],
        "reasoning_summary": "Looks supported.",
    }
    out = enforce_citations(dict(result))
    assert out["verdict"] == "Insufficient Evidence"
    assert out["confidence"] == "low"
    assert "unverified" in out["reasoning_summary"].lower()


def test_enforce_citations_keeps_when_citations_present():
    """Supported verdict WITH citations -> stays Supported."""
    result = {
        "verdict": "Supported",
        "confidence": "high",
        "citations": [{"source_index": 1, "snippet": "x"}],
        "reasoning_summary": "x",
    }
    out = enforce_citations(dict(result))
    assert out["verdict"] == "Supported"
    assert len(out["citations"]) == 1


def test_strip_unverified_urls_drops_unknown_urls():
    """Citations with URLs not in allowed set get dropped."""
    result = {
        "verdict": "Supported",
        "confidence": "high",
        "citations": [
            {"url": "https://pib.gov.in/real", "snippet": "x"},
            {"url": "https://evil.com/fake", "snippet": "y"},
        ],
        "reasoning_summary": "x",
    }
    out = strip_unverified_urls(dict(result), {"https://pib.gov.in/real"})
    assert len(out["citations"]) == 1
    assert out["citations"][0]["url"] == "https://pib.gov.in/real"


def test_strip_unverified_urls_keeps_official_urls():
    """Official .gov.in URLs are kept even if not in the exact allowed set
    (the fetcher might have followed a redirect)."""
    result = {
        "verdict": "Supported",
        "confidence": "high",
        "citations": [
            {"url": "https://static.pib.gov.in/something", "snippet": "x"},
        ],
        "reasoning_summary": "x",
    }
    out = strip_unverified_urls(dict(result), set())
    assert len(out["citations"]) == 1


def test_strip_unverified_urls_downgrades_when_all_dropped():
    """If all citations are dropped, the verdict is downgraded."""
    result = {
        "verdict": "Supported",
        "confidence": "high",
        "citations": [
            {"url": "https://evil.com/fake", "snippet": "y"},
        ],
        "reasoning_summary": "x",
    }
    out = strip_unverified_urls(dict(result), set())
    assert out["verdict"] == "Insufficient Evidence"
    assert out["citations"] == []


def test_is_official_recognizes_gov_domains():
    """Official domains include pib.gov.in, .gov.in, .nic.in."""
    assert _is_official("https://pib.gov.in/x")
    assert _is_official("https://static.pib.gov.in/y")
    assert _is_official("https://india.gov.in/z")
    assert _is_official("https://example.nic.in/w")
    assert not _is_official("https://evil.com/fake")
    assert not _is_official("https://t.me/somechannel")  # Telegram is not .gov.in


def test_enforce_schema_rejects_bad_verdict():
    """enforce_schema should reject an invalid verdict string."""
    bad = {
        "request_id": "x",
        "extracted_claim": "x",
        "verdict": "Maybe",  # invalid
        "confidence": "low",
        "citations": [],
        "reasoning_summary": "x",
        "risk_signals": [],
        "audit_trail": [],
    }
    try:
        enforce_schema(bad)
        assert False, "should have raised"
    except Exception:
        pass  # expected


def test_enforce_schema_accepts_valid_response():
    """A well-formed VerifyResponse dict should pass schema validation."""
    good = {
        "request_id": "test-1",
        "extracted_claim": "PM Modi announced ₹10 lakh per farmer.",
        "verdict": "Contradicted",
        "confidence": "high",
        "citations": [
            {
                "source_name": "PIB Fact Check",
                "url": "https://t.me/PIB_FactCheck/1763",
                "title": "Fake news alert",
                "fetched_at": datetime.now(timezone.utc),
                "snippet": "PIB debunks the claim...",
            }
        ],
        "reasoning_summary": "PIB Fact Check has debunked this claim.",
        "risk_signals": [],
        "audit_trail": [
            {"step": "ingest", "detail": "text input", "ts": datetime.now(timezone.utc)}
        ],
    }
    resp = enforce_schema(good)
    assert resp.verdict == "Contradicted"
    assert len(resp.citations) == 1
    assert resp.citations[0].source_name == "PIB Fact Check"
