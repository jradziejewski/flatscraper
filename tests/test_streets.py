import json

import pytest

from aptsearch import streets
from aptsearch.streets import StreetIndex, distinctive, fetch_streets, stem, street_index, words

NAMES = {
    "Białowieska": 5, "Krzysztofa Komedy": 3, "Alejka Miedziana": 9, "Miedziana": 2, "plac Grunwaldzki": 44,
    "Grunwaldzka": 18, "Świętego Józefa": 4, "Józefa Hallera": 1, "Generała Władysława Sikorskiego": 6,
    "Tadeusza Kościuszki": 12, "Kościuszki": 2, "Aleja Kasztanowa": 1, "Kasztanowa": 7,
}


@pytest.fixture
def index():
    return StreetIndex(NAMES)


def test_words_are_letters_only():
    assert words("ul. 55A/10 Gen. Sikorskiego, Wrocław") == ["ul", "A", "Gen", "Sikorskiego", "Wrocław"]
    assert [w for w in words("gen. Sikorskiego 12") if distinctive(w)] == ["Sikorskiego"]  # gen., ks., św.: too short to identify a street


def test_stem_strips_one_case_ending_but_keeps_short_words_whole():
    assert stem("Białowieskiej") == stem("Białowieska") == "białowiesk"
    assert stem("Madalińskiego") == stem("Madaliński") == "madalińsk"
    assert stem("Komedy") == "komed" and stem("Kościuszki") == "kościuszk"
    assert stem("Grunwaldzkim") == stem("Grunwaldzki") == "grunwaldzk"            # locative -im / -ym
    assert stem("Sad") == "sad" and stem("Lipa") == "lipa"  # stripping would leave fewer than 4 letters


@pytest.mark.parametrize("written, canonical", [
    (["Białowieskiej"], "Białowieska"),                 # inflected -> the base form the geocoder understands
    (["Białowieska"], "Białowieska"),
    (["Komedy"], "Krzysztofa Komedy"),                  # a surname alone
    (["Krzysztofa", "Komedy"], "Krzysztofa Komedy"),
    (["Sikorskiego"], "Generała Władysława Sikorskiego"),
    (["Władysława", "Sikorskiego"], "Generała Władysława Sikorskiego"),
    (["Józefa", "Hallera"], "Józefa Hallera"),          # not "Świętego Józefa": the last word decides, earlier words must fit too
    (["Kościuszki"], "Kościuszki"),                     # an exact one-word name beats the better-known "Tadeusza Kościuszki"
    (["Miedzianej"], "Miedziana"),                      # ...and "ul. Miedzianej" is not "Alejka Miedziana"
])
def test_lookup_maps_written_forms_to_the_canonical_name(index, written, canonical):
    assert index.lookup(written) == canonical


@pytest.mark.parametrize("written", [["Wrocławskiej"], ["Nieznanej"], [], ["gen", "w"], ["Anny", "Hallera"]])
def test_lookup_returns_none_for_what_is_not_a_street(index, written):
    assert index.lookup(written) is None


def test_the_cue_word_can_pick_the_kind_of_place(index):
    assert index.lookup(["Grunwaldzki"]) == "Grunwaldzka"                    # no kind: the street
    assert index.lookup(["Grunwaldzki"], prefer="Plac") == "plac Grunwaldzki"  # "pl. Grunwaldzki": the square, however OSM capitalises it
    assert index.lookup(["Kasztanowa"], prefer="Aleja") == "Aleja Kasztanowa"


def test_lookup_exact_needs_the_base_form(index):
    assert index.lookup_exact(["Białowieska"]) == "Białowieska"
    assert index.lookup_exact(["Białowieskiej"]) is None   # without a cue word an inflected adjective is far more likely to be ordinary text
    assert index.lookup_exact(["Komedy"]) is None          # the full name is "Krzysztofa Komedy"
    assert index.lookup_exact([]) is None


def test_the_committed_wroclaw_list_is_there_and_other_cities_have_none():
    index = street_index("Wrocław")
    assert index is not None and len(index.names) > 2000
    assert index.lookup(["Legnickiej"]) == "Legnicka" and index.lookup(["Hubskiej"]) == "Hubska"
    assert street_index("Kraków") is None and street_index(None) is None and street_index("") is None


def test_fetch_streets_counts_ways_per_name():
    answer = json.dumps({"elements": [{"tags": {"name": "Hubska"}}, {"tags": {"name": "Hubska"}}, {"tags": {"name": " Cicha "}}, {"tags": {}}, {}]})
    seen = {}

    def fake(url, **kwargs):
        seen.update(url=url, **kwargs)
        return answer

    assert fetch_streets("Wrocław", fake) == {"Hubska": 2, "Cicha": 1}
    assert seen["url"].startswith("https://overpass-api.de/api/interpreter?data=")
    assert "github.com/jradziejewski/flatscraper" in seen["user_agent"]  # identifies the app, no personal address
    assert "Wroc%C5%82aw" in seen["url"]


def test_save_streets_is_stable_and_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(streets, "DATA_DIR", tmp_path)
    path = streets.save_streets({"Żeromskiego": 3, "Cicha": 1}, "Wrocław")
    text = path.read_text(encoding="utf-8")
    assert path.name == "streets_wroclaw.json" and "Żeromskiego" in text and text.index("Cicha") < text.index("Żeromskiego")
    assert json.loads(text) == {"Cicha": 1, "Żeromskiego": 3}
