from pathlib import Path

import pytest

from aptsearch.config import Config
from aptsearch.scrapers import nieruchomosci as no

HTML = (Path(__file__).parent / "fixtures" / "nieruchomosci_search.html").read_text(encoding="utf-8")


def test_parse_page():
    span_tile, anchor_tile, no_hood, edge = no.parse_page(HTML)
    assert span_tile.site == "nieruchomosci"
    assert span_tile.id == "26872010"
    assert span_tile.url.endswith("/26872010.html")
    assert span_tile.title == "Apartament umeblowany Wrocław"
    assert span_tile.price_pln == 1900
    assert span_tile.area_m2 == 30.09
    assert span_tile.rooms == 1
    assert span_tile.city == "Wrocław"
    assert span_tile.district == "Jerzmanowo-Jarnołtów-Strachowice-Osiniec"
    assert span_tile.posted_at is None
    # the site renders the province as <a> links on most tiles and <span>s on others
    assert anchor_tile.id == "24703680" and anchor_tile.district == "Różanka"
    assert anchor_tile.street == "Obornicka" and span_tile.street is None  # streetAddress from ld+json, when present
    assert anchor_tile.price_pln == 3200 and anchor_tile.rooms == 2
    # province "Wrocław, dolnośląskie" means there is no neighbourhood
    assert no_hood.id == "27021854" and no_hood.district is None
    # non-PLN price and missing rooms become None; no tile falls back to the offer name
    assert edge.price_pln is None and edge.rooms is None and edge.title == "Edge case"


def test_photo_comes_from_the_offer_image():
    span_tile, anchor_tile, *_ = no.parse_page(HTML)
    assert span_tile.photo == "https://i.st-nieruchomosci-online.pl/ks3dz9l/mieszkanie-wroclaw.jpg"
    assert anchor_tile.photo == "https://i.st-nieruchomosci-online.pl/h198ssl/mieszkanie-wroclaw.jpg"
    offer = no.extract_offers(HTML)[0]
    for odd in (None, "", 5, "javascript:alert(1)"):
        assert no.parse_offer({**offer, "image": odd}, {}).photo is None
    assert no.parse_offer({k: v for k, v in offer.items() if k != "image"}, {}).photo is None


def test_extract_offers_requires_ld_json():
    assert len(no.extract_offers(HTML)) == 4
    with pytest.raises(ValueError):
        no.extract_offers("<html></html>")


def test_build_url():
    cfg = Config(city="Wrocław")
    assert no.build_url(cfg, 1) == f"{no.BASE}?3,mieszkanie,wynajem,,Wroc%C5%82aw"
    assert no.build_url(cfg, 3).endswith("Wroc%C5%82aw&p=3")


def test_scrape_stops_on_repeated_page(monkeypatch):
    monkeypatch.setattr(no, "DELAY_S", 0)
    calls = []
    monkeypatch.setattr(no.http, "get", lambda url: calls.append(url) or HTML)
    result = no.scrape(Config(city="Wrocław"))
    assert len(calls) == 2 and len(result) == 4  # page 2 repeats page 1 -> stop


def test_scrape_stops_on_empty_page(monkeypatch):
    monkeypatch.setattr(no, "DELAY_S", 0)
    calls = []
    monkeypatch.setattr(no.http, "get", lambda url: calls.append(url) or "")
    monkeypatch.setattr(no, "parse_page", lambda html: [])
    assert no.scrape(Config(city="Wrocław")) == [] and len(calls) == 1
