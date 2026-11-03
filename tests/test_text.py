import pytest

from aptsearch.text import city_slug, clean_street, photo_url


@pytest.mark.parametrize("raw, expected", [
    ("ul. Przyjaźni", "Przyjaźni"), ("Ul. Przyjaźni", "Przyjaźni"), ("ul.Przyjaźni", "Przyjaźni"),
    ("al. Kromera", "Kromera"), ("Aleja Kromera", "Kromera"), ("pl. Grunwaldzki", "Grunwaldzki"),
    ("Plac Grunwaldzki", "Grunwaldzki"), ("os. Sobieskiego", "Sobieskiego"), ("ulica Hubska", "Hubska"),
    ("Hubska", "Hubska"), ("  Obrońców   Poczty Gdańskiej ", "Obrońców Poczty Gdańskiej"),
    ("Żeromskiego,", "Żeromskiego"), ("Kuźnicza &amp; Rynek", "Kuźnicza & Rynek"),
])
def test_clean_street(raw, expected):
    assert clean_street(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "ul.", "ul. "])
def test_clean_street_empty_is_none(raw):
    assert clean_street(raw) is None


def test_clean_street_keeps_names_that_merely_start_like_a_prefix():
    assert clean_street("Ulańska") == "Ulańska"
    assert clean_street("Osobowicka") == "Osobowicka"
    assert clean_street("Aleksandra Fredry") == "Aleksandra Fredry"
    assert clean_street("Placowa") == "Placowa"


def test_clean_street_is_idempotent():
    assert clean_street(clean_street("ul. Przyjaźni")) == "Przyjaźni"


def test_photo_url_keeps_only_http_urls():
    assert photo_url("https://img.test/a.jpg") == "https://img.test/a.jpg"
    assert photo_url("  http://img.test/a.jpg ") == "http://img.test/a.jpg"
    assert photo_url("HTTPS://img.test/a.jpg") == "HTTPS://img.test/a.jpg"
    for bad in (None, "", "   ", 5, ["https://x"], {"a": 1}, "javascript:alert(1)", "data:image/png;base64,AAAA",
                "//img.test/a.jpg", "ftp://img.test/a.jpg", "/relative.jpg"):
        assert photo_url(bad) is None


def test_city_slug():
    assert city_slug("Wrocław") == "wroclaw"
    assert city_slug("Zielona Góra") == "zielona-gora"
