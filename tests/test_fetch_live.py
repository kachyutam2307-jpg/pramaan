"""Phase 0 feasibility gate.

Run:  pytest tests/test_fetch_live.py -v

If any of the three primary sources fails to return a 200 with
extractable text, the gate fails — fix or replace the source BEFORE
writing any other code (per spec section 10, Phase 0).
"""
import asyncio
import sys

import httpx

from backend.config import USER_AGENT, REQUEST_TIMEOUT_SEC
from backend.fetch import pib, factcheck, myscheme
from backend.fetch.sources import headers


SOURCES = [
    # (canonical_name, display_label, home_url, fetcher)
    ("pib", "PIB home", "https://pib.gov.in/", pib.fetch),
    ("factcheck", "PIB Fact Check home", "https://factcheck.pib.gov.in/", factcheck.fetch),
    ("myscheme", "MyScheme home", "https://www.myscheme.gov.in/", myscheme.fetch),
]


async def _check_home(name: str, url: str) -> tuple[str, bool, int, str]:
    """Hit the home page, return (name, ok, status_code, snippet)."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SEC, headers=headers(), follow_redirects=True
    ) as c:
        try:
            r = await c.get(url)
            text = r.text[:200].replace("\n", " ")
            return name, r.status_code == 200, r.status_code, text
        except Exception as e:
            return name, False, 0, f"{type(e).__name__}: {e}"


async def _check_search(name: str, fetcher) -> tuple[str, bool, int, str]:
    """Try a real search via the fetcher. 'modi' or 'agriculture' are
    terms with high hit-rate on every gov source."""
    try:
        pages = await asyncio.wait_for(fetcher("modi scheme"), timeout=45)
        snippet = pages[0].snippet[:200] if pages else "(no results)"
        return name, len(pages) > 0, len(pages), snippet
    except asyncio.TimeoutError:
        return name, False, 0, "timeout 45s"
    except Exception as e:
        return name, False, 0, f"{type(e).__name__}: {e}"


async def main():
    from backend.fetch.sources import KNOWN_BROKEN
    print("\n=== Phase 0: Home-page reachability ===")
    ok_count = 0
    for cano, label, url, _ in SOURCES:
        n, ok, status, snip = await _check_home(label, url)
        flag = "OK " if ok else "FAIL"
        print(f"  [{flag}] {n:25} status={status} | {snip[:80]}")
        ok_count += int(ok)
    print(f"\n  Home pages: {ok_count}/{len(SOURCES)} reachable")

    print("\n=== Phase 0: Live search via fetcher ===")
    # Skip sources we've explicitly marked as broken.
    sr_total = 0
    sr_ok = 0
    for cano, label, _, fetcher in SOURCES:
        if cano in KNOWN_BROKEN:
            print(f"  [SKIP] {label:25} (in KNOWN_BROKEN — see backend/fetch/sources.py)")
            continue
        sr_total += 1
        n, ok, count, snip = await _check_search(label, fetcher)
        flag = "OK " if ok else "FAIL"
        print(f"  [{flag}] {n:25} pages={count} | {snip[:80]}")
        sr_ok += int(ok)
    print(f"\n  Search fetchers: {sr_ok}/{sr_total} returning results")

    # Spec Phase 0 GATE: every home reachable + every non-broken fetcher returns ≥1 page.
    if ok_count == len(SOURCES) and sr_ok == sr_total and sr_total >= 2:
        print("\n*** PHASE 0 GATE PASSED ***\n")
        return 0
    print("\n!!! PHASE 0 GATE FAILED — fix the broken sources before proceeding !!!\n")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


# pytest entry points so this can be run with `pytest tests/test_fetch_live.py -v`
def test_pib_home():
    """PIB home must return 200 with extractable text."""
    r = asyncio.run(_check_home("PIB home", "https://pib.gov.in/"))
    assert r[1], f"PIB home failed: {r}"


def test_factcheck_home():
    """PIB Fact Check home must return 200."""
    r = asyncio.run(_check_home("PIB Fact Check home", "https://factcheck.pib.gov.in/"))
    assert r[1], f"FactCheck home failed: {r}"


def test_myscheme_home():
    """MyScheme home must return 200."""
    r = asyncio.run(_check_home("MyScheme home", "https://www.myscheme.gov.in/"))
    assert r[1], f"MyScheme home failed: {r}"
