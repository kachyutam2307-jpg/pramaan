"""Guardrails. Per spec section 8.

Three checks, all run after the LLM verdict:
1. enforce_citations  — if verdict != Insufficient and citations is
   empty, force Insufficient. No verdict without evidence.
2. strip_unverified_urls — remove any citation URL not in the set of
   URLs the fetcher actually returned. Kills hallucinated links.
3. enforce_schema — pydantic validation; raise on failure.

These are PURE functions — same input always gives same output. Easy
to unit-test (see tests/test_guardrails.py).
"""
from __future__ import annotations

from typing import Set
from pydantic import ValidationError

from backend.schemas import VerifyResponse, Citation
from backend.utils.logging import get_logger

log = get_logger("reason.guardrails")

# The fallback message when we have to overrule the LLM's verdict.
INSUFFICIENT_MSG = (
    "We could not find an official source that clearly confirms or "
    "denies this claim. Treat it as unverified."
)


def enforce_citations(result: dict) -> dict:
    """If verdict != 'Insufficient Evidence' but citations is empty,
    downgrade the verdict. This is the rule that prevents the LLM
    from fabricating support without evidence."""
    verdict = result.get("verdict", "Insufficient Evidence")
    citations = result.get("citations") or []
    if verdict != "Insufficient Evidence" and not citations:
        log.warning(f"guardrail | enforce_citations | downgrading verdict {verdict} -> Insufficient")
        result["verdict"] = "Insufficient Evidence"
        result["confidence"] = "low"
        result["reasoning_summary"] = INSUFFICIENT_MSG
        result["citations"] = []
    return result


def strip_unverified_urls(result: dict, allowed_urls: Set[str]) -> dict:
    """Remove any citation whose URL is NOT in the set of URLs the
    fetcher actually returned. If all citations get stripped, run
    enforce_citations to downgrade the verdict."""
    kept = []
    for c in result.get("citations", []):
        url = c.get("url", "")
        # Allow exact match OR a gov.in/nic.in subdomain
        if url in allowed_urls or _is_official(url):
            kept.append(c)
        else:
            log.warning(f"guardrail | strip_unverified_urls | dropped {url}")
    result["citations"] = kept
    # If we just nuked all citations, downgrade verdict
    if not kept and result.get("verdict") != "Insufficient Evidence":
        return enforce_citations(result)
    return result


def enforce_schema(result: dict) -> VerifyResponse:
    """Run pydantic validation. Raises ValidationError on failure —
    the caller (FastAPI route) catches it and returns 500 with an
    honest error, never a partially fabricated response."""
    return VerifyResponse(**result)


def _is_official(url: str) -> bool:
    """True if URL ends in a gov.in or nic.in domain. Used as a backup
    when the fetcher's URL list is missing an entry (e.g. for sources
    that go through a redirect)."""
    url = url.lower()
    return any(d in url for d in (".gov.in", ".nic.in", "pib.gov.in", "t.me/pib_factcheck", "t.me/PIB_FactCheck"))
