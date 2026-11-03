import re
import unicodedata


def city_slug(city: str) -> str:
    """'Wrocław' -> 'wroclaw', 'Zielona Góra' -> 'zielona-gora' (URL slug used by the sites)."""
    text = city.casefold().replace("ł", "l")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")
