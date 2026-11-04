import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from aptsearch.config import load_config, matches
from aptsearch import details
from aptsearch.districts import normalize_district
from aptsearch.geocode import fill_street_coordinates, load_cache, save_cache
from aptsearch.scrapers import SCRAPERS
from aptsearch.storage import load_listings, merge, prune, save_listings, save_snapshot

log = logging.getLogger("aptsearch")


def with_street_coordinates(listings, default_city, cache_path):
    """Add street-level coordinates (cached in geocache.json). A nicety: any failure is logged and the run goes on."""
    try:
        cache = load_cache(cache_path)
        before = dict(cache)
        listings = fill_street_coordinates(listings, cache, default_city=default_city)
        if cache != before:
            save_cache(cache, cache_path)
    except Exception:
        log.exception("Geocoding failed; continuing without new coordinates")
    return listings


def with_page_addresses(listings, default_city, cache_path, skip):
    """Street and house number from the listing's own page for rows still without a street. A nicety like geocoding: any failure
    is logged and the run goes on. `skip`: rows whose full text the list already gave this run (OLX)."""
    try:
        cache = details.load_cache(cache_path)
        before = dict(cache)
        listings = details.fill_addresses(listings, cache, skip=skip, default_city=default_city)
        if cache != before:
            details.save_cache(cache, cache_path)
    except Exception:
        log.exception("Reading listing pages failed; continuing without them")
    return listings


def run(scrapers=SCRAPERS, config_path="config.yaml", csv_path="listings.csv", now=None, archive_dir=None):
    cfg = load_config(config_path)
    now = now or datetime.now(timezone.utc)
    found = []
    for scraper in scrapers:
        try:
            found.extend(scraper(cfg))
        except Exception:
            log.exception("Scraper %s failed", getattr(scraper, "__name__", scraper))
    unknown: set[str] = set()

    def with_official_district(listing):
        district = normalize_district(listing.district, listing.city or cfg.city)
        if listing.district and district is None:
            unknown.add(listing.district)
        return replace(listing, district=district)

    found = [with_official_district(l) for l in found]
    found = [l for l in found if matches(l, cfg)]
    existing = {k: with_official_district(l) for k, l in load_listings(csv_path).items()}
    merged = merge(existing, found, now)
    olx_seen_now = {(l.site, l.id) for l in found if l.site == "olx"}  # the list gave their full text: no page needed
    merged = with_page_addresses(merged, cfg.city, Path(csv_path).parent / "addresscache.json", olx_seen_now)
    merged = with_street_coordinates(merged, cfg.city, Path(csv_path).parent / "geocache.json")
    pruned = 0
    if found:  # on a total outage keep everything: no snapshot, no pruning
        new_keys = {(l.site, l.id) for l in found} - set(existing)
        if new_keys:  # the archive holds only listings first seen in this run
            snapshot = save_snapshot(
                {k: merged[k] for k in new_keys}, now, archive_dir or Path(csv_path).parent / "archive"
            )
            log.info("Archived %d new listings to %s", len(new_keys), snapshot)
        before = len(merged)
        merged = prune(merged, now)
        pruned = before - len(merged)
    if unknown:
        log.warning("Unknown district names (add them to aptsearch/districts.py): %s", sorted(unknown))
    save_listings(merged, csv_path)
    log.info("Saved %d rows (%d matches this run, %d pruned)", len(merged), len(found), pruned)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
