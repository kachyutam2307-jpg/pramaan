"""MyScheme scraper.

MyScheme (myscheme.gov.in) is a Next.js SPA. The cleanest live-data
endpoint is the Next.js data route:
  GET https://www.myscheme.gov.in/_next/data/{buildId}/search.json?query={q}

The buildId changes per deploy, so we fetch the search HTML page first
to extract the current buildId, then hit the JSON endpoint.

If anything fails, we return [] — never fabricate scheme entries.
"""
from __future__ import annotations

import json
import re
from typing import List
from urllib.parse import quote_plus

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from backend.fetch.sources import SourcePage, safe_fetch, now_iso
from backend.utils.logging import get_logger

log = get_logger("fetch.myscheme")

MYSHEME_BASE = "https://www.myscheme.gov.in"
SEARCH_PAGE = MYSHEME_BASE + "/search?query={q}"
DATA_ENDPOINT = MYSHEME_BASE + "/_next/data/{build_id}/search.json?query={q}"
SCHEME_PAGE = MYSHEME_BASE + "/schemes/{slug}"
MAX_RESULTS = 5


async def fetch(query: str) -> List[SourcePage]:
    """Search MyScheme for `query`. Returns up to MAX_RESULTS scheme
    detail pages with full eligibility text."""
    if not query.strip():
        return []

    q = quote_plus(query)
    # Step 1: fetch the search HTML page to extract the buildId
    search_html = await safe_fetch(
        SEARCH_PAGE.format(q=q), source_name="myscheme"
    )
    if not search_html:
        return []
    text = search_html.decode("utf-8", errors="ignore")
    m = re.search(r'"buildId":"([^"]+)"', text)
    if not m:
        log.warning("myscheme buildId not found in HTML")
        return []
    build_id = m.group(1)

    # Step 2: hit the JSON data route
    data_url = DATA_ENDPOINT.format(build_id=build_id, q=q)
    data_bytes = await safe_fetch(data_url, source_name="myscheme")
    if not data_bytes:
        return []
    try:
        data = json.loads(data_bytes)
    except json.JSONDecodeError:
        log.warning("myscheme data endpoint returned non-JSON")
        return []

    page_props = data.get("pageProps", {})
    schemes = (
        page_props.get("schemes")
        or page_props.get("data", {}).get("schemes")
        or page_props.get("searchResults")
        or []
    )
    if not isinstance(schemes, list) or not schemes:
        log.info(f"myscheme returned 0 schemes for: {query[:50]}")
        return []

    return await _fetch_scheme_pages(schemes[:MAX_RESULTS])


async def _fetch_scheme_pages(schemes: list) -> List[SourcePage]:
    """For each scheme dict, fetch its detail page for full text."""
    pages: List[SourcePage] = []
    for s in schemes:
        if not isinstance(s, dict):
            continue
        title = s.get("schemeName") or s.get("name") or s.get("title") or "Scheme"
        slug = s.get("slug") or s.get("schemeSlug")
        if not slug:
            continue
        url = SCHEME_PAGE.format(slug=slug)
        html_bytes = await safe_fetch(url, source_name="myscheme")
        if not html_bytes:
            continue
        html = html_bytes.decode("utf-8", errors="ignore")
        body = _extract_text(html)
        if not body or len(body) < 80:
            # Fallback: use whatever short text came in the JSON list
            body = (s.get("description") or s.get("summary") or title).strip()
            if len(body) < 80:
                continue
        pages.append(
            SourcePage(
                source_name="MyScheme",
                url=url,
                title=title,
                fetched_at=now_iso(),
                text=body,
                snippet=body[:300].replace("\n", " "),
            )
        )
    log.info(f"myscheme returned {len(pages)} scheme pages")
    return pages


def _extract_text(html: str) -> str:
    """Pull body text from a scheme detail page (SSR'd with content)."""
    tree = HTMLParser(html)
    for sel in ["main", "article", ".scheme-detail", ".content"]:
        node = tree.css_first(sel)
        if node:
            text = node.text(separator=" ", strip=True)
            if len(text) > 80:
                return text
    paragraphs = [p.text(strip=True) for p in tree.css("p") if p.text(strip=True)]
    return " ".join(paragraphs)
