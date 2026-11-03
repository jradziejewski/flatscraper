import json
from pathlib import Path

import pytest

from aptsearch.config import Config
from aptsearch.scrapers import otodom

FIXTURE = Path(__file__).parent / "fixtures" / "otodom_search_ads.json"


@pytest.fixture
def ads():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_items(ads):
    first, _, third, edge = otodom.parse_items(ads)
    assert first.site == "otodom" and first.id == "63164515"
    assert first.url == "https://www.otodom.pl/pl/oferta/mieszkanie-45m2-i-2-pok-i-okolice-ch-magnolia-i-garaz-ID4h1Yw"
    assert first.price_pln == 2900
    assert first.area_m2 == 45 and isinstance(first.area_m2, float)  # site sends an int
    assert first.rooms == 2
    assert first.district == "Fabryczna"
    assert first.city == "Wrocław"
    assert first.posted_at == "2022-06-05T21:01:01Z"
    assert third.area_m2 == 54.79 and third.district == "Krzyki"
    # missing values come through as None
    assert edge.price_pln is None and edge.rooms is None
    assert edge.area_m2 is None and edge.district is None


def test_extract_search_ads_from_html(ads):
    page = {"props": {"pageProps": {"data": {"searchAds": ads}}}}
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script></html>'
    assert otodom.extract_search_ads(html) == ads
    with pytest.raises(ValueError):
        otodom.extract_search_ads("<html></html>")


def test_build_url():
    url = otodom.build_url(Config(city="Wrocław", max_price=5000, min_area=30, min_rooms=2), 3)
    assert "/dolnoslaskie/wroclaw/wroclaw/wroclaw?" in url
    assert "priceMax=5000" in url and "areaMin=30" in url and "page=3" in url
    assert "roomsNumber=%5BTWO%2CTHREE%2CFOUR%2CFIVE%2CSIX_OR_MORE%5D" in url
    with pytest.raises(ValueError):
        otodom.build_url(Config(city="Atlantyda"), 1)


def test_scrape_stops_at_last_page(ads, monkeypatch):
    ads["pagination"]["totalPages"] = 2
    calls = []
    monkeypatch.setattr(otodom, "DELAY_S", 0)
    monkeypatch.setattr(otodom, "extract_search_ads", lambda html: ads)
    monkeypatch.setattr(otodom.http, "get", lambda url: calls.append(url) or "")
    result = otodom.scrape(Config(city="Wrocław"))
    assert len(calls) == 2 and len(result) == 8
