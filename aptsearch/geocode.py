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
from .models import Listing, geo_rank
from .streets import distinctive, stem, words

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


def cache_key(street: str, city: str, house: str | None = None) -> str:
    """"wrocław|hubska" for a street, "wrocław|hubska|12" for one building."""
    key = f"{city.casefold()}|{street.casefold()}"
    return f"{key}|{house.casefold()}" if house else key


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


def lookup(street: str, city: str, fetch=None, house: str | None = None) -> Point | None:
    """Ask Nominatim for a street, or with `house` for one building. None means "not found in the city"; network errors are raised.

    A building counts only if the answer really carries the house number asked for, on a road with the street's name:
    otherwise Nominatim quietly answers with the street itself, and that must not be passed off as a building."""
    fetch = fetch or http.get  # looked up at call time, so tests can replace http.get
    params = {"q": f"{street} {house}, {city}" if house else f"{street}, {city}", "format": "jsonv2", "limit": 1, "countrycodes": "pl"}
    if house:
        params["addressdetails"] = 1
    hits = json.loads(fetch(f"{NOMINATIM}?{urlencode(params)}", user_agent=USER_AGENT))
    for hit in hits[:1]:
        point = clean_point(hit.get("lat"), hit.get("lon"), CITY_BOXES[city.casefold()])
        if point and (not house or _is_that_building(hit.get("address") or {}, street, house)):
            return point
    return None


def _is_that_building(address: dict, street: str, house: str) -> bool:
    got = str(address.get("house_number") or "").replace(" ", "").casefold()
    road = address.get("road") or address.get("pedestrian") or address.get("footway") or ""
    street_words = [w for w in words(street) if distinctive(w)]
    road_words = [w for w in words(road) if distinctive(w)]
    same_road = bool(street_words and road_words) and stem(street_words[-1]) == stem(road_words[-1])
    return got == house.replace(" ", "").casefold() and same_road


def fill_street_coordinates(
    listings: dict, cache: Cache, default_city: str | None = None,
    limit: int = DEFAULT_LIMIT, fetch=None, sleep=time.sleep,
) -> dict:
    """Locate rows that have a street: a row with a house number gets its building (geo="address", better than anything else we
    know), a row with only a street and no location gets the street's point (geo="street").
    Updates `cache` in place and returns a new dict. A failed request stops this run's lookups and is not cached."""
    out = dict(listings)
    state = {"looked_up": 0, "stopped": False}
    skipped_cities: set[str] = set()

    def ask(street: str, city: str, house: str | None = None) -> list[float] | None:
        """The cached point (or None for "not found"); asks Nominatim only if it is unknown, there is budget and nothing failed yet."""
        ck = cache_key(street, city, house)
        if ck in cache:
            return cache[ck]
        if state["stopped"] or state["looked_up"] >= limit:
            return None
        if state["looked_up"]:
            sleep(DELAY_S)
        try:
            point = lookup(street, city, fetch, house)
        except Exception as exc:  # geocoding is a nicety: never let it break a run
            log.warning("geocode: stopping after a failed lookup (%s: %s)", type(exc).__name__, exc)
            state["stopped"] = True
            return None
        state["looked_up"] += 1
        cache[ck] = list(point) if point else None
        return cache[ck]

    for key in sorted(listings):
        row: Listing = listings[key]
        if not row.street:
            continue
        city = row.city or default_city
        if not city or city.casefold() not in CITY_BOXES:
            skipped_cities.add(city or "(none)")
            continue
        if row.house and geo_rank(row.geo) < geo_rank("address"):
            hit = ask(row.street, city, row.house)
            if hit:
                out[key] = replace(row, lat=hit[0], lon=hit[1], geo="address")
                continue
        if row.lat is None:  # no building (no number, or none found): the street's point is better than nothing
            hit = ask(row.street, city)
            if hit:
                out[key] = replace(row, lat=hit[0], lon=hit[1], geo="street")
    if skipped_cities:
        log.info("geocode: no city box for %s, skipped (add it to CITY_BOXES)", sorted(skipped_cities))
    waiting = set()  # distinct streets and buildings (many flats share one), not rows
    for r in out.values():
        city = r.city or default_city
        if not r.street or (city or "").casefold() not in CITY_BOXES:
            continue
        if r.house and geo_rank(r.geo) < geo_rank("address") and cache_key(r.street, city, r.house) not in cache:
            waiting.add(cache_key(r.street, city, r.house))
        if r.lat is None and cache_key(r.street, city) not in cache:
            waiting.add(cache_key(r.street, city))
    log.info("geocode: %d lookup(s) this run, %d street(s)/address(es) still waiting", state["looked_up"], len(waiting))
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
