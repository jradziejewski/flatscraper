import pytest

from aptsearch.address import Address, find_address, parse_house, resolve


@pytest.mark.parametrize("text, expected", [
    # real phrasing from OLX descriptions: inflected street, cue word, house number
    ("Mieszkanie zlokalizowane jest przy ul. Białowieskiej 105 - gwarantuje wygodne", ("Białowieska", "105")),
    ("położone przy ul. Racławickiej 95 we Wrocławiu. Lokal jest w pełni umeblowany", ("Racławicka", "95")),
    ("Lokalizacja: ul. Gajowicka 194, Wrocław Powierzchnia: 39 m²", ("Gajowicka", "194")),
    ("zlokalizowanej przy ulicy Gajowickiej 194 we Wrocławiu", ("Gajowicka", "194")),
    ("Mieszkanie przy ul. Legnickiej 33C w spokojnej okolicy", ("Legnicka", "33C")),
    ("Сдам кавалерку ul.Komedy 16 / Jagodno / Gaj", ("Krzysztofa Komedy", "16")),   # glued cue, a Cyrillic start
    ("ul. Gen. Sikorskiego 12", ("Generała Władysława Sikorskiego", "12")),         # the short "Gen." is ignored, the name is canonical
    ("al. Kromera 52", ("Marcina Kromera", "52")),
    ("pl. Grunwaldzki 22", ("Grunwaldzki", "22")),                                  # the square, not the street Grunwaldzka
    ("mieszkanie przy placu Grunwaldzkim w centrum", ("Grunwaldzki", None)),        # locative -im/-ym endings, and "przy" vouches for a square
    ("ul. Grunwaldzkiej 12", ("Grunwaldzka", "12")),
    ("przy ulicy Miedzianej we Wrocławiu", ("Miedziana", None)),                    # a street, no number
    ("Kawalerka, Krzyki, ul.Braterska, ogródek", ("Braterska", None)),
    ("Mieszkanie we Wrocławiu przy ul. Wrocławskiej 12", ("Wrocławska", "12")),     # "Wrocławska" is a street, the city is not
    # a title that STARTS with "<Street> <number>" needs no cue word
    ("Buforowa 89 - mieszkania na wynajem", ("Buforowa", "89")),
    # a street with a number beats an earlier street without one; a neighbour ("obok", "blisko") is never the flat's street
    ("Mieszkanie 2 pokoje, ul. Hubska, obok ul. Legnickiej 5", ("Hubska", None)),
    ("w pobliżu ul. Legnickiej 5 znajduje się ul. Hubska 7", ("Hubska", "7")),
])
def test_finds_the_street_and_house_the_text_names(text, expected):
    assert find_address(text) == Address(*expected)


@pytest.mark.parametrize("text", [
    "", None, "Mieszkanie do wynajęcia, 3 pokoje, balkon",
    "Mieszkanie blisko ul. Legnickiej, tramwaj pod domem",     # only a neighbour is named
    "Jasne 3-pokojowe mieszkanie z balkonem",                  # "Jasne" resembles the street Jasna, but it is a room count and no cue
    "Piękne 3-pokojowe mieszkanie na Jagodnie",
    "Mieszkanie w Głównej części miasta",                      # a street-like word without a cue word
    "w centrum/Piwna/pl. Grunwaldzki/Politechnika/Uniwersytet",  # a list of landmarks: a square without "przy"/"na" or a number
    "Madalińskiego 57, garaż, 2 pokoje",                       # inflected and no cue word: deliberately not guessed
    "ul. Nieistniejąca 12",                                    # not in the street list
    "mieszkanie we Wrocławiu, ul. Wrocławia",                  # the city's own name
])
def test_finds_nothing_where_the_text_names_no_street(text):
    assert find_address(text) is None


@pytest.mark.parametrize("text, house", [
    ("ul. Hubska 55A/10", "55A"),         # building 55A, flat 10: only the building is kept
    ("ul. Hubska 2/3", "2"),
    ("ul. Hubska 12a", "12A"),
    ("ul. Hubska 16-18", None),           # a range of buildings: ambiguous
    ("ul. Hubska 3 pokoje", None),        # not a house number
    ("przy ul. Hubskiej 45 m2 z balkonem", None),
    ("ul. Hubska 2-pokojowe", None),
    ("ul. Hubska 12, Krzyki", "12"),
    ("ul. Hubska 1234", None),
])
def test_house_number_formats_and_what_is_not_one(text, house):
    assert find_address(text) == Address("Hubska", house)


def test_parse_house_reads_right_after_a_position():
    assert parse_house("Komedy 16 / Jagodno", 6) == "16" and parse_house("Komedy, 16", 6) is None


def test_known_street_only_adds_the_house_number():
    assert find_address("w centrum, przy Hubskiej 12 w spokojnej okolicy", "Wrocław", known_street="Hubska") == Address("Hubska", "12")
    assert find_address("ul. Hubska 12", "Wrocław", known_street="gen. Hubska") == Address("gen. Hubska", "12")  # the portal's spelling is kept
    assert find_address("ul. Legnicka 5", "Wrocław", known_street="Hubska") is None       # another street's number is not ours
    assert find_address("przy Hubskiej, blisko centrum", "Wrocław", known_street="Hubska") is None   # no number given
    assert find_address("Hubska 3 pokoje", "Wrocław", known_street="Hubska") is None


def test_cities_without_a_street_list_find_nothing():
    assert find_address("ul. Hubska 12", "Kraków") is None and find_address("ul. Hubska 12", None) is None


def test_resolve_lets_the_portal_street_win():
    assert resolve("Pułaskiego", "ul. Madalińskiego 57", "Wrocław") == ("Pułaskiego", None)      # the text never overrides the portal
    assert resolve("Hubska", "mieszkanie przy Hubskiej 12", "Wrocław") == ("Hubska", "12")       # ...but it can add the number
    assert resolve(None, "ul. Madalińskiego 57", "Wrocław") == ("Generała Antoniego Madalińskiego", "57")  # and fill a gap
    assert resolve(None, "nic o adresie", "Wrocław") == (None, None)
    assert resolve("", "ul. Hubska 3", "Wrocław") == ("Hubska", "3")  # an empty portal street is no street
