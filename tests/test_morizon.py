import json
from pathlib import Path

import pytest

from aptsearch.config import Config
from aptsearch.scrapers import morizon as mz

NODES = json.loads((Path(__file__).parent / "fixtures" / "morizon_nodes.json").read_text(encoding="utf-8"))


def flatten(obj) -> list:
    """Encode `obj` the way Nuxt does: every child is an index into one flat array."""
    arr = [None]

    def add(v) -> int:
        i = len(arr)
        arr.append(None)
        if isinstance(v, dict):
            arr[i] = {k: add(x) for k, x in v.items()}
        elif isinstance(v, list):
            arr[i] = [add(x) for x in v]
        else:
            arr[i] = v
        return i

    arr[0] = ["ShallowReactive", add(obj)]
    return arr


def page_with(obj) -> str:
    return f'<script type="application/json" id="__NUXT_DATA__">{json.dumps(flatten(obj))}</script>'


def test_unflatten_handles_wrappers_shared_refs_and_specials():
    arr = [
        ["ShallowReactive", 1],
        {"a": 2, "b": 3, "c": 4, "d": 5, "e": 6, "f": -1, "g": 8},
        "shared",
        ["Reactive", 2],          # wrapper around the same string
        [2, 2],                   # plain list reusing one entry
        ["Set", 2, 9],
        ["EmptyRef", 10],
        ["Date", 11],
        9,
        "x",
        0,
        "2026-11-03",
    ]
    out = mz.unflatten(arr)
    assert out == {"a": "shared", "b": "shared", "c": ["shared", "shared"],
                   "d": ["shared", "x"], "e": None, "f": None, "g": 9}
    arr2 = [["ShallowReactive", 1], {"when": 2}, ["Date", 3], "2026-11-03"]
    assert mz.unflatten(arr2) == {"when": "2026-11-03"}


def test_extract_properties_round_trip():
    props = {"nodes": NODES, "totalCount": 758}
    html = page_with({"data": {"decode-x": {"data": 1}, "property-listing-data-/a-offers": {"data": {"searchResult": {"properties": props}}}}})
    assert mz.extract_properties(html) == props
    with pytest.raises(ValueError):
        mz.extract_properties("<html></html>")
    with pytest.raises(ValueError):
        mz.extract_properties(page_with({"data": {"other": {}}}))


def test_parse_nodes():
    first, second, hood, edge = mz.parse_nodes(NODES)
    assert first.site == "morizon"
    assert first.id == NODES[0]["idOnFrontend"]
    assert first.url == mz.BASE + NODES[0]["url"] and first.url.startswith("https://www.morizon.pl/oferta/")
    assert first.title == NODES[0]["advertisementText"] != NODES[0]["title"]
    assert first.price_pln == 3800
    assert first.area_m2 == 76.0 and isinstance(first.area_m2, float)  # site sends "76"
    assert first.rooms == 4
    assert first.district == "Krzyki" and first.city == "Wrocław"
    assert first.posted_at == "2026-11-03"
    assert second.rooms == 2 and second.price_pln == 3000
    # the 4th location element (neighbourhood) is ignored; district is the 3rd
    assert hood.district == NODES[2]["location"]["location"][2]
    # no district, non-PLN price and unparseable rooms all become None
    assert edge.district is None and edge.price_pln is None and edge.rooms is None
    assert edge.city == "Wrocław" and edge.title == "Edge case"


def test_parse_node_unescapes_title():
    node = {**NODES[0], "advertisementText": "Spa &amp; Gym | Pierwszy najem"}
    assert mz.parse_node(node).title == "Spa & Gym | Pierwszy najem"


def test_parse_nodes_skips_malformed():
    assert len(mz.parse_nodes(NODES + [{"idOnFrontend": "x"}])) == 4  # no url -> skipped


def test_city_slug_and_build_url():
    assert mz.city_slug("Wrocław") == "wroclaw"
    assert mz.city_slug("Zielona Góra") == "zielona-gora"
    url = mz.build_url(Config(city="Wrocław", max_price=5000, min_area=30, min_rooms=2), 1)
    assert url.startswith("https://www.morizon.pl/do-wynajecia/mieszkania/najnowsze/wroclaw/?")
    assert "ps%5Bprice_to%5D=5000" in url and "ps%5Bliving_area_from%5D=30" in url
    assert "ps%5Bnumber_of_rooms_from%5D=2" in url and "page=" not in url
    assert "page=3" in mz.build_url(Config(city="Wrocław"), 3)
    assert mz.build_url(Config(city="Wrocław"), 1).endswith("/wroclaw/")  # no params when no filters


def test_scrape_stops_at_total_count(monkeypatch):
    monkeypatch.setattr(mz, "DELAY_S", 0)
    calls = []
    monkeypatch.setattr(mz.http, "get", lambda url: calls.append(url) or "")
    monkeypatch.setattr(mz, "extract_properties", lambda html: {"nodes": NODES, "totalCount": 40})
    result = mz.scrape(Config(city="Wrocław"))
    assert len(calls) == 2 and len(result) == 4  # 2 * 35 >= 40; page 2 repeats page 1 ids


def test_scrape_stops_on_empty_page(monkeypatch):
    calls = []
    monkeypatch.setattr(mz.http, "get", lambda url: calls.append(url) or "")
    monkeypatch.setattr(mz, "extract_properties", lambda html: {"nodes": [], "totalCount": 999})
    assert mz.scrape(Config(city="Wrocław")) == [] and len(calls) == 1
