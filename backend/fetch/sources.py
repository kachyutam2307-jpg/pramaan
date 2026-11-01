"""Source registry. Each source = (name, fetcher_callable, query_builder).
The verify pipeline calls each enabled source's fetch() with a search
query and gets back a list of SourcePage dicts."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from datetime import datetime, timezone

from backend.config import USER_AGENT, REQUEST_TIMEOUT_SEC, SOURCE_RATE_LIMIT_SEC, CACHE_TTL_SEC
from backend.utils.logging import get_logger

log = get_logger("fetch.sources")

# Track last-fetch time per domain to enforce rate limit
_LAST_FETCH_TS: dict[str, float] = {}

# 10-minute in-memory cache per spec section 3. Key = (source, query)
_PAGE_CACHE: dict[tuple[str, str], tuple[float, list]] = {}

# Sources that are known to be broken on demo day. Update this list
# if a site starts 403ing — never fabricate data instead.
KNOWN_BROKEN: set[str] = {
    "myscheme",  # site is fully client-rendered; no SSR HTML to scrape.
                 # team can fix later by adding Playwright or finding the
                 # hidden API. Honest empty result instead of fake data.
}


@dataclass
class SourcePage:
    """One fetched page from a live source. Used by the RAG layer."""
    source_name: str
    url: str
    title: str
    fetched_at: datetime
    text: str
    snippet: str = ""  # short extract for citation display


async def _rate_limit(domain: str) -> None:
    """Sleep to respect 1 req/sec per domain. Called before each fetch."""
    last = _LAST_FETCH_TS.get(domain, 0.0)
    elapsed = time.monotonic() - last
    if elapsed < SOURCE_RATE_LIMIT_SEC:
        await asyncio.sleep(SOURCE_RATE_LIMIT_SEC - elapsed)
    _LAST_FETCH_TS[domain] = time.monotonic()


def _cache_get(source: str, query: str) -> Optional[List[SourcePage]]:
    """Return cached pages if within CACHE_TTL_SEC, else None."""
    key = (source, query)
    if key in _PAGE_CACHE:
        ts, pages = _PAGE_CACHE[key]
        if time.time() - ts < CACHE_TTL_SEC:
            log.info(f"cache hit | {source} | {query[:50]}")
            return pages
    return None


def _cache_put(source: str, query: str, pages: List[SourcePage]) -> None:
    _PAGE_CACHE[(source, query)] = (time.time(), pages)


def headers() -> dict:
    """Standard HTTP headers — gov sites block default python-requests UA."""
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    }


async def safe_fetch(
    url: str, *,
    source_name: str,
    client=None,
) -> Optional[bytes]:
    """Fetch a single URL with rate limit + log. Returns None on failure.
    NEVER raises — callers handle None as 'this source had nothing'."""
    import httpx
    domain = url.split("/")[2]
    if source_name in KNOWN_BROKEN:
        log.warning(f"skip broken source | {source_name}")
        return None
    await _rate_limit(domain)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SEC, headers=headers(), follow_redirects=True
        )
    try:
        t0 = time.monotonic()
        r = await client.get(url)
        latency = (time.monotonic() - t0) * 1000
        log.info(
            f"fetch | {source_name} | {r.status_code} | "
            f"{len(r.content)}B | {latency:.0f}ms | {url[:80]}"
        )
        if r.status_code != 200:
            return None
        return r.content
    except Exception as e:
        log.warning(f"fetch error | {source_name} | {type(e).__name__}: {e}")
        return None
    finally:
        if own_client:
            await client.aclose()


def now_iso() -> datetime:
    """UTC now — used to stamp every Citation.fetched_at."""
    return datetime.now(timezone.utc)


async def gather_sources(
    query: str,
    fetchers: dict[str, Callable[[str], "any"]],
) -> dict[str, List[SourcePage]]:
    """Run all enabled fetchers concurrently. Returns {source_name: pages}.
    If a source returns None or [], the entry is just empty — caller
    sees honest gaps, not fabricated data."""
    tasks = []
    names = []
    for name, fn in fetchers.items():
        cached = _cache_get(name, query)
        if cached is not None:
            tasks.append(_return_cached(name, cached))
        else:
            tasks.append(_wrap_fetch(name, fn, query))
        names.append(name)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, List[SourcePage]] = {}
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            log.warning(f"source failed | {name} | {res}")
            out[name] = []
            continue
        pages = res or []
        out[name] = pages
        _cache_put(name, query, pages)
    return out


async def _return_cached(name: str, pages: List[SourcePage]):
    return pages


async def _wrap_fetch(name: str, fn: Callable, query: str):
    try:
        return await fn(query)
    except Exception as e:
        log.warning(f"fetcher exception | {name} | {e}")
        return []
