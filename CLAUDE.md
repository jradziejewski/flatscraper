# Apartment search helper

Scrapes Polish rental listings into `listings.csv` in this repo. Runs on GitHub Actions cron.

Keep it minimal: no server, no database, no UI.

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
  - rows are never deleted
- Filters live in `config.yaml` (`city`, `max_price`, `min_area`, `min_rooms`, `districts`). Only matching listings are saved.
- A GitHub Actions workflow runs on cron (e.g. 07:00, 13:00, 19:00 Europe/Warsaw) and commits `listings.csv` only if it changed (`permissions: contents: write`).

## Scraping rules

- Be polite: realistic User-Agent, small delays, cap the number of pages per run.
- Prefer embedded JSON (e.g. `__NEXT_DATA__`) or JSON APIs over HTML parsing where sites have them.
- One failing site must not break the run: log and continue.

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
- Filtering happens before merging, so a saved listing whose price rises above `max_price` stops getting `price_pln` and `last_seen` updates.

## Commands

```bash
uv sync
uv run pytest
uv run python main.py
```

