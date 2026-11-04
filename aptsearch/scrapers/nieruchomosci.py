import html as htmllib
import json
import logging
import re
import time
from urllib.parse import quote

from .. import http
from ..config import Config
from ..models import Listing
from ..address import resolve
from ..text import clean_street, photo_url

log = logging.getLogger(__name__)

SITE = "nieruchomosci"
BASE = "https://www.nieruchomosci-online.pl/szukaj.html"
MAX_PAGES = 5
DELAY_S = 2.0

_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_TILE = re.compile(
    r'<h2 class=" name[^>]*><a href="([^"]+)"[^>]*>(.*?)</a></h2>\s*'
    r'<p class="province[^>]*>(.*?)</p>',
    re.S,
)
# Province parts are <span>s on some tiles and <a> links on others.
_PART = re.compile(r"<(?:span|a)\b[^>]*>(.*?)</(?:span|a)>", re.S)
_ID = re.compile(r"/(\d+)\.html")


def build_url(cfg: Config, page: int) -> str:
    url = f"{BASE}?3,mieszkanie,wynajem,,{quote(cfg.city)}"
    return url if page <= 1 else f"{url}&p={page}"


def extract_offers(html: str) -> list[dict]:
    for m in _LD_JSON.finditer(html):
        data = json.loads(m.group(1))
        if data.get("@type") == "CollectionPage":
            return data["mainEntity"]["offers"][0].get("offers") or []
    raise ValueError("nieruchomosci: CollectionPage ld+json not found")


def extract_tiles(html: str) -> dict[str, tuple[str, list[str]]]:
    """Map listing URL -> (title, province parts, e.g. ['Różanka', 'Wrocław'])."""
    tiles = {}
    for url, title, province in _TILE.findall(html):
        spans = [htmllib.unescape(s).strip().rstrip(",").strip() for s in _PART.findall(province)]
        tiles[htmllib.unescape(url)] = (htmllib.unescape(title).strip(), spans)
    return tiles


def parse_offer(offer: dict, tiles: dict) -> Listing:
    url = offer["url"]
    item = offer.get("itemOffered") or {}
    city = (item.get("address") or {}).get("addressLocality") or None
    title, spans = tiles.get(url, (offer.get("name") or "", []))
    # First span is the neighbourhood, unless it is just the city itself.
    district = spans[0] if spans and spans[0].casefold() != (city or "").casefold() else None
    price = offer.get("price")
    area = (item.get("floorSize") or {}).get("value")
    street, house = resolve(clean_street((item.get("address") or {}).get("streetAddress")), title, city)
    return Listing(
        site=SITE,
        id=_ID.search(url).group(1),
        url=url,
        title=title,
        price_pln=int(float(price)) if price and offer.get("priceCurrency") == "PLN" else None,
        area_m2=float(area) if area else None,
        rooms=item.get("numberOfRooms") or None,
        district=district,
        street=street,
        house=house,
        photo=photo_url(offer.get("image")),
        city=city,
    )


def parse_page(html: str) -> list[Listing]:
    tiles = extract_tiles(html)
    out = []
    for offer in extract_offers(html):
        try:
            out.append(parse_offer(offer, tiles))
        except (KeyError, AttributeError, TypeError, ValueError):
            log.warning("nieruchomosci: skipping malformed offer %s", offer.get("url"))
    return out


def scrape(cfg: Config) -> list[Listing]:
    seen: dict[str, Listing] = {}
    fetched = 0
    for page in range(1, MAX_PAGES + 1):
        found = parse_page(http.get(build_url(cfg, page)))
        fetched += 1
        new = [l for l in found if l.id not in seen]
        seen.update((l.id, l) for l in new)
        if not new:  # empty page or nothing but repeats: past the last page
            break
        if page < MAX_PAGES:
            time.sleep(DELAY_S)
    log.info("nieruchomosci: fetched %d listings from %d page(s)", len(seen), fetched)
    return list(seen.values())
