"""A city's street names (from OpenStreetMap) and a matcher for finding them in running Polish text.

Streets appear inflected in listings ("przy ul. Białowieskiej" for the street "Białowieska"), and the geocoder
understands only the base form, so text has to be matched against a real list and mapped to the canonical name.
The matcher compares stems: a word with its case ending stripped ("Białowieskiej" and "Białowieska" both give "białowiesk").

The list is data, not code: `python -m aptsearch.streets` fetches it once (one Overpass query) into
aptsearch/data/streets_<city>.json, which is committed. Only Wrocław has one, like the district table.
"""
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

from .text import city_slug

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
OVERPASS = "https://overpass-api.de/api/interpreter"

# Longest first, so "iego" is tried before "ego" and "go". A stem must keep at least MIN_STEM letters.
CASE_ENDINGS = ("iego", "ego", "iej", "ej", "ymi", "imi", "ich", "ach", "ami", "owi", "em", "om", "ą", "ę", "y", "i", "a", "u", "o", "e")
MIN_STEM = 4
LETTERS = r"A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż"


def words(text: str) -> list[str]:
    """The words of `text`: runs of letters, nothing else ("55A" gives "A"; digits never count)."""
    return re.findall(f"[{LETTERS}]+", text)


def stem(word: str) -> str:
    """Case-folded word without its case ending, unless that would leave fewer than MIN_STEM letters."""
    w = word.casefold()
    for ending in CASE_ENDINGS:
        if w.endswith(ending) and len(w) - len(ending) >= MIN_STEM:
            return w[: -len(ending)]
    return w


def distinctive(word: str) -> bool:
    """Short words (gen., ks., św., im., de) are ignored when comparing street names."""
    return len(word) >= MIN_STEM


def _name_words(name: str) -> list[str]:
    return [w for w in words(re.sub(r"^(?:ul|al|pl|os)\.\s*", "", name)) if distinctive(w)]


class StreetIndex:
    """Canonical street names by the stem of their last distinctive word."""

    def __init__(self, names: dict[str, int]):
        self.names = names  # canonical name -> number of OSM ways (a proxy for how well known the street is)
        self._by_last: dict[str, list[str]] = defaultdict(list)
        for name in names:
            ws = _name_words(name)
            if ws:
                self._by_last[stem(ws[-1])].append(name)
        for candidates in self._by_last.values():
            candidates.sort(key=lambda n: (-names[n], n))

    def lookup(self, text_words: list[str], prefer: str | None = None) -> str | None:
        """Canonical name for words as written in a text ("Białowieskiej", "Krzysztofa Komedy"), or None.

        The last distinctive word must be the last distinctive word of the street; any earlier ones must also be in it.
        A single word ("Sikorskiego") may be the street's surname only. Several streets can share a surname: the best known wins.
        `prefer` is the kind the cue word named ("Plac", "Aleja", "Osiedle"): "pl. Grunwaldzki" is the square, not the street Grunwaldzka."""
        ws = [w for w in text_words if distinctive(w)]
        if not ws:
            return None
        candidates = self._by_last.get(stem(ws[-1]))
        if not candidates:
            return None
        wanted = {stem(w) for w in ws}
        fits = [n for n in candidates if wanted <= {stem(w) for w in _name_words(n)}]
        kind = [n for n in fits if prefer and n.casefold().startswith(prefer.casefold())]  # OSM writes "plac X" and "Plac X"
        if kind:
            return kind[0]
        # "ul. Miedzianej" means "Miedziana", not the better-known "Alejka Miedziana": prefer a name with exactly the words written
        exact = [n for n in fits if len(_name_words(n)) == len(ws)]
        return (exact or fits or [None])[0]

    def lookup_exact(self, text_words: list[str]) -> str | None:
        """Like `lookup`, but the words must be the street's own words in the base form (no inflection): for text with no cue
        word before the name, where "Jasne" must not become the street "Jasna"."""
        ws = [w.casefold() for w in text_words if distinctive(w)]
        for name in self._by_last.get(stem(ws[-1]) if ws else "", []):
            if [w.casefold() for w in _name_words(name)] == ws:
                return name
        return None


def streets_path(city: str) -> Path:
    return DATA_DIR / f"streets_{city_slug(city)}.json"


_loaded: dict[str, StreetIndex | None] = {}


def street_index(city: str | None) -> StreetIndex | None:
    """The index for `city`, or None when there is no list for it (only Wrocław has one)."""
    if not city:
        return None
    key = city_slug(city)
    if key not in _loaded:
        path = streets_path(city)
        _loaded[key] = StreetIndex(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
    return _loaded[key]


def fetch_streets(city: str, fetch=None) -> dict[str, int]:
    """One Overpass query: every named road in the city, counted by name. A way is one road segment, so a long street has many."""
    from . import http
    from .geocode import USER_AGENT

    fetch = fetch or http.get
    query = f'[out:json][timeout:90];area["name"="{city}"]["boundary"="administrative"]["admin_level"="6"]->.a;way(area.a)["highway"]["name"];out tags;'
    raw = json.loads(fetch(f"{OVERPASS}?{urlencode({'data': query})}", timeout=150, user_agent=USER_AGENT))
    counts: dict[str, int] = defaultdict(int)
    for element in raw.get("elements", []):
        name = (element.get("tags") or {}).get("name")
        if name:
            counts[name.strip()] += 1
    return dict(counts)


def save_streets(names: dict[str, int], city: str) -> Path:
    path = streets_path(city)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(names, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    # One-off (and whenever new streets matter): python -m aptsearch.streets
    from .config import load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    city = load_config("config.yaml").city
    names = fetch_streets(city)
    if len(names) < 500:  # a half-answer from an overloaded server must not replace a good list
        raise SystemExit(f"only {len(names)} names for {city}: refusing to overwrite the list")
    log.info("%d street names for %s saved to %s", len(names), city, save_streets(names, city))
