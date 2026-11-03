import json
import subprocess
from pathlib import Path

import pytest

from aptsearch import http
from aptsearch.config import Config
from aptsearch.scrapers import olx

PAGE = json.loads((Path(__file__).parent / "fixtures" / "olx_offers.json").read_text(encoding="utf-8"))
OFFERS = PAGE["data"]
IDS = {"category_id": 15, "region_id": 3, "city_id": 19701}


def test_parse_offers():
    first, _, _, edge = olx.parse_offers(OFFERS)
    raw = OFFERS[0]
    params = {p["key"]: p["value"] for p in raw["params"]}
    assert first.site == "olx"
    assert first.id == str(raw["id"]) and isinstance(first.id, str)
    assert first.url == raw["url"] and first.title == raw["title"]
    assert first.price_pln == params["price"]["value"]
    assert first.area_m2 == float(params["m"]["key"]) and isinstance(first.area_m2, float)  # API sends "60"
    assert first.rooms == olx.ROOMS.index(params["rooms"]["key"]) + 1
    assert first.district == raw["location"]["district"]["name"] and first.city == "Wrocław"
    assert first.street is None  # the OLX API has no street field
    assert first.posted_at.endswith("Z") and len(first.posted_at) == 20
    # no district, non-PLN price and missing area/rooms all become None
    assert edge.id == "1" and edge.title == "Edge case"
    assert edge.district is None and edge.price_pln is None
    assert edge.area_m2 is None and edge.rooms is None


def test_photo_placeholders_are_replaced_with_a_thumbnail_size():
    first, *_, edge = olx.parse_offers(OFFERS)
    link = OFFERS[0]["photos"][0]["link"]
    assert "{width}x{height}" in link  # what the API sends
    assert first.photo == link.replace("{width}x{height}", "320x240") and "{" not in first.photo
    assert edge.photo is None  # no photos: the listing is still returned
    for odd in ([], [{}], [{"link": None}], [{"link": "ftp://x"}], None):
        assert olx.parse_offer({**OFFERS[0], "photos": odd}).photo is None


def test_posted_at_is_converted_to_utc():
    offer = {**OFFERS[0], "created_time": "2026-10-23T14:34:54+02:00"}
    assert olx.parse_offer(offer).posted_at == "2026-10-23T12:34:54Z"


def test_parse_offers_skips_malformed():
    assert len(olx.parse_offers(OFFERS + [{"id": 5}])) == 4  # no url -> skipped


def test_build_url():
    url = olx.build_url(IDS, Config(city="Wrocław", max_price=5000, min_area=30, min_rooms=2), 40)
    assert url.startswith("https://www.olx.pl/api/v1/offers/?")
    assert "city_id=19701" in url and "offset=40" in url and "sort_by=created_at%3Adesc" in url
    assert "filter_float_price%3Ato=5000" in url and "filter_float_m%3Afrom=30" in url
    assert "filter_enum_rooms%5B0%5D=two" in url and "filter_enum_rooms%5B2%5D=four" in url
    assert "=one" not in url
    bare = olx.build_url(IDS, Config(city="Wrocław"), 0)
    assert "filter_" not in bare


def test_resolve_location(monkeypatch):
    seen = []
    resp = {"data": {**IDS}}
    monkeypatch.setattr(olx, "get_json", lambda url: seen.append(url) or resp)
    assert olx.resolve_location(Config(city="Wrocław")) == IDS
    assert seen[0].endswith("/nieruchomosci,mieszkania,wynajem,wroclaw/")
    monkeypatch.setattr(olx, "get_json", lambda url: {"data": {"category_id": 15}})
    with pytest.raises(ValueError):
        olx.resolve_location(Config(city="Atlantyda"))


def fake_api(monkeypatch, pages):
    calls = []
    monkeypatch.setattr(olx, "DELAY_S", 0)
    monkeypatch.setattr(olx, "resolve_location", lambda cfg: IDS)

    def get_json(url):
        calls.append(url)
        return pages[min(len(calls), len(pages)) - 1]

    monkeypatch.setattr(olx, "get_json", get_json)
    return calls


def test_scrape_follows_next_and_dedupes_promoted(monkeypatch):
    page2 = {"data": OFFERS[:2] + [{**OFFERS[0], "id": 777, "url": "https://www.olx.pl/d/oferta/x-ID777.html"}], "links": {"next": {"href": "x"}}}
    calls = fake_api(monkeypatch, [PAGE, page2, {"data": OFFERS, "links": {}}])
    result = olx.scrape(Config(city="Wrocław"))
    assert len(calls) == 3 and sorted(l.id for l in result).count("777") == 1
    assert len(result) == 5  # 4 from page 1 + id 777; repeats dropped


def test_scrape_stops_without_next_link(monkeypatch):
    calls = fake_api(monkeypatch, [{"data": OFFERS, "links": {}}])
    assert len(olx.scrape(Config(city="Wrocław"))) == 4 and len(calls) == 1


def test_scrape_stops_on_repeat_only_page(monkeypatch):
    calls = fake_api(monkeypatch, [PAGE])  # every page returns the same offers and a next link
    assert len(olx.scrape(Config(city="Wrocław"))) == 4 and len(calls) == 2


def test_get_curl_runs_curl_with_user_agent(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured.update(cmd=cmd, kw=kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="zażółć".encode(), stderr=b"")

    monkeypatch.setattr(http.subprocess, "run", fake_run)
    assert http.get_curl("https://example.test/x") == "zażółć"
    assert captured["cmd"][0] == "curl" and "--fail" in captured["cmd"]
    assert http.USER_AGENT in captured["cmd"] and captured["cmd"][-1] == "https://example.test/x"
    assert captured["kw"]["check"] is True
