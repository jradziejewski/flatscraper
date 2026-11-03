import json
import logging
import time
from datetime import datetime
from urllib.parse import urlencode

from .. import http
from ..config import Config
from ..models import Listing
from ..storage import utc_iso
from ..text import city_slug, photo_url

log = logging.getLogger(__name__)

SITE = "olx"
API = "https://www.olx.pl/api/v1"
MAX_PAGES = 5
PAGE_SIZE = 40
DELAY_S = 2.0
ROOMS = ["one", "two", "three", "four"]  # "four" means 4 or more
THUMB_W, THUMB_H = 320, 240


def get_json(url: str) -> dict:
    return json.loads(http.get_curl(url))


def resolve_location(cfg: Config) -> dict:
    """Ask OLX which category/region/city ids belong to the configured city."""
    url = f"{API}/friendly-links/query-params/nieruchomosci,mieszkania,wynajem,{city_slug(cfg.city)}/"
    data = get_json(url).get("data") or {}
    if not data.get("city_id"):
        raise ValueError(f"olx: could not resolve city {cfg.city!r}")
    return {k: data[k] for k in ("category_id", "region_id", "city_id") if k in data}


def build_url(ids: dict, cfg: Config, offset: int) -> str:
    params = {**ids, "offset": offset, "limit": PAGE_SIZE, "sort_by": "created_at:desc"}
    if cfg.max_price is not None:
        params["filter_float_price:to"] = cfg.max_price
    if cfg.min_area is not None:
        params["filter_float_m:from"] = cfg.min_area
    if cfg.min_rooms is not None:
        for i, room in enumerate(ROOMS[max(cfg.min_rooms, 1) - 1 :]):
            params[f"filter_enum_rooms[{i}]"] = room
    return f"{API}/offers/?{urlencode(params)}"


def _photo(offer: dict) -> str | None:
    """OLX photo links carry {width}x{height} placeholders; ask for a thumbnail-sized image."""
    try:
        link = offer["photos"][0]["link"]
        return photo_url(link.replace("{width}", str(THUMB_W)).replace("{height}", str(THUMB_H)))
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def parse_offer(offer: dict) -> Listing:
    params = {p["key"]: p.get("value") or {} for p in offer.get("params") or []}
    price = params.get("price", {})
    area = params.get("m", {}).get("key")
    room = params.get("rooms", {}).get("key")
    loc = offer.get("location") or {}
    created = offer.get("created_time")
    return Listing(
        site=SITE,
        id=str(offer["id"]),
        url=offer["url"],
        title=offer.get("title") or "",
        price_pln=int(price["value"]) if price.get("currency") == "PLN" and price.get("value") is not None else None,
        area_m2=float(area) if area else None,
        rooms=ROOMS.index(room) + 1 if room in ROOMS else None,
        district=(loc.get("district") or {}).get("name"),
        photo=_photo(offer),
        city=(loc.get("city") or {}).get("name"),
        posted_at=utc_iso(datetime.fromisoformat(created)) if created else None,
    )


def parse_offers(offers: list[dict]) -> list[Listing]:
    out = []
    for offer in offers:
        try:
            out.append(parse_offer(offer))
        except (KeyError, TypeError, ValueError):
            log.warning("olx: skipping malformed offer %s", offer.get("id"))
    return out


def scrape(cfg: Config) -> list[Listing]:
    ids = resolve_location(cfg)
    seen: dict[str, Listing] = {}
    fetched = 0
    for page in range(MAX_PAGES):
        time.sleep(DELAY_S)  # also spaces the first call after the location lookup
        resp = get_json(build_url(ids, cfg, page * PAGE_SIZE))
        fetched += 1
        # promoted ads are mixed into every page, so ids repeat
        new = [l for l in parse_offers(resp.get("data") or []) if l.id not in seen]
        seen.update((l.id, l) for l in new)
        if not new or not (resp.get("links") or {}).get("next"):
            break
    log.info("olx: fetched %d listings from %d page(s)", len(seen), fetched)
    return list(seen.values())
