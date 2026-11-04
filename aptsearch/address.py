"""Find the street (and house number) a listing's text says the flat is on.

Listings often name it in the title or description ("przy ul. Komedy 16", "Buforowa 89 - mieszkania na wynajem"),
which fills the gap for portals that give no street (OLX never does) and lets the map pin the building, not the street.
A wrong street is worse than none (it would mislead the map and the duplicate merging), so the rules are conservative:

- a cue word ("ul.", "ulicy", "al.", "plac", ...) must come right before the name, and the name must be in the city's
  real street list (aptsearch/streets.py), matched by stem so inflected forms ("Białowieskiej") work;
- a street named after a "nearby" word ("blisko", "obok", "w pobliżu") is a neighbour, not the flat's street: skipped;
- the city itself ("we Wrocławiu") is never a street;
- one more case without a cue: a title that STARTS with "<Street> <number>".
The result is always the canonical name from the list, never the inflected text.
"""
import re
from typing import NamedTuple

from .streets import LETTERS, StreetIndex, distinctive, stem, street_index, words
from .text import clean_street

CUE = re.compile(
    rf"(?<![{LETTERS}])(?:ul\.|ulic[aąęyi](?![{LETTERS}])|al\.|alei(?![{LETTERS}])|aleja(?![{LETTERS}])|aleją(?![{LETTERS}])"
    rf"|pl\.|placu(?![{LETTERS}])|plac(?![{LETTERS}])|os\.|osiedle(?![{LETTERS}])|osiedlu(?![{LETTERS}]))\s*",
    re.IGNORECASE,
)
TOKEN = re.compile(rf"[{LETTERS}0-9][{LETTERS}0-9.\-]*")
# "ul.Komedy": a glued cue; the cue regex copes, the capital letter check below does not need a space either
# Words that mean the street named next to them is a neighbour, not the flat's own street: "blisko ul. X", "dojazd do szkoły
# przy ul. X". Long descriptions name many schools, shops and stops, so these matter more as the text gets longer. A landmark
# word counts only within 3 words of the cue and never across a sentence end ("Szkoła jest blisko. Mieszkanie przy ul. X" is fine).
LANDMARKS = (
    r"blisko|bliskość|obok|pobliż\w*|niedaleko|nieopodal|sąsiedztw\w*|dojazd\w*|skrzyżowani\w*|róg|rogu|między|"
    r"szkoł\w*|school|przedszkol\w*|żłob\w*|uczelni\w*|uniwersytet\w*|politechnik\w*|szpital\w*|przychodni\w*|apte\w*|"
    r"sklep\w*|market\w*|biedronk\w*|lidl|żabk\w*|kaufland|galeri\w*|park(?:u|owi|iem|i|y|ach|ów)?(?![\w])|kości(?:oł|ół)\w*|kościele|stacj\w*|przystan\w*|tramwaj\w*|"
    r"autobus\w*|metro(?![\w])|dworc\w*|dworzec|lotnisk\w*|biur[ao]|urzęd\w*|urząd|agenc\w*|agency|real estate"
)
NEARBY = re.compile(rf"(?:{LANDMARKS})[^\w.!?]{{0,3}}(?:\w+[^\w.!?]{{1,3}}){{0,3}}$", re.IGNORECASE)
# The agency's own address, usually in the footer of a long description: "... Tel. kom.: 72*******76 50-541 ul. Krakowska 52".
# A phone label, a phone number (masked or not) or a postal code in the 40 characters before the cue marks a contact block, not the flat.
# (A bare word like "pośredników" or "nieruchomości" proved too common in real listings to count: only these shapes do.)
CONTACT = re.compile(r"(?:\b(?:tel|kom|mobile|phone|fax)\b|\d{2}-\d{3}|\*{2,}|\b\d{3}[ -]\d{3}[ -]\d{3}\b)[^\n]{0,40}$", re.IGNORECASE)
# words after a number that show it is not a house number ("Komedy 45 m2", "ul. Hubska 3 pokoje")
UNITS = re.compile(r"(?:m\b|m2|m²|mkw|pok\w*|zł|pln|piętr\w*|p\.|osob\w*|os\.|mies\w*|lat\w*|dni|kg|%)", re.IGNORECASE)
HOUSE = re.compile(r"[ \t]*(\d{1,3}[A-Za-z]?)(?![A-Za-z0-9])(?:[ \t]*/[ \t]*\d{1,3}[A-Za-z]?)?")
CITY_STEMS = {"wrocław", "wrocławi"}  # "Wrocławia", "Wrocławiu", "Wrocław": the city, not a street (but "Wrocławska" is one)
TITLE_START = re.compile(
    rf"^\W*([A-ZŁŚŻŹĆŃÓ][{LETTERS}]+(?:[ \t]+[A-ZŁŚŻŹĆŃÓ][{LETTERS}]+){{0,2}})[ \t]+(\d{{1,3}}[A-Za-z]?)(?![A-Za-z0-9/])"
)


class Address(NamedTuple):
    street: str  # canonical name from the street list, without "ul."/"Aleja" (as `text.clean_street` writes it)
    house: str | None  # the building number ("16", "92A"); the flat number after "/" is dropped


def parse_house(text: str, pos: int) -> str | None:
    """The house number written right after position `pos` ("... Komedy 16", "... Hubska 55A/10"), or None."""
    m = HOUSE.match(text, pos)
    if not m:
        return None
    rest = text[m.end() : m.end() + 6]
    if re.match(r"[ \t]*-[ \t]*\d", rest):  # "16-18": a range of buildings, ambiguous
        return None
    if re.match(r"-[A-Za-z]", rest):  # "3-pokojowe", "2-os.": a count of rooms or people, not a building
        return None
    if UNITS.match(rest.lstrip(" \t")):
        return None
    return m.group(1).upper()


def _tokens_after(text: str, pos: int) -> list[re.Match]:
    """Up to 4 words/numbers after `pos`, only while they are separated by plain spaces (a street name has no comma or slash in it)."""
    out: list[re.Match] = []
    last_end = pos
    for m in TOKEN.finditer(text, pos, pos + 70):
        if out and text[last_end : m.start()].strip(" \t"):
            break
        if not out and text[pos : m.start()].strip(" \t"):
            break
        out.append(m)
        last_end = m.end()
        if len(out) == 4:
            break
    return out


KINDS = (("pl", "Plac"), ("plac", "Plac"), ("placu", "Plac"), ("al", "Aleja"), ("alei", "Aleja"), ("aleja", "Aleja"), ("aleją", "Aleja"),
         ("os", "Osiedle"), ("osiedle", "Osiedle"), ("osiedlu", "Osiedle"))


def _kind(cue: str) -> str | None:
    """"pl." / "placu" -> "Plac", "al." / "alei" -> "Aleja": the kind of place the cue word names ("ul." names no kind)."""
    word = cue.strip().rstrip(".").casefold()
    return next((kind for prefix, kind in KINDS if word == prefix), None)


def _canonical(index: StreetIndex, ws: list[str], prefer: str | None = None) -> str | None:
    if not ws or stem(ws[-1]) in CITY_STEMS:
        return None
    name = index.lookup(ws, prefer)
    return clean_street(name) if name else None


def _after_cue(text: str, index: StreetIndex) -> list[tuple[Address, bool]]:
    """Every street named right after a cue word, as (address, strong): weak ones follow a "nearby" word and are not used."""
    found = []
    for cue in CUE.finditer(text):
        tokens = _tokens_after(text, cue.end())
        for span in (3, 2, 1):
            chosen = tokens[:span]
            if len(chosen) < span:
                continue
            ws = [w for t in chosen for w in words(t.group()) if distinctive(w)]
            kind = _kind(cue.group())
            street = _canonical(index, ws, kind)
            if not street:
                continue
            last = max(i for i, t in enumerate(chosen) if any(distinctive(w) for w in words(t.group())))
            house = parse_house(text, chosen[last].end())
            # "centrum/Piwna/pl. Grunwaldzki/Politechnika" lists landmarks: a square or estate counts as the flat's address
            # only when it has a house number or the text says the flat is "przy"/"na" it
            if kind in ("Plac", "Osiedle") and not house and not re.search(r"\b(?:przy|na)\s*$", text[max(0, cue.start() - 12) : cue.start()], re.IGNORECASE):
                continue
            strong = not NEARBY.search(text[max(0, cue.start() - 70) : cue.start()]) and not CONTACT.search(text[max(0, cue.start() - 45) : cue.start()])
            found.append((Address(street, house), strong))
            break
    return found


def find_address(text: str, city: str | None = "Wrocław", known_street: str | None = None) -> Address | None:
    """The street and house number a text names for the flat, or None. With `known_street` (the portal gave one) it
    only looks for that street's house number. Only cities with a street list work (Wrocław)."""
    index = street_index(city)
    if index is None or not text:
        return None
    text = re.sub(r"\s+", " ", text)
    if known_street:
        return _house_for_known_street(text, known_street)
    strong = [a for a, ok in _after_cue(text, index) if ok]
    for address in strong:  # a street with a number is the best evidence; otherwise the first one named
        if address.house:
            return address
    if strong:
        return strong[0]
    m = TITLE_START.match(text)
    if m:
        exact = index.lookup_exact(words(m.group(1)))  # no cue word, so only the street's own spelling counts
        if exact and not UNITS.match(text[m.end() : m.end() + 6].lstrip(" \t")) and stem(words(exact)[-1]) not in CITY_STEMS:
            return Address(clean_street(exact), m.group(2).upper())
    return None


def resolve(street: str | None, text: str, city: str | None) -> tuple[str | None, str | None]:
    """(street, house) for a listing: the portal's own street always wins and the text only adds its house number;
    when the portal gave no street, the street the text names (with its number) fills the gap."""
    if street:
        found = find_address(text, city, known_street=street)
        return street, found.house if found else None
    found = find_address(text, city)
    return (found.street, found.house) if found else (None, None)


def _house_for_known_street(text: str, street: str) -> Address | None:
    ws = [w for w in words(street) if distinctive(w)]
    if not ws:
        return None
    want = stem(ws[-1])
    for m in TOKEN.finditer(text):
        if any(distinctive(w) and stem(w) == want for w in words(m.group())):
            house = parse_house(text, m.end())
            if house:
                return Address(street, house)
    return None
