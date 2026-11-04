import json
import logging
import re
import time
from urllib.parse import urlencode

from .. import http
from ..config import Config
from ..models import Listing
from ..address import resolve
from ..text import clean_street, photo_url

log = logging.getLogger(__name__)

SITE = "otodom"
BASE = "https://www.otodom.pl"
MAX_PAGES = 5
PAGE_SIZE = 36
DELAY_S = 2.0

# Otodom needs the full location path in the URL; extend as cities are needed.
CITY_PATHS = {
    "wrocław": "dolnoslaskie/wroclaw/wroclaw/wroclaw",
}

ROOMS = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX_OR_MORE"]
ROOM_VALUES = {name: i + 1 for i, name in enumerate(ROOMS)}
ROOM_VALUES.update({"SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10})

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def build_url(cfg: Config, page: int) -> str:
    path = CITY_PATHS.get(cfg.city.casefold())
    if path is None:
        raise ValueError(f"otodom: no location path for city {cfg.city!r}")
    params = {"limit": PAGE_SIZE, "page": page, "by": "LATEST", "direction": "DESC"}
    if cfg.max_price is not None:
        params["priceMax"] = cfg.max_price
    if cfg.min_area is not None:
        params["areaMin"] = cfg.min_area
    if cfg.min_rooms is not None:
        wanted = ROOMS[max(cfg.min_rooms, 1) - 1 :]
        params["roomsNumber"] = "[" + ",".join(wanted) + "]"
    return f"{BASE}/pl/wyniki/wynajem/mieszkanie/{path}?{urlencode(params)}"


def extract_search_ads(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    if not m:
        raise ValueError("otodom: __NEXT_DATA__ not found")
    return json.loads(m.group(1))["props"]["pageProps"]["data"]["searchAds"]


def _location(item: dict, level: str) -> str | None:
    locs = ((item.get("location") or {}).get("reverseGeocoding") or {}).get("locations") or []
    for loc in locs:
        if loc.get("locationLevel") == level:
            return loc.get("name")
    return None


def _photo(item: dict) -> str | None:
    try:
        return photo_url(item["images"][0]["small"])
    except (KeyError, IndexError, TypeError):
        return None


def parse_item(item: dict) -> Listing:
    price = item.get("totalPrice") or {}
    area = item.get("areaInSquareMeters")
    city = _location(item, "city_or_village")
    title = item.get("title") or ""
    # the list has no description, but the title sometimes holds the address ("Buforowa 89 - mieszkania na wynajem")
    street, house = resolve(clean_street((((item.get("location") or {}).get("address") or {}).get("street") or {}).get("name")), title, city)
    return Listing(
        site=SITE,
        id=str(item["id"]),
        url=f"{BASE}/pl/oferta/{item['slug']}",
        title=title,
        price_pln=int(price["value"]) if price.get("currency") == "PLN" and price.get("value") is not None else None,
        area_m2=float(area) if area is not None else None,
        rooms=ROOM_VALUES.get(item.get("roomsNumber")),
        district=_location(item, "district"),
        street=street,
        house=house,
        photo=_photo(item),
        city=city,
        posted_at=item.get("dateCreated"),
    )


def parse_items(search_ads: dict) -> list[Listing]:
    out = []
    for item in search_ads.get("items") or []:
        try:
            out.append(parse_item(item))
        except (KeyError, TypeError, ValueError):
            log.warning("otodom: skipping malformed item %s", item.get("id"))
    return out


def scrape(cfg: Config) -> list[Listing]:
    listings: list[Listing] = []
    fetched = 0
    for page in range(1, MAX_PAGES + 1):
        ads = extract_search_ads(http.get(build_url(cfg, page)))
        fetched += 1
        listings.extend(parse_items(ads))
        if page >= (ads.get("pagination") or {}).get("totalPages", 1):
            break
        time.sleep(DELAY_S)
    log.info("otodom: fetched %d listings from %d page(s)", len(listings), fetched)
    return listings
