import logging
from datetime import datetime, timezone
from pathlib import Path

from aptsearch.config import load_config, matches
from aptsearch.scrapers import SCRAPERS
from aptsearch.storage import load_listings, merge, prune, save_listings, save_snapshot

log = logging.getLogger("aptsearch")


def run(scrapers=SCRAPERS, config_path="config.yaml", csv_path="listings.csv", now=None, archive_dir=None):
    cfg = load_config(config_path)
    now = now or datetime.now(timezone.utc)
    found = []
    for scraper in scrapers:
        try:
            found.extend(scraper(cfg))
        except Exception:
            log.exception("Scraper %s failed", getattr(scraper, "__name__", scraper))
    found = [l for l in found if matches(l, cfg)]
    merged = merge(load_listings(csv_path), found, now)
    pruned = 0
    if found:  # on a total outage keep everything: no empty snapshot, no pruning
        keys = {(l.site, l.id) for l in found}
        snapshot = save_snapshot(
            {k: merged[k] for k in keys}, now, archive_dir or Path(csv_path).parent / "archive"
        )
        before = len(merged)
        merged = prune(merged, now)
        pruned = before - len(merged)
        log.info("Archived %d listings to %s", len(keys), snapshot)
    save_listings(merged, csv_path)
    log.info("Saved %d rows (%d matches this run, %d pruned)", len(merged), len(found), pruned)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
