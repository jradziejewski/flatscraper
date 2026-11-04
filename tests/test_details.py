import json
import logging
from pathlib import Path

import pytest

from aptsearch import details
from aptsearch.details import fill_addresses, load_cache, read_morizon, read_nieruchomosci, read_olx, read_otodom, save_cache
from aptsearch.address import find_address
from aptsearch.models import Listing

FIXTURES = Path(__file__).parent / "fixtures"


def flatten(obj) -> list:
    """Encode `obj` the way Nuxt does (as in test_morizon): every child is an index into one flat array."""
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


def otodom_page(ad: dict) -> str:
    page = {"props": {"pageProps": {"ad": ad}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script></html>'


def morizon_page(data: dict) -> str:
    return f'<script type="application/json" id="__NUXT_DATA__">{json.dumps(flatten({"data": data}))}</script>'


def row(site="otodom", id="1", **kw):
    return Listing(site=site, id=id, url=f"https://{site}.test/{id}", title="t", city="Wrocław", **kw)


def rows(*listings):
    return {(l.site, l.id): l for l in listings}


# ---------- readers: only the listing's own text ----------

def test_otodom_reads_title_and_description_but_never_the_agency_address():
    page = otodom_page({
        "title": "3 POKOJE | PO REMONCIE",
        "description": "<p>Oferujemy mieszkanie przy ul. Legnickiej 12 we Wrocławiu.</p>",
        "owner": {"address": "ul. Jaworska 11, 53-612, Wrocław"},      # the agency's office, as on the real pages
        "agency": {"address": "ul. Jaworska 11, 53-612, Wrocław"},
    })
    text = read_otodom(page)
    assert find_address(text) == ("Legnicka", "12") and "Jaworska" not in text
    only_agency = otodom_page({"title": "Port Popowice", "description": "<p>Nowa inwestycja</p>", "owner": {"address": "ul. Jaworska 11"}})
    assert find_address(read_otodom(only_agency)) is None                # a flat is not on its agency's street
    with pytest.raises(ValueError):
        read_otodom("<html></html>")


def test_morizon_reads_the_description_and_the_page_title_but_not_the_rest_of_the_page():
    data = {"decode-1": {"data": {
        "propertyData": {"description": "<p>Słoneczne mieszkanie na 3. piętrze w kamienicy.</p>"},
        "headTags": {"title": "Mieszkanie na wynajem 58 m² (3 pok.) | Wrocław, ul. Łubinowa/Parafialna"},
        "contactFormConsents": [{"consentContent": "Administrator: Morizon, ul. Wojska Polskiego 47, Poznań"}],   # the portal's own address
        "environment": {"urbanGreenery": [{"name": "Zieleniec przy ul. Świętej Trójcy"}]},                       # a park nearby
    }}}
    text = read_morizon(morizon_page(data))
    assert find_address(text) == ("Łubinowa", None)            # the first street of "Łubinowa/Parafialna"
    assert "Wojska" not in text and "Trójcy" not in text
    body = {"decode-1": {"data": {"propertyData": {"description": "<p>przy ul. Hubskiej 12, blisko centrum</p>"}, "headTags": {"title": "Mieszkanie | Wrocław"}}}}
    assert find_address(read_morizon(morizon_page(body))) == ("Hubska", "12")
    with pytest.raises(ValueError):
        read_morizon("<html></html>")


def test_nieruchomosci_page_with_three_traps_gives_no_street():
    page = (FIXTURES / "nieruchomosci_detail.html").read_text(encoding="utf-8")   # a real page, trimmed
    text = read_nieruchomosci(page, "Wrocław")
    # 1) the title "Boczna Karmelkowej Wrocław, Klecina" is a side street off Karmelkowa: no street of that name exists, so it is not guessed
    # 2) the description names a school "przy ul. Racławickiej": a landmark, not the flat's street
    # 3) the agent's office address (ld+json "Legnicka 55A/10", "Wyszyńskiego 1") is not on the listing's text at all
    assert "Racławickiej" in text and "Wyszyńskiego" not in text and "Legnicka" not in text
    assert find_address(text) is None


def test_nieruchomosci_title_that_is_the_address_line_is_read_as_a_street():
    page = (FIXTURES / "nieruchomosci_detail.html").read_text(encoding="utf-8")
    addressed = page.replace("Boczna Karmelkowej Wrocław, Klecina", "Hubska Wrocław, Krzyki")
    assert find_address(read_nieruchomosci(addressed, "Wrocław")) == ("Hubska", None)
    prose = page.replace("Boczna Karmelkowej Wrocław, Klecina", "Do wynajęcia mieszkanie 3-pokojowe na Gądowie Wrocław")
    assert find_address(read_nieruchomosci(prose, "Wrocław")) is None       # a seller's headline is not an address
    no_title = page.replace("<title>Boczna Karmelkowej Wrocław, Klecina</title>", "")
    assert find_address(read_nieruchomosci(no_title, "Wrocław")) is None    # and a page without a title does not crash the reader


def test_olx_reads_the_single_offer_answer():
    page = json.dumps({"data": {"title": "Kawalerka", "description": "<p>Lokalizacja: ul. Racławicka 95, Wrocław</p>"}})
    assert find_address(read_olx(page)) == ("Racławicka", "95")


# ---------- the fill ----------

class Pages:
    def __init__(self, texts):
        self.texts, self.calls = dict(texts), []

    def __call__(self, row_):
        self.calls.append((row_.site, row_.id))
        text = self.texts[(row_.site, row_.id)]
        if isinstance(text, Exception):
            raise text
        return text


def patch_readers(monkeypatch):
    for site in ("otodom", "morizon", "nieruchomosci", "olx"):
        monkeypatch.setitem(details.READERS, site, lambda page, city=None: page)   # the "page" is already the text


def test_a_row_without_a_street_gets_the_one_its_page_names_and_the_answer_is_cached(monkeypatch):
    patch_readers(monkeypatch)
    pages = Pages({("otodom", "1"): "mieszkanie przy ul. Legnickiej 12", ("otodom", "2"): "nic o adresie"})
    cache = {}
    out = fill_addresses(rows(row(id="1"), row(id="2")), cache, fetch=pages, sleep=lambda s: None)
    assert (out[("otodom", "1")].street, out[("otodom", "1")].house) == ("Legnicka", "12")
    assert out[("otodom", "2")].street is None
    assert cache == {"otodom:1": ["Legnicka", "12"], "otodom:2": None}   # "nothing found" is remembered too
    again = Pages({})
    fill_addresses(rows(row(id="1"), row(id="2")), cache, fetch=again, sleep=lambda s: None)
    assert again.calls == []                                             # a page is read once


def test_rows_that_need_no_page_are_left_alone(monkeypatch):
    patch_readers(monkeypatch)
    pages = Pages({})
    listings = rows(
        row(id="1", street="Hubska"),            # already has a street
        row(site="s", id="2"),                   # a portal with no reader
        row(site="olx", id="3"),                 # skipped: the list already gave its full text this run
    )
    out = fill_addresses(listings, {}, skip={("olx", "3")}, fetch=pages, sleep=lambda s: None)
    assert pages.calls == [] and out == listings


def test_the_page_cap_and_the_spacing_between_fetches(monkeypatch):
    patch_readers(monkeypatch)
    pages = Pages({("otodom", str(i)): f"ul. Hubska {i}" for i in range(1, 5)})
    sleeps = []
    cache = {}
    out = fill_addresses(rows(*(row(id=str(i)) for i in range(1, 5))), cache, limit=3, fetch=pages, sleep=sleeps.append)
    assert len(pages.calls) == 3 and sleeps == [details.DELAY_S, details.DELAY_S] and details.DELAY_S >= 1.0
    assert out[("otodom", "4")].street is None and "otodom:4" not in cache   # waits for the next run


def test_failed_fetches_are_not_cached_and_too_many_stop_the_run(monkeypatch, caplog):
    patch_readers(monkeypatch)
    boom = OSError("blocked")
    pages = Pages({("otodom", str(i)): boom for i in range(1, 7)})
    cache = {}
    with caplog.at_level(logging.WARNING):
        out = fill_addresses(rows(*(row(id=str(i)) for i in range(1, 7))), cache, fetch=pages, sleep=lambda s: None)
    assert len(pages.calls) == details.MAX_FAILURES and cache == {}           # it gave up instead of hammering the site
    assert all(r.street is None for r in out.values()) and "blocked" in caplog.text


def test_an_unreadable_page_counts_as_a_failure_not_a_crash(monkeypatch):
    monkeypatch.setitem(details.READERS, "otodom", read_otodom)
    out = fill_addresses(rows(row()), {}, fetch=Pages({("otodom", "1"): "<html>changed layout</html>"}), sleep=lambda s: None)
    assert out[("otodom", "1")].street is None


def test_the_house_number_is_kept_and_a_known_one_is_not_replaced(monkeypatch):
    patch_readers(monkeypatch)
    pages = Pages({("otodom", "1"): "ul. Hubska 12"})
    out = fill_addresses(rows(row(id="1", house="7")), {}, fetch=pages, sleep=lambda s: None)
    assert (out[("otodom", "1")].street, out[("otodom", "1")].house) == ("Hubska", "7")


def test_cache_entries_of_listings_that_are_gone_are_dropped(monkeypatch):
    patch_readers(monkeypatch)
    cache = {"otodom:1": None, "otodom:gone": ["Hubska", "3"]}
    fill_addresses(rows(row(id="1")), cache, fetch=Pages({}), sleep=lambda s: None)
    assert cache == {"otodom:1": None}


def test_cache_file_round_trip_is_stable_and_ignores_garbage(tmp_path):
    path = tmp_path / "addresscache.json"
    cache = {"otodom:2": ["Żeromskiego", "5"], "olx:1": None, "morizon:x": ["Hubska", None]}
    save_cache(cache, path)
    text = path.read_text(encoding="utf-8")
    assert "Żeromskiego" in text and text.endswith("}\n") and text.index("morizon:x") < text.index("olx:1") < text.index("otodom:2")
    assert load_cache(path) == cache
    path.write_text(json.dumps({"a": ["x"], "b": "y", "c": [1, 2], "d": ["Hubska", 5], "e": ["Hubska", "1"]}), encoding="utf-8")
    assert load_cache(path) == {"e": ["Hubska", "1"]}
    assert load_cache(tmp_path / "missing.json") == {}
