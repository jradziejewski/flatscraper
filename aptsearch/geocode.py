"""Street -> coordinates through OpenStreetMap's Nominatim, cached in geocache.json.

Only Otodom, Morizon and nieruchomosci need this (OLX sends its own coordinates, which `scrapers/olx.py` reads).
A street resolves to a point in the middle of the street, never a house number, so the row is marked geo="street".

Follows the Nominatim usage policy: at most one request per second, a User-Agent that identifies the app,
every result cached (including "not found"), and a hard cap on lookups per run.
"""
import json
import logging
import math
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

from . import http
from .models import Listing

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "flatscraper/0.1 (personal apartment search; +https://github.com/jradziejewski/flatscraper)"
DELAY_S = 1.1  # the policy allows 1 request per second
DEFAULT_LIMIT = 40  # lookups per run; the committed cache makes later runs need only a few
CACHE_PATH = "geocache.json"

# (lat_min, lat_max, lon_min, lon_max). A hit outside the city's box is a same-named street elsewhere: rejected.
POLAND = (48.0, 55.0, 13.0, 25.0)
CITY_BOXES = {"wrocław": (51.04, 51.21, 16.80, 17.18)}

Point = tuple[float, float]
Cache = dict[str, list[float] | None]


def clean_point(lat, lon, box=POLAND) -> Point | None:
    """(lat, lon) rounded to 6 decimals (about 10 cm, and a stable CSV), or None if it is not a real place inside `box`."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(la) and math.isfinite(lo)):
        return None
    if not (box[0] <= la <= box[1] and box[2] <= lo <= box[3]):
        return None
    return round(la, 6), round(lo, 6)


def cache_key(street: str, city: str) -> str:
    return f"{city.casefold()}|{street.casefold()}"


def load_cache(path: str | Path = CACHE_PATH) -> Cache:
    path = Path(path)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    cache: Cache = {}
    for key, value in (raw.items() if isinstance(raw, dict) else []):
        if value is None:
            cache[key] = None
        elif isinstance(value, list) and len(value) == 2 and clean_point(*value):
            cache[key] = list(clean_point(*value))
    return cache


def save_cache(cache: Cache, path: str | Path = CACHE_PATH) -> None:
    text = json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def lookup(street: str, city: str, fetch=None) -> Point | None:
    """Ask Nominatim for one street. None means "not found in the city"; network errors are raised."""
    fetch = fetch or http.get  # looked up at call time, so tests can replace http.get
    query = urlencode({"q": f"{street}, {city}", "format": "jsonv2", "limit": 1, "countrycodes": "pl"})
    hits = json.loads(fetch(f"{NOMINATIM}?{query}", user_agent=USER_AGENT))
    for hit in hits[:1]:
        point = clean_point(hit.get("lat"), hit.get("lon"), CITY_BOXES[city.casefold()])
        if point:
            return point
    return None


def fill_street_coordinates(
    listings: dict, cache: Cache, default_city: str | None = None,
    limit: int = DEFAULT_LIMIT, fetch=None, sleep=time.sleep,
) -> dict:
    """Give rows that have a street but no coordinates the cached (or freshly looked-up) street point.
    Updates `cache` in place and returns a new dict. A failed request stops this run's lookups and is not cached."""
    out = dict(listings)
    looked_up = 0
    stopped = False
    skipped_cities: set[str] = set()
    for key in sorted(listings):
        row: Listing = listings[key]
        if row.lat is not None or not row.street:
            continue
        city = row.city or default_city
        if not city or city.casefold() not in CITY_BOXES:
            skipped_cities.add(city or "(none)")
            continue
        ck = cache_key(row.street, city)
        if ck not in cache:
            if stopped or looked_up >= limit:
                continue
            if looked_up:
                sleep(DELAY_S)
            try:
                point = lookup(row.street, city, fetch)
            except Exception as exc:  # geocoding is a nicety: never let it break a run
                log.warning("geocode: stopping after a failed lookup (%s: %s)", type(exc).__name__, exc)
                stopped = True
                continue
            looked_up += 1
            cache[ck] = list(point) if point else None
        hit = cache[ck]
        if hit:
            out[key] = replace(row, lat=hit[0], lon=hit[1], geo="street")
    if skipped_cities:
        log.info("geocode: no city box for %s, skipped (add it to CITY_BOXES)", sorted(skipped_cities))
    pending = sum(
        1 for r in out.values()
        if r.lat is None and r.street and (r.city or default_city or "").casefold() in CITY_BOXES
        and cache_key(r.street, r.city or default_city) not in cache
    )
    log.info("geocode: %d lookup(s) this run, %d street(s) still waiting", looked_up, pending)
    return out


if __name__ == "__main__":
    # One-off backfill of the committed listings.csv: python -m aptsearch.geocode
    from .config import load_config
    from .storage import load_listings, save_listings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config("config.yaml")
    rows = load_listings("listings.csv")
    cache = load_cache()
    filled = fill_street_coordinates(rows, cache, default_city=cfg.city, limit=10_000)
    save_cache(cache)
    save_listings(filled, "listings.csv")
    log.info("%d of %d rows now have coordinates", sum(1 for r in filled.values() if r.lat is not None), len(filled))
