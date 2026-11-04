import html
import re
import unicodedata


def plain_text(markup: str | None) -> str:
    """Text of an HTML snippet (a listing description): tags dropped, entities decoded, one line. Block breaks become spaces,
    so "<p>ul. Hubska</p><p>12</p>" does not glue words together."""
    if not markup:
        return ""
    text = re.sub(r"<\s*(?:br|/p|/li|/div|/h\d)\s*/?>", " ", markup, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def city_slug(city: str) -> str:
    """'Wrocław' -> 'wroclaw', 'Zielona Góra' -> 'zielona-gora' (URL slug used by the sites)."""
    text = city.casefold().replace("ł", "l")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def photo_url(raw) -> str | None:
    """A thumbnail URL as the sites send it, or None. Only http(s) strings are kept: the value ends up in
    an <img src> on the page, and a photo is optional, so anything odd is dropped rather than failing the listing."""
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    return url if url.lower().startswith(("http://", "https://")) else None


_STREET_PREFIX =re.compile(r"^(?:(?:ul|al|pl|os)\.\s*|(?:ulica|aleja|plac|osiedle)\s+)", re.IGNORECASE)


def clean_street(raw: str | None) -> str | None:
    """'ul. Przyjaźni' -> 'Przyjaźni'; empty or missing -> None. The prefix is always dropped
    so the same street compares equal whichever way a site writes it."""
    if not raw:
        return None
    street = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    street = _STREET_PREFIX.sub("", street).strip(" ,.")
    return street or None
