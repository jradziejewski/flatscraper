"""Street and house number from a listing's own page, for the rows the list data left without a street.

The list pages carry only a title (Otodom, nieruchomosci) or the first ~300 characters of the description (Morizon), so some
listings have their address only on the listing's page. For every row still without a street, `fill_addresses` fetches that page
once and runs `find_address` on the listing's OWN text. Which fields count is decided per portal, on purpose: a page also holds the
agency's office address, the portal's company address and nearby parks, and reading "everything" would give a flat the wrong street.

- Otodom: `ad.title` and `ad.description` (not `ad.owner` / `ad.agency`, which carry the agency's address)
- Morizon: `propertyData.description` (the full text) and the page title ("... | Wrocław, ul. Łubinowa/Parafialna")
- nieruchomosci-online: the description block, and the page title when it is the portal's address line ("Boczna Karmelkowej Wrocław, Klecina")
- OLX: its single-offer API (only needed for older rows the list no longer returns: the list already has the full text)

Each page is fetched at most once: the answer, including "no street found" (null), is cached in addresscache.json, keyed by
"site:id", and entries of listings that are gone are dropped. Like geocoding this is a nicety: a failure is logged and the run goes on.
"""
import json
import logging
import re
import subprocess
import time
import urllib.error
from collections import Counter
from dataclasses import replace
from pathlib import Path

from . import http
from .address import find_address
from .scrapers import morizon, olx, otodom
from .streets import street_index, words
from .text import plain_text

log = logging.getLogger(__name__)

CACHE_PATH = "addresscache.json"
DELAY_S = 2.0  # between page fetches, like the scrapers
DEFAULT_LIMIT = 25  # pages per run: the backfill is one-off, later runs only meet a few new gaps
MAX_FAILURES = 3  # a portal that fails this many fetches in a run is left alone until the next run: something is wrong there
GONE = (404, 410)  # the portal says the listing no longer exists: a final answer, not a failure

Cache = dict[str, list[str | None] | None]


# ---------- readers: page -> the text that is about THIS listing ----------

def read_otodom(page: str, city: str | None = None) -> str:
    m = otodom._NEXT_DATA.search(page)
    if not m:
        raise ValueError("otodom: __NEXT_DATA__ not found")
    ad = json.loads(m.group(1))["props"]["pageProps"]["ad"]
    return f"{ad.get('title') or ''} . {plain_text(ad.get('description'))}"


def _find_key(obj, key: str, depth: int = 0):
    """The first value stored under `key` anywhere in nested dicts/lists (Morizon's page data has a changing outer key)."""
    if depth > 9:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj[:60]
    else:
        return None
    for child in children:
        found = _find_key(child, key, depth + 1)
        if found is not None:
            return found
    return None


def read_morizon(page: str, city: str | None = None) -> str:
    m = morizon._NUXT_DATA.search(page)
    if not m:
        raise ValueError("morizon: __NUXT_DATA__ not found")
    data = morizon.unflatten(json.loads(m.group(1)))
    prop = _find_key(data, "propertyData") or {}
    head = _find_key(data, "headTags") or {}
    return f"{head.get('title') or ''} . {plain_text(prop.get('description'))}"


_NO_MORE = re.compile(r'<div class="estate-desc-more"[^>]*>(.*?)</div>', re.S)
_NO_LESS = re.compile(r'<div class="estate-desc-less"[^>]*>(.*?)</div>', re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)


def read_nieruchomosci(page: str, city: str | None = "Wrocław") -> str:
    desc = _NO_MORE.search(page) or _NO_LESS.search(page)
    title = plain_text(_TITLE.search(page).group(1)) if _TITLE.search(page) else ""
    # the <title> is either the seller's own headline or the portal's address line "<Street> Wrocław, <neighbourhood>":
    # it is read as a street only when the words before the city are in the street list in full
    street = ""
    m = re.match(r"(.+?)\s+" + re.escape(city or "Wrocław") + r",", title)
    index = street_index(city)
    if m and index and index.lookup(words(m.group(1))):
        street = f"ul. {m.group(1)}"
    return f"{street} . {plain_text(desc.group(1)) if desc else ''}"


def read_olx(page: str, city: str | None = None) -> str:
    data = json.loads(page)["data"]
    return f"{plain_text(data.get('title'))} . {plain_text(data.get('description'))}"


READERS = {"otodom": read_otodom, "morizon": read_morizon, "nieruchomosci": read_nieruchomosci, "olx": read_olx}


def fetch_page(row) -> str:
    """The page for a row; OLX answers only `curl` (see http.get_curl) and its JSON API has the full offer."""
    if row.site == "olx":
        url = f"{olx.API}/offers/{row.id}/"
        try:
            return http.get_curl(url)
        except subprocess.CalledProcessError as exc:
            # curl only says "failed": ask once for the real status, so a removed offer (404) is told from a block (403, 429)
            status = http.curl_status(url)
            if status:
                raise urllib.error.HTTPError(url, status, "curl failed", {}, None) from exc
            raise
    return http.get(row.url)


# ---------- cache ----------

def load_cache(path: str | Path = CACHE_PATH) -> Cache:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    cache: Cache = {}
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        if value is None:
            cache[key] = None
        elif isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and (value[1] is None or isinstance(value[1], str)):
            cache[key] = list(value)
    return cache


def save_cache(cache: Cache, path: str | Path = CACHE_PATH) -> None:
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


# ---------- the fill ----------

def fill_addresses(
    listings: dict, cache: Cache, skip=frozenset(), default_city: str | None = None,
    limit: int = DEFAULT_LIMIT, fetch=None, sleep=time.sleep,
) -> dict:
    """Give rows that still have no street the street (and house number) their own page names.
    `skip`: keys whose full text was already read from the list (OLX rows of this run). Updates `cache` in place."""
    fetch = fetch or fetch_page
    out = dict(listings)
    fetched = 0
    failures: Counter = Counter()  # per portal: one site's dead links must not starve the others
    for key in sorted(listings):
        row = listings[key]
        reader = READERS.get(row.site)
        if row.street or reader is None or key in skip:
            continue
        ck = f"{row.site}:{row.id}"
        if ck not in cache:
            if fetched >= limit or failures[row.site] >= MAX_FAILURES:
                continue
            if fetched + sum(failures.values()):
                sleep(DELAY_S)
            city = row.city or default_city
            try:
                found = find_address(reader(fetch(row), city), city)
            except urllib.error.HTTPError as exc:
                if exc.code in GONE:  # removed from the portal: nothing to read, and nothing to ask again
                    fetched += 1
                    cache[ck] = None
                else:
                    failures[row.site] += 1
                    log.warning("details: %s %s failed (HTTP %s)", row.site, row.id, exc.code)
                continue
            except Exception as exc:  # a changed page or a blocked request must not break the run
                failures[row.site] += 1
                log.warning("details: %s %s failed (%s: %s)", row.site, row.id, type(exc).__name__, str(exc)[:120])
                continue
            fetched += 1
            cache[ck] = [found.street, found.house] if found else None
        hit = cache[ck]
        if hit:
            out[key] = replace(row, street=hit[0], house=hit[1] if row.house is None else row.house)
    live = {f"{r.site}:{r.id}" for r in listings.values()}
    for ck in [k for k in cache if k not in live]:  # the listing is gone: forget it, the file stays small
        del cache[ck]
    waiting = sum(1 for k, r in listings.items() if not r.street and r.site in READERS and k not in skip and f"{r.site}:{r.id}" not in cache)
    log.info("details: %d page(s) read this run, %d failed%s, %d row(s) without a street still waiting", fetched, sum(failures.values()),
             f" ({dict(failures)})" if failures else "", waiting)
    return out


if __name__ == "__main__":
    # One-off backfill of the committed listings.csv: python -m aptsearch.details
    from .config import load_config
    from .storage import load_listings, save_listings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config("config.yaml")
    rows = load_listings("listings.csv")
    cache = load_cache()
    filled = fill_addresses(rows, cache, default_city=cfg.city, limit=10_000)
    save_cache(cache)
    save_listings(filled, "listings.csv")
    log.info("%d of %d rows now have a street", sum(1 for r in filled.values() if r.street), len(filled))
