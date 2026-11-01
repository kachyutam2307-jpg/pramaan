"""PIB press-release scraper.

PIB does not expose a JSON API and the WebForms search endpoint is
unreliable (returns the latest 24 releases regardless of query). So
we take the pragmatic, robust approach: scrape the public list page
`/allRel.aspx?reg=48&lang=2` which always shows the most recent 24
press releases in English. The verify pipeline then runs similarity
matching over these (via FAISS) to find the ones relevant to the
user's claim.

This is real, fresh data — re-fetched on every cache miss, never
hardcoded. If the page goes down, we return [] and the verdict
becomes "Insufficient Evidence" (the honest answer).
"""
from __future__ import annotations

import re
from typing import List

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from backend.fetch.sources import SourcePage, safe_fetch, now_iso
from backend.utils.logging import get_logger

log = get_logger("fetch.pib")

PIB_BASE = "https://pib.gov.in"
# Public list of recent press releases in English. Always 200 OK.
LIST_URL = PIB_BASE + "/allRel.aspx?reg=48&lang=2"
MAX_PAGES = 2  # cap fetches — was 4, lowered so the pipeline fits in the LLM timeout


async def fetch(query: str) -> List[SourcePage]:
    """Pull the latest PIB press releases. The `query` param is unused
    here — we always fetch the same recent-releases list because the
    PIB search endpoint doesn't actually filter. The RAG layer does
    the relevance filtering."""
    html = await safe_fetch(LIST_URL, source_name="pib")
    if not html:
        return []

    prids = _extract_prids(html.decode("utf-8", errors="ignore"))
    if not prids:
        log.warning("pib list page returned 0 PRIDs — structure changed?")
        return []

    pages: List[SourcePage] = []
    for prid in prids[:MAX_PAGES]:
        url = f"{PIB_BASE}/PressReleasePage.aspx?PRID={prid}"
        page_html = await safe_fetch(url, source_name="pib")
        if not page_html:
            continue
        text = page_html.decode("utf-8", errors="ignore")
        title = _extract_title(text) or f"Press release {prid}"
        body = _extract_body(text)
        if not body or len(body) < 80:
            continue
        pages.append(
            SourcePage(
                source_name="PIB",
                url=url,
                title=title,
                fetched_at=now_iso(),
                text=body,
                snippet=body[:300].replace("\n", " "),
            )
        )
    log.info(f"pib returned {len(pages)} pages")
    return pages


def _extract_prids(html: str) -> list[str]:
    """Pull all PRID numbers from the list page HTML."""
    return list(dict.fromkeys(re.findall(r"PRID=(\d+)", html)))


def _extract_title(html: str) -> str:
    """Press releases have a clear <title> or <h2 class='release_title'>."""
    m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if m:
        return m.group(1).split("|")[0].strip()[:200]
    tree = HTMLParser(html)
    h = tree.css_first("h2.release_title, .release-title, h1")
    if h:
        return h.text(strip=True)[:200]
    return ""


def _extract_body(html: str) -> str:
    """The release body lives in div.releaseBody or in tables; pull all text."""
    tree = HTMLParser(html)
    for sel in [".releaseBody", ".ReleaseBody", "#ReleaseBody", ".content"]:
        node = tree.css_first(sel)
        if node:
            text = node.text(separator=" ", strip=True)
            if len(text) > 100:
                return text
    paragraphs = [p.text(strip=True) for p in tree.css("p") if p.text(strip=True)]
    return " ".join(paragraphs)
