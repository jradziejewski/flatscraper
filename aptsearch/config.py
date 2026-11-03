from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .models import Listing


@dataclass
class Config:
    city: str
    max_price: int | None = None
    min_area: float | None = None
    min_rooms: int | None = None
    districts: list[str] = field(default_factory=list)


def load_config(path: str | Path = "config.yaml") -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")
    if "city" not in data:
        raise ValueError("Config is missing required key: city")
    data["districts"] = data.get("districts") or []
    return Config(**data)


def matches(listing: Listing, cfg: Config) -> bool:
    """Reject only on known values; missing values pass."""
    if listing.city and listing.city.casefold() != cfg.city.casefold():
        return False
    if (
        cfg.max_price is not None
        and listing.price_pln is not None
        and listing.price_pln > cfg.max_price
    ):
        return False
    if (
        cfg.min_area is not None
        and listing.area_m2 is not None
        and listing.area_m2 < cfg.min_area
    ):
        return False
    if (
        cfg.min_rooms is not None
        and listing.rooms is not None
        and listing.rooms < cfg.min_rooms
    ):
        return False
    if cfg.districts and listing.district:
        wanted = {d.casefold() for d in cfg.districts}
        if listing.district.casefold() not in wanted:
            return False
    return True
