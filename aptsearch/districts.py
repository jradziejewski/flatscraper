"""Wrocław's 5 official districts and the neighbourhoods (osiedla) inside them.

Some sites (nieruchomosci-online) report a neighbourhood, a typo or "District-Neighbourhood" where
Otodom, Morizon and OLX report the district. `normalize_district` maps all of that to one of the 5
district names so the Dzielnica column and filters stay clean.

The table was built from the neighbourhood/district pairs Morizon and Otodom return (no neighbourhood
maps to two districts) and cross-checked against pl.wikipedia "Podział administracyjny Wrocławia"
(all names present in both agree). To extend it, add a name under its district; unknown names are
logged by main.py.
"""

import difflib
import re
import unicodedata

WROCLAW: dict[str, list[str]] = {
    "Fabryczna": [
        "Gądów",
        "Gądów Mały",
        "Gądów-Popowice Południowe",
        "Gajowice",
        "Grabiszyn",
        "Grabiszyn-Grabiszynek",
        "Jerzmanowo-Jarnołtów-Strachowice-Osiniec",
        "Kozanów",
        "Kuźniki",
        "Leśnica",
        "Maślice",
        "Muchobór Mały",
        "Muchobór Wielki",
        "Nowy Dwór",
        "Oporów",
        "Pilczyce",
        "Popowice",
        "Popowice Północne",
        "Popowice Południowe",
        "Stabłowice",
        "Żerniki",
        "Złotniki",
    ],
    "Krzyki": [
        "Borek",
        "Brochów",
        "Gaj",
        "Huby",
        "Jagodno",
        "Klecina",
        "Księże",
        "Księże Małe",
        "Księże Wielkie",
        "Ołtaszyn",
        "Partynice",
        "Powstańców Śląskich",
        "Przedmieście Oławskie",
        "Tarnogaj",
        "Wojszyce",
    ],
    "Psie Pole": [
        "Karłowice",
        "Kleczków",
        "Lipa Piotrowska",
        "Osobowice",
        "Poświętne",
        "Różanka",
        "Sołtysowice",
        "Swojczyce",
        "Zakrzów",
        "Zawidawie",
    ],
    "Stare Miasto": [
        "Przedmieście Świdnickie",
        "Szczepin",
    ],
    "Śródmieście": [
        "Bartoszowice",
        "Biskupin",
        "Kępa Mieszczańska",
        "Nadodrze",
        "Ołbin",
        "Plac Grunwaldzki",
        "Sępolno",
        "Szczytniki",
    ],
}

TABLES = {"wroclaw": WROCLAW}
FUZZY_CUTOFF = 0.85  # high on purpose: repairs typos like "Krzyk", but never guesses between real names


def fold(text: str) -> str:
    """Lowercase, no diacritics, single spaces, uniform dashes, no leading 'os.'/'osiedle'."""
    text = text.casefold().replace("ł", "l")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^(?:os\.|osiedle)\s*", "", text)


def _lookup(table: dict[str, list[str]]) -> dict[str, str]:
    lookup = {}
    for district, names in table.items():
        lookup[fold(district)] = district
        lookup.update({fold(n): district for n in names})
    return lookup


_LOOKUPS = {city: _lookup(table) for city, table in TABLES.items()}


def normalize_district(raw: str | None, city: str | None) -> str | None:
    """Official district for `raw`, or None if it is empty or cannot be matched.
    Cities without a table are returned unchanged."""
    lookup = _LOOKUPS.get(fold(city or ""))
    if lookup is None:
        return raw
    key = fold(raw or "")
    if not key:
        return None
    if key in lookup:
        return lookup[key]
    for part in re.split(r"\s*[-,]\s*", key):  # "Krzyki-Partynice" -> Krzyki
        if part in lookup:
            return lookup[part]
    close = difflib.get_close_matches(key, lookup, n=1, cutoff=FUZZY_CUTOFF)
    return lookup[close[0]] if close else None
