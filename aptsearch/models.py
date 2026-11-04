from dataclasses import dataclass, fields


@dataclass
class Listing:
    site: str
    id: str
    url: str
    title: str
    price_pln: int | None = None
    area_m2: float | None = None
    rooms: int | None = None
    district: str | None = None
    street: str | None = None
    house: str | None = None  # building number from the text ("16", "92A"); the flat number is dropped
    photo: str | None = None
    lat: float | None = None
    lon: float | None = None
    geo: str | None = None  # how trustworthy lat/lon are, see GEO_LEVELS
    city: str | None = None
    posted_at: str | None = None
    first_seen: str = ""
    last_seen: str = ""


FIELDS = [f.name for f in fields(Listing)]

# Precision of a location, worst to best. "area": a circle of several km that OLX blurs on purpose; "street": the middle of the
# street; "point": the pin OLX shows (sometimes only street-level, in business listings off by km); "address": a building
# geocoded from a house number the seller wrote in the text, which beats OLX's own pin when both exist.
GEO_LEVELS = ("area", "street", "point", "address")


def geo_rank(geo: str | None) -> int:
    return GEO_LEVELS.index(geo) if geo in GEO_LEVELS else -1
