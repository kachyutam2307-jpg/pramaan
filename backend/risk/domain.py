"""Domain reputation list. Used by the risk module + guardrails to
classify URLs that appear in user claims."""
from __future__ import annotations

from backend.config import OFFICIAL_DOMAINS


def is_official(url: str) -> bool:
    """True if URL is on an official .gov.in / .nic.in / india.gov.in
    domain. Used to flag URLs in user claims that don't match the
    official allowlist."""
    if not url:
        return False
    u = url.lower()
    return any(d in u for d in OFFICIAL_DOMAINS)


def classify(url: str) -> str:
    """Return a one-word label for a URL: 'official', 'social_media',
    'news', or 'unknown'."""
    if not url:
        return "unknown"
    u = url.lower()
    if is_official(u):
        return "official"
    if any(d in u for d in ("whatsapp.com", "facebook.com", "twitter.com",
                              "x.com", "instagram.com", "t.me", "youtube.com")):
        return "social_media"
    if any(d in u for d in ("ndtv.com", "thehindu.com", "timesofindia.com",
                              "indiatoday.in", "bbc.com", "reuters.com")):
        return "news"
    return "unknown"
