import html
import re
import unicodedata


def city_slug(city: str) -> str:
    """'Wrocław' -> 'wroclaw', 'Zielona Góra' -> 'zielona-gora' (URL slug used by the sites)."""
    text = city.casefold().replace("ł", "l")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


_STREET_PREFIX = re.compile(r"^(?:(?:ul|al|pl|os)\.\s*|(?:ulica|aleja|plac|osiedle)\s+)", re.IGNORECASE)


def clean_street(raw: str | None) -> str | None:
    """'ul. Przyjaźni' -> 'Przyjaźni'; empty or missing -> None. The prefix is always dropped
    so the same street compares equal whichever way a site writes it."""
    if not raw:
        return None
    street = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    street = _STREET_PREFIX.sub("", street).strip(" ,.")
    return street or None
