import pytest

from aptsearch.districts import WROCLAW, fold, normalize_district

OFFICIAL = {"Fabryczna", "Krzyki", "Psie Pole", "Stare Miasto", "Śródmieście"}


def norm(raw, city="Wrocław"):
    return normalize_district(raw, city)


@pytest.mark.parametrize("raw", sorted(OFFICIAL))
def test_official_names_pass_through(raw):
    assert norm(raw) == raw


@pytest.mark.parametrize("raw, expected", [
    ("KRZYKI", "Krzyki"), ("krzyki", "Krzyki"), ("srodmiescie", "Śródmieście"), ("ŚRÓDMIEŚCIE", "Śródmieście"),
    ("Psie  Pole", "Psie Pole"), ("  Psie Pole ", "Psie Pole"),
    ("Os. Psie Pole", "Psie Pole"), ("Os. Stare Miasto", "Stare Miasto"), ("Os. Powstańców Śląskich", "Krzyki"),
])
def test_spelling_variants(raw, expected):
    assert norm(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Partynice", "Krzyki"), ("Popowice Południowe", "Fabryczna"), ("Nadodrze", "Śródmieście"),
    ("Kępa Mieszczańska", "Śródmieście"), ("Sępolno", "Śródmieście"), ("Kuźniki", "Fabryczna"),
    ("Żerniki", "Fabryczna"), ("Lipa Piotrowska", "Psie Pole"), ("Stabłowice", "Fabryczna"),
    ("Szczepin", "Stare Miasto"), ("Różanka", "Psie Pole"),
    ("Rakowiec", "Krzyki"), ("Strachowice", "Fabryczna"),
])
def test_neighbourhoods_map_to_their_district(raw, expected):
    assert norm(raw) == expected


def test_composite_names():
    assert norm("Krzyki-Partynice") == "Krzyki"
    assert norm("Gądów-Popowice Południowe") == "Fabryczna"  # a real osiedle name, matched whole
    assert norm("Grabiszyn-Grabiszynek") == "Fabryczna"
    assert norm("Jerzmanowo-Jarnołtów-Strachowice-Osiniec") == "Fabryczna"  # long osiedle name, matched whole
    assert norm("Stare Miasto, Wrocław") == "Stare Miasto"
    assert norm("Gądów–Popowice Południowe") == "Fabryczna"  # en dash


@pytest.mark.parametrize("raw, expected", [
    ("Krzyk", "Krzyki"), ("Krzki", "Krzyki"), ("Fabryczn", "Fabryczna"), ("Psie Pol", "Psie Pole"),
])
def test_missing_letter_typos_are_repaired(raw, expected):
    assert norm(raw) == expected


@pytest.mark.parametrize("raw", ["Atlantyda", "Wrocław", "Centrum", "", "   ", None])
def test_unknown_or_empty_is_none(raw):
    assert norm(raw) is None


def test_other_cities_are_left_alone():
    assert norm("Mokotów", "Warszawa") == "Mokotów"
    assert norm("Krzk", "Kraków") == "Krzk"
    assert norm(None, "Kraków") is None
    assert norm("Partynice", None) == "Partynice"  # no city known: no table, no change


def test_idempotent_for_every_name_in_the_table():
    for district, names in WROCLAW.items():
        for name in [district, *names]:
            once = norm(name)
            assert once == district, (name, once)
            assert norm(once) == once


def test_table_integrity():
    assert set(WROCLAW) == OFFICIAL
    seen: dict[str, str] = {}
    for district, names in WROCLAW.items():
        for name in names:
            key = fold(name)
            assert seen.setdefault(key, district) == district, f"{name!r} is under two districts"
            assert key not in {fold(d) for d in OFFICIAL}, f"{name!r} duplicates a district name"
