import html
import json
import logging
import re
import time
from urllib.parse import urlencode

from .. import http
from ..config import Config
from ..models import Listing
from ..text import city_slug, clean_street, photo_url

log = logging.getLogger(__name__)

SITE = "morizon"
BASE = "https://www.morizon.pl"
IMG_BASE = "https://img1.staticmorizon.com.pl"
MAX_PAGES = 5
PAGE_SIZE = 35
DELAY_S = 2.0

_NUXT_DATA = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef"}
_LEADING_INT = re.compile(r"\d+")


def build_url(cfg: Config, page: int) -> str:
    params = {}
    if cfg.max_price is not None:
        params["ps[price_to]"] = cfg.max_price
    if cfg.min_area is not None:
        params["ps[living_area_from]"] = cfg.min_area
    if cfg.min_rooms is not None:
        params["ps[number_of_rooms_from]"] = cfg.min_rooms
    if page > 1:
        params["page"] = page
    url = f"{BASE}/do-wynajecia/mieszkania/najnowsze/{city_slug(cfg.city)}/"
    return f"{url}?{urlencode(params)}" if params else url


def unflatten(arr: list):
    """Decode Nuxt's flat payload: every container child is an index into `arr`."""
    cache: dict[int, object] = {}

    def resolve(i: int):
        if i < 0:  # devalue specials (undefined, NaN, ±Infinity, -0)
            return None
        if i in cache:
            return cache[i]
        v = arr[i]
        if isinstance(v, dict):
            out: object = {}
            cache[i] = out  # register first so shared references resolve to one object
            out.update({k: resolve(x) for k, x in v.items()})
        elif isinstance(v, list):
            if v and isinstance(v[0], str):  # tagged value, e.g. ["Reactive", 5]
                tag = v[0]
                if tag in _WRAPPERS or tag == "Date":
                    out = resolve(v[1])
                elif tag == "Set":
                    out = [resolve(x) for x in v[1:]]
                else:  # EmptyRef, Map, ... : nothing we need
                    out = None
            else:
                out = [resolve(x) for x in v]
            cache[i] = out
        else:
            out = cache[i] = v
        return out

    return resolve(0)


def extract_properties(html: str) -> dict:
    m = _NUXT_DATA.search(html)
    if not m:
        raise ValueError("morizon: __NUXT_DATA__ not found")
    data = (unflatten(json.loads(m.group(1))) or {}).get("data") or {}
    for key, value in data.items():
        if key.startswith("property-listing-data"):
            try:
                return value["data"]["searchResult"]["properties"]
            except (KeyError, TypeError):
                break
    raise ValueError("morizon: listing data not found in __NUXT_DATA__")


def _photo(node: dict) -> str | None:
    """Morizon lists photo ids, not URLs; this is the thumbnail URL its own pages use (450x300)."""
    try:
        first = node["photos"][0]
        return photo_url(f"{IMG_BASE}/thumb/{first['id']}/3x2_s:fill_and_crop/{first['name']}.jpg")
    except (KeyError, IndexError, TypeError):
        return None


def parse_node(node: dict) -> Listing:
    price = node.get("price") or {}
    area = node.get("area")
    rooms = _LEADING_INT.match(node.get("numberOfRooms") or "")
    where = (node.get("location") or {}).get("location") or []
    return Listing(
        site=SITE,
        id=str(node["idOnFrontend"]),
        url=BASE + node["url"],
        title=html.unescape(node.get("advertisementText") or node.get("title") or ""),  # site sends &amp;
        price_pln=int(float(price["amount"])) if price.get("currency") == "PLN" and price.get("amount") else None,
        area_m2=float(area) if area else None,
        rooms=int(rooms.group()) if rooms else None,
        district=where[2] if len(where) > 2 else None,
        street=clean_street((node.get("location") or {}).get("street")),
        photo=_photo(node),
        city=where[1] if len(where) > 1 else None,
        posted_at=node.get("addedAt"),
    )


def parse_nodes(nodes: list[dict]) -> list[Listing]:
    out = []
    for node in nodes:
        try:
            out.append(parse_node(node))
        except (KeyError, TypeError, ValueError):
            log.warning("morizon: skipping malformed node %s", node.get("idOnFrontend"))
    return out


def scrape(cfg: Config) -> list[Listing]:
    seen: dict[str, Listing] = {}
    fetched = 0
    for page in range(1, MAX_PAGES + 1):
        props = extract_properties(http.get(build_url(cfg, page)))
        fetched += 1
        nodes = props.get("nodes") or []
        seen.update((l.id, l) for l in parse_nodes(nodes))
        if not nodes or page * PAGE_SIZE >= (props.get("totalCount") or 0):
            break
        if page < MAX_PAGES:
            time.sleep(DELAY_S)
    log.info("morizon: fetched %d listings from %d page(s)", len(seen), fetched)
    return list(seen.values())
