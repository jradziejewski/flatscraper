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
    city: str | None = None
    posted_at: str | None = None
    first_seen: str = ""
    last_seen: str = ""


FIELDS = [f.name for f in fields(Listing)]
