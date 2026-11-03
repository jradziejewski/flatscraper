import logging
from datetime import datetime, timezone

from aptsearch.config import load_config, matches
from aptsearch.scrapers import SCRAPERS
from aptsearch.storage import load_listings, merge, save_listings

log = logging.getLogger("aptsearch")


def run(scrapers=SCRAPERS, config_path="config.yaml", csv_path="listings.csv", now=None):
    cfg = load_config(config_path)
    now = now or datetime.now(timezone.utc)
    found = []
    for scraper in scrapers:
        try:
            found.extend(scraper(cfg))
        except Exception:
            log.exception("Scraper %s failed", getattr(scraper, "__name__", scraper))
    found = [l for l in found if matches(l, cfg)]
    save_listings(merge(load_listings(csv_path), found, now), csv_path)
    log.info("Saved %d new/updated matches", len(found))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
