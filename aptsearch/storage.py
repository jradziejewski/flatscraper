import csv
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import FIELDS, Listing

Key = tuple[str, str]

STALE_AFTER = timedelta(days=3)
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime(TS_FORMAT)


def _opt(value: str, cast):
    return cast(value) if value != "" else None


def load_listings(path: str | Path = "listings.csv") -> dict[Key, Listing]:
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[Key, Listing] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            lst = Listing(
                site=row["site"],
                id=row["id"],
                url=row["url"],
                title=row["title"],
                price_pln=_opt(row["price_pln"], int),
                area_m2=_opt(row["area_m2"], float),
                rooms=_opt(row["rooms"], int),
                district=row["district"] or None,
                street=row.get("street") or None,  # CSVs written before this column existed
                photo=row.get("photo") or None,  # same for photo
                lat=_opt(row.get("lat") or "", float),  # and for the location columns
                lon=_opt(row.get("lon") or "", float),
                geo=row.get("geo") or None,
                city=row["city"] or None,
                posted_at=row["posted_at"] or None,
                first_seen=row["first_seen"],
                last_seen=row["last_seen"],
            )
            out[(lst.site, lst.id)] = lst
    return out


def merge(
    existing: dict[Key, Listing], scraped: list[Listing], now: datetime
) -> dict[Key, Listing]:
    """New rows get first_seen=now; known rows update price_pln, district, street, photo, location and last_seen.
    A None price, district, street, photo or location never overwrites a known one. Rows are never deleted.
    lat, lon and geo move together: they only mean something as a set."""
    ts = utc_iso(now)
    result = dict(existing)
    for lst in scraped:
        key = (lst.site, lst.id)
        old = result.get(key)
        if old is None:
            result[key] = replace(lst, first_seen=ts, last_seen=ts)
        else:
            price = lst.price_pln if lst.price_pln is not None else old.price_pln
            district = lst.district if lst.district is not None else old.district
            street = lst.street if lst.street is not None else old.street
            photo = lst.photo if lst.photo is not None else old.photo
            where = (lst.lat, lst.lon, lst.geo) if lst.lat is not None and lst.lon is not None else (old.lat, old.lon, old.geo)
            result[key] = replace(
                old, price_pln=price, district=district, street=street, photo=photo,
                lat=where[0], lon=where[1], geo=where[2], last_seen=ts,
            )
    return result


def save_listings(listings: dict[Key, Listing], path: str | Path = "listings.csv") -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for key in sorted(listings):
            row = asdict(listings[key])
            writer.writerow({k: "" if v is None else v for k, v in row.items()})


def prune(listings: dict[Key, Listing], now: datetime) -> dict[Key, Listing]:
    """Drop rows not seen for STALE_AFTER. A row whose last_seen cannot be parsed is kept."""
    cutoff = now.astimezone(timezone.utc).replace(microsecond=0) - STALE_AFTER
    kept = {}
    for key, listing in listings.items():
        try:
            seen = datetime.strptime(listing.last_seen, TS_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            kept[key] = listing
            continue
        if seen >= cutoff:
            kept[key] = listing
    return kept


def save_snapshot(
    listings: dict[Key, Listing], now: datetime, archive_dir: str | Path = "archive"
) -> Path:
    """Write one immutable archive file for a run: archive/YYYY-MM/YYYY-MM-DDTHH-MM-SSZ.csv."""
    ts = utc_iso(now)
    path = Path(archive_dir) / ts[:7] / f"{ts.replace(':', '-')}.csv"
    if path.exists():
        raise FileExistsError(f"snapshot already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_listings(listings, path)
    return path
