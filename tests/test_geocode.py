import json
import math
from urllib.parse import parse_qs, urlparse

import pytest

from aptsearch import geocode
from aptsearch.geocode import cache_key, clean_point, fill_street_coordinates, load_cache, lookup, save_cache
from aptsearch.models import Listing


def row(id, street="Hubska", city="Wrocław", **kw):
    return Listing(site="otodom", id=id, url="u", title="t", street=street, city=city, **kw)


def rows(*listings):
    return {(l.site, l.id): l for l in listings}


def hit(lat, lon):
    return json.dumps([{"lat": str(lat), "lon": str(lon), "display_name": "x"}])  # Nominatim sends strings


class Fake:
    """A stand-in for http.get that replays answers in order and records every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def streets(self):
        return [parse_qs(urlparse(url).query)["q"][0] for url, _ in self.calls]


def test_clean_point_accepts_numbers_and_strings_and_rejects_nonsense():
    assert clean_point("51.1234567891", "17.0000001") == (51.123457, 17.0)
    assert clean_point(51.1, 17.0) == (51.1, 17.0)
    for bad in [(None, 17.0), (51.1, None), ("x", 17.0), (math.nan, 17.0), (51.1, math.inf), (10.0, 17.0), (51.1, 40.0), ([], {})]:
        assert clean_point(*bad) is None, bad
    assert clean_point(52.23, 21.01, geocode.CITY_BOXES["wrocław"]) is None  # Warsaw is not Wrocław


def test_lookup_sends_an_identifying_user_agent_and_a_polish_city_query():
    fake = Fake(hit(51.1, 17.03))
    assert lookup("Hubska", "Wrocław", fake) == (51.1, 17.03)
    url, kwargs = fake.calls[0]
    query = parse_qs(urlparse(url).query)
    assert urlparse(url).netloc == "nominatim.openstreetmap.org"
    assert query["q"] == ["Hubska, Wrocław"] and query["countrycodes"] == ["pl"] and query["limit"] == ["1"]
    assert "github.com/jradziejewski/flatscraper" in kwargs["user_agent"] and "@" not in kwargs["user_agent"]  # app name, no personal address


def test_lookup_rejects_a_same_named_street_in_another_city_and_empty_answers():
    assert lookup("Hubska", "Wrocław", Fake(hit(52.23, 21.01))) is None  # Warsaw
    assert lookup("Hubska", "Wrocław", Fake("[]")) is None


def test_fill_sets_street_coordinates_and_asks_once_per_street():
    fake = Fake(hit(51.1, 17.03))
    cache = {}
    out = fill_street_coordinates(rows(row("1"), row("2")), cache, fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == 1  # two flats, one street
    for key in (("otodom", "1"), ("otodom", "2")):
        assert (out[key].lat, out[key].lon, out[key].geo) == (51.1, 17.03, "street")
    assert cache == {"wrocław|hubska": [51.1, 17.03]}
    again = fill_street_coordinates(rows(row("3")), cache, fetch=Fake(), sleep=lambda s: None)  # an empty Fake would raise if asked
    assert again[("otodom", "3")].lat == 51.1


def test_a_street_that_does_not_resolve_is_cached_as_null_and_not_asked_again():
    cache = {}
    out = fill_street_coordinates(rows(row("1")), cache, fetch=Fake("[]"), sleep=lambda s: None)
    assert out[("otodom", "1")].lat is None and cache == {"wrocław|hubska": None}
    fake = Fake()
    fill_street_coordinates(rows(row("1")), cache, fetch=fake, sleep=lambda s: None)
    assert fake.calls == []


def test_lookups_are_capped_and_spaced_out():
    sleeps = []
    fake = Fake(hit(51.1, 17.0), hit(51.1, 17.1), hit(51.1, 17.2), hit(51.1, 17.3))
    listings = rows(row("1", "Aleja"), row("2", "Bema"), row("3", "Cicha"), row("4", "Dobra"))
    cache = {}
    out = fill_street_coordinates(listings, cache, limit=3, fetch=fake, sleep=sleeps.append)
    assert fake.streets() == ["Aleja, Wrocław", "Bema, Wrocław", "Cicha, Wrocław"]  # deterministic order, stops at the cap
    assert sleeps == [geocode.DELAY_S, geocode.DELAY_S] and geocode.DELAY_S >= 1.0   # OSM allows one request per second
    assert out[("otodom", "4")].lat is None and "wrocław|dobra" not in cache          # waits for the next run


def test_a_failed_request_stops_the_lookups_and_is_not_cached():
    fake = Fake(OSError("offline"), hit(51.1, 17.0))
    cache = {}
    out = fill_street_coordinates(rows(row("1", "Aleja"), row("2", "Bema")), cache, fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == 1 and cache == {}  # it gave up instead of hammering a broken service
    assert all(r.lat is None for r in out.values())


def test_rows_that_need_no_lookup_are_left_alone():
    fake = Fake()
    cache = {}
    listings = rows(
        row("1", lat=51.1, lon=17.0, geo="point"),   # already has a (better) location
        row("2", street=None),                       # no street
        row("3", city="Kraków"),                     # no city box: cannot tell a wrong-city hit from a right one
    )
    out = fill_street_coordinates(listings, cache, fetch=fake, sleep=lambda s: None)
    assert fake.calls == [] and cache == {} and out == listings
    assert out[("otodom", "1")].geo == "point"


def test_the_configured_city_is_used_when_a_row_has_none():
    fake = Fake(hit(51.1, 17.03))
    out = fill_street_coordinates(rows(row("1", city=None)), {}, default_city="Wrocław", fetch=fake, sleep=lambda s: None)
    assert out[("otodom", "1")].geo == "street"


def building(number, road="Krzysztofa Komedy", lat=51.1, lon=17.03):
    return json.dumps([{"lat": str(lat), "lon": str(lon), "address": {"house_number": number, "road": road}}])


def test_a_building_is_asked_for_with_its_number_and_only_accepted_if_the_answer_is_that_building():
    fake = Fake(building("16"))
    assert lookup("Krzysztofa Komedy", "Wrocław", fake, house="16") == (51.1, 17.03)
    url, _ = fake.calls[0]
    query = parse_qs(urlparse(url).query)
    assert query["q"] == ["Krzysztofa Komedy 16, Wrocław"] and query["addressdetails"] == ["1"]
    # Nominatim quietly answers with the street itself when it does not know the building: that is not a building
    assert lookup("Hubska", "Wrocław", Fake(json.dumps([{"lat": "51.1", "lon": "17.0", "address": {"road": "Hubska"}}])), house="12") is None
    assert lookup("Hubska", "Wrocław", Fake(building("14", "Hubska")), house="12") is None             # another number
    assert lookup("Hubska", "Wrocław", Fake(building("12", "Legnicka")), house="12") is None           # another road
    assert lookup("Hubska", "Wrocław", Fake(building("12", "Hubska", 52.23, 21.01)), house="12") is None  # outside the city
    assert lookup("Hubska", "Wrocław", Fake(building("33c", "Hubska")), house="33C") == (51.1, 17.03)   # case does not matter


def test_a_row_with_a_house_number_gets_its_building_and_the_cache_remembers_it():
    fake = Fake(building("16"))
    cache = {}
    listings = rows(row("1", street="Krzysztofa Komedy", house="16"), row("2", street="Krzysztofa Komedy", house="16"))
    out = fill_street_coordinates(listings, cache, fetch=fake, sleep=lambda s: None)
    assert len(fake.calls) == 1                                      # two flats, one building
    assert (out[("otodom", "1")].geo, out[("otodom", "2")].lat) == ("address", 51.1)
    assert cache == {"wrocław|krzysztofa komedy|16": [51.1, 17.03]}


def test_a_building_replaces_a_street_point_or_a_blurred_area_but_never_the_other_way_round():
    fake = Fake(building("16"), building("16", lat=51.15, lon=17.05))
    cache = {}
    upgraded = fill_street_coordinates(
        rows(row("1", street="Krzysztofa Komedy", house="16", lat=51.0, lon=17.0, geo="street"),
             row("2", street="Krzysztofa Komedy", house="16", lat=51.2, lon=16.9, geo="area")),
        cache, fetch=fake, sleep=lambda s: None)
    assert [upgraded[("otodom", i)].geo for i in "12"] == ["address", "address"]
    assert upgraded[("otodom", "1")].lat == 51.1
    # a row that already is a building is left alone, with no request at all
    quiet = Fake()
    done = rows(row("3", street="Krzysztofa Komedy", house="16", lat=51.1, lon=17.03, geo="address"))
    assert fill_street_coordinates(done, {}, fetch=quiet, sleep=lambda s: None) == done and quiet.calls == []


def test_when_no_building_is_found_the_street_point_is_the_fallback_and_the_house_number_stays():
    fake = Fake("[]", hit(51.1, 17.03))          # the building is unknown, the street is known
    cache = {}
    out = fill_street_coordinates(rows(row("1", street="Hubska", house="12")), cache, fetch=fake, sleep=lambda s: None)
    row1 = out[("otodom", "1")]
    assert (row1.geo, row1.house, row1.lat) == ("street", "12", 51.1)
    assert cache == {"wrocław|hubska|12": None, "wrocław|hubska": [51.1, 17.03]}   # both answers are remembered
    again = fill_street_coordinates(rows(row("1", street="Hubska", house="12")), cache, fetch=Fake(), sleep=lambda s: None)
    assert again[("otodom", "1")].geo == "street"                                  # no new request
    # an area-level row without a building keeps its area: a street midpoint is not an upgrade it is asked for
    kept = fill_street_coordinates(rows(row("2", street="Hubska", house="12", lat=51.2, lon=16.9, geo="area")), cache, fetch=Fake(), sleep=lambda s: None)
    assert kept[("otodom", "2")].geo == "area"


def test_cache_file_round_trip_is_stable_and_keeps_polish_letters(tmp_path):
    path = tmp_path / "geocache.json"
    cache = {cache_key("Żeromskiego", "Wrocław"): [51.1, 17.0], cache_key("Cicha", "Wrocław"): None}
    save_cache(cache, path)
    text = path.read_text(encoding="utf-8")
    assert "wrocław|żeromskiego" in text and text.endswith("}\n")  # readable, not \u-escaped
    assert text.index("cicha") < text.index("żeromskiego")        # sorted keys keep diffs small
    assert load_cache(path) == cache
    save_cache(load_cache(path), path)
    assert path.read_text(encoding="utf-8") == text


def test_cache_ignores_garbage_entries(tmp_path):
    path = tmp_path / "geocache.json"
    path.write_text(json.dumps({"a": [51.1, 17.0], "b": None, "c": [51.1], "d": "x", "e": [10, 10], "f": [51.1, "y"]}), encoding="utf-8")
    assert load_cache(path) == {"a": [51.1, 17.0], "b": None}
    assert load_cache(tmp_path / "missing.json") == {}
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_cache(path) == {}
