# Apartment search helper

Scrapes Polish rental listings into `listings.csv` in this repo. Runs on GitHub Actions cron.

Keep it minimal: no server, no database. The only UI is one static page (`index.html` + `app.js`): no build step, no dependencies.

## Working agreement

We build incrementally, one step at a time. Do only the step asked for, then stop and wait.
For a new step, show the file structure first, then implement.

## Final shape

- Python 3.12. Deps managed with uv (or pip + requirements.txt).
- Scrapers for otodom.pl, morizon.pl, olx.pl and nieruchomosci-online.pl. Poland, rentals only; city and filters come from config.
- Every scraper returns the same `Listing` dataclass:
  `site, id, url, title, price_pln, area_m2, rooms, district, city, posted_at, first_seen, last_seen`.
- Results merge into `listings.csv`, keyed by `(site, id)`:
  - new rows get `first_seen=now`
  - existing rows update `price_pln` and `last_seen`
  - `merge()` itself never deletes rows
- Every run that finds listings not yet in `listings.csv` writes an immutable snapshot of just those new
  listings to `archive/YYYY-MM/YYYY-MM-DDTHH-MM-SSZ.csv` (same columns and sort order as `listings.csv`).
  A run with no new listings writes no snapshot.
- `listings.csv` is the current view: after merging, rows not seen for 3 days (`STALE_AFTER` in `storage.py`)
  are removed; they stay in the snapshots of the runs that saw them. A run that finds nothing
  (all scrapers failed) writes no snapshot and prunes nothing.
- Filters live in `config.yaml` (`city`, `max_price`, `min_area`, `min_rooms`, `districts`). Only matching listings are saved.
- A GitHub Actions workflow runs on cron (e.g. 07:00, 13:00, 19:00 Europe/Warsaw) and commits `listings.csv` and `archive/` only if they changed (`permissions: contents: write`).

## Scraping rules

- Be polite: realistic User-Agent, small delays, cap the number of pages per run.
- Prefer embedded JSON (e.g. `__NEXT_DATA__`) or JSON APIs over HTML parsing where sites have them.
- One failing site must not break the run: log and continue.

## Viewing the listings

- `index.html` + `app.js` is a static, Polish-language page served by GitHub Pages: search, filters, sortable columns, "NOWE" badge for listings first seen in the last 24 h, card layout on phones. Live at https://jradziejewski.github.io/flatscraper/.
- It fetches `listings.csv` from `raw.githubusercontent.com` (CORS-enabled, cached ~5 min) and falls back to the relative `listings.csv`. So it never needs redeploying when the bot commits new data.
- One-time setup: repo Settings → Pages → Source "Deploy from a branch" → `main`, folder `/ (root)`. `.nojekyll` skips the Jekyll build.
- Listing fields come from scraped sites, so `app.js` fills the page with `textContent` only (never `innerHTML`) and links only `http(s)` URLs. A test enforces this.
- Local preview: `python -m http.server`, then open http://localhost:8000. Page tests (`tests/test_site.py`) use `node` and are skipped when it is missing.

## Step 1 (current)

Project skeleton, `config.yaml`, `Listing` model, CSV merge logic with a small test, and a `main.py` that runs a list of scrapers (empty for now). No scrapers and no workflow yet.

Planned layout:

```
.
├── pyproject.toml
├── .python-version
├── .gitignore
├── config.yaml
├── main.py                 # runs scrapers, filters, merges, saves
├── listings.csv            # generated, committed
├── aptsearch/
│   ├── __init__.py
│   ├── config.py           # Config dataclass, load_config(), matches()
│   ├── models.py           # Listing dataclass
│   ├── storage.py          # load_listings(), merge(), save_listings()
│   └── scrapers/
│       └── __init__.py     # SCRAPERS = []
└── tests/
    ├── test_storage.py
    └── test_main.py
```

## Implementation defaults

- A scraper is a plain function taking the config and returning `list[Listing]`.
- `merge()` takes `now` as an argument so tests stay deterministic. Timestamps are UTC ISO 8601 at second precision.
- `listings.csv`: stdlib `csv`, UTF-8, `\n` line endings, rows sorted by `(site, id)` so diffs stay small. Saving unchanged data must produce identical bytes.
- A `None` price from a scraper must not overwrite a known price.
- Filters reject only on known values: a listing with a missing area, rooms, price or district passes.
- Empty `districts` means all districts. Text comparisons are case-insensitive.
- Unknown keys in `config.yaml` should fail loudly, so typos don't silently disable a filter.

## Known consequences of the spec

- `last_seen` changes on every successful run, so "commit only if changed" will commit nearly every run.
- Filtering happens before merging, so a saved listing whose price rises above `max_price` stops getting `price_pln` and `last_seen` updates, and is removed from `listings.csv` after 3 days.
- A listing that comes back after being pruned is treated as new: its `first_seen` resets and it is archived again. The earlier `first_seen` survives only in old snapshots.
- Each site is fetched only to a page cap, so a still-live listing can fall out of the window and be pruned after 3 days.
- Snapshots hold only new listings, so they stay small (the first run after a fresh start archives everything), at up to ~3 files a day.

## Commands

```bash
uv sync
uv run pytest
uv run python main.py
```

