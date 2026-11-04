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
    photo: str | None = None
    lat: float | None = None
    lon: float | None = None
    geo: str | None = None  # how trustworthy lat/lon are: "point" (exact), "street" (middle of the street), "area" (blurred)
    city: str | None = None
    posted_at: str | None = None
    first_seen: str = ""
    last_seen: str = ""


FIELDS = [f.name for f in fields(Listing)]
