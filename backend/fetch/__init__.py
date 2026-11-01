"""Source registry. Single import point for the rest of the backend.
Exposes:
  - enabled_fetchers()  -> dict[name, callable]  used by gather_sources
  - build_search_queries(claim, entities) -> list[str]
"""
from __future__ import annotations

from typing import Callable, List, Awaitable

from backend.config import ENABLED_SOURCES
from backend.fetch import pib, factcheck, myscheme, datagov
from backend.fetch.sources import gather_sources, SourcePage

# Each entry: name -> async (query) -> List[SourcePage]
_ALL_FETCHERS: dict[str, Callable[[str], Awaitable[List[SourcePage]]]] = {
    "pib": pib.fetch,
    "factcheck": factcheck.fetch,
    "myscheme": myscheme.fetch,
    "datagov": datagov.fetch,
}


def enabled_fetchers() -> dict[str, Callable[[str], Awaitable[List[SourcePage]]]]:
    """Return only the fetchers toggled on in config.ENABLED_SOURCES."""
    return {
        name: fn
        for name, fn in _ALL_FETCHERS.items()
        if ENABLED_SOURCES.get(name, False)
    }


def build_search_queries(claim: str, entities: list[str]) -> list[str]:
    """Build a small set of search queries for the fetchers. We use:
       1. The full claim (best precision)
       2. A compact query built from the key entities (best recall)
    More than 3 queries per source is wasteful and burns rate limit.
    """
    queries: list[str] = []
    if claim:
        # Truncate to ~120 chars — gov search boxes don't like long queries.
        queries.append(claim[:120])
    if entities:
        # Pick top 2 most-specific entities (longest first)
        ents = sorted(set(entities), key=len, reverse=True)[:2]
        if ents:
            queries.append(" ".join(ents))
    return queries or [""]

