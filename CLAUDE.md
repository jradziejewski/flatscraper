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
  `site, id, url, title, price_pln, area_m2, rooms, district, street, photo, city, posted_at, first_seen, last_seen`.
  `street` is the street name without "ul."/"al." (`text.clean_street`), empty where a site gives none (OLX).
  `photo` is the URL of the first list thumbnail, taken from the list data each scraper already fetches (no extra
  requests); only http(s) URLs are kept (`text.photo_url`), empty where a site sends none.
  `load_listings` still reads older CSVs that lack the `street` or `photo` column.
- Results merge into `listings.csv`, keyed by `(site, id)`:
  - new rows get `first_seen=now`
  - existing rows update `price_pln`, `district`, `street`, `photo` and `last_seen`; an empty price, district, street or photo never overwrites a known one
  - `merge()` itself never deletes rows
- Every run that finds listings not yet in `listings.csv` writes an immutable snapshot of just those new
  listings to `archive/YYYY-MM/YYYY-MM-DDTHH-MM-SSZ.csv` (same columns and sort order as `listings.csv`).
  A run with no new listings writes no snapshot.
- `listings.csv` is the current view: after merging, rows not seen for 3 days (`STALE_AFTER` in `storage.py`)
  are removed; they stay in the snapshots of the runs that saw them. A run that finds nothing
  (all scrapers failed) writes no snapshot and prunes nothing.
- `district` is always one of the city's official districts, or empty. `aptsearch/districts.py` maps neighbourhoods
  (nieruchomosci-online reports those), spelling variants and small typos to the 5 official Wrocław districts
  (`normalize_district`, applied in `main.run` to scraped listings and to the rows already in `listings.csv`).
  A name it cannot match becomes empty and is logged as "Unknown district names", so add it to the table. Only
  Wrocław has a table; other cities keep the site's text. The table was built from Morizon/Otodom data and
  cross-checked against pl.wikipedia "Podział administracyjny Wrocławia".
- Filters live in `config.yaml` (`city`, `max_price`, `min_area`, `min_rooms`, `districts`). Only matching listings are saved.
- A GitHub Actions workflow runs on cron (e.g. 07:00, 13:00, 19:00 Europe/Warsaw) and commits `listings.csv` and `archive/` only if they changed (`permissions: contents: write`).

## Scraping rules

- Be polite: realistic User-Agent, small delays, cap the number of pages per run.
- Prefer embedded JSON (e.g. `__NEXT_DATA__`) or JSON APIs over HTML parsing where sites have them.
- One failing site must not break the run: log and continue.

## Viewing the listings

- `index.html` + `app.js` is a static, Polish-language page served by GitHub Pages: search, filters, sort, "NOWE" badge for listings first seen in the last 24 h. Desktop is a clean list (photo, title with a muted
  "district · street · added" line and the portals, price with zł/m², m², rooms, ★ ✕); phones get cards with the photo on top.
  The toolbar (search, "Filtry" button with an active-filter count, sort, Aktualne/Ulubione/Ukryte tabs) is always visible and sticky
  on desktop; the other filters live in a panel (a dropdown on desktop, a bottom sheet on phones), and active filters show as
  removable chips under the toolbar. No web fonts or libraries; light and dark theme follow the system. Live at https://jradziejewski.github.io/flatscraper/.
- **Duplicates:** the same flat is often listed on several portals, so the page merges those rows ("Łącz duplikaty", on by default; off shows every listing). Strict rule (`groupDuplicates` in `app.js`): same price, area (rounded to a whole m²), rooms and district on different portals, plus evidence of the same place, which means the same street (one name's distinctive words contained in the other's, so "Sikorskiego" matches "gen. Władysława Sikorskiego"). A row without a street (OLX) matches only if its title or URL names the other row's street. Different streets never merge, two listings from one portal are never combined, and a merged row links to every portal's listing. Nothing is removed from the data; merging is display only. It is the only place that decides what counts as the same flat, so alerts or exports would need the same rule.
- **Photos:** `photoCell` in `app.js`. 180×135 on desktop; full-width 3:2 (max 230 px) at the top of the phone card, with NOWE and ★ ✕
  overlaid. `photoSources` gives each `<img>` a `srcset` with a larger variant for Otodom, OLX and Morizon (same CDN path, bigger size
  token), so phones fetch a sharp image and desktop the small one; nieruchomosci has one size. The image is lazy-loaded, sent with
  `referrerpolicy="no-referrer"`, and its `src`/`srcset` only come from `photoSources` (which runs `safeUrl`). The box has a fixed
  size, so a missing or dead image (portals rotate CDN URLs) leaves a plain placeholder and the title link keeps working; if only the
  larger variant fails, the stored one is tried first. A merged row shows the picture of the first portal (by priority) that has one.
  Favourites keep the photo URL in their snapshot. Scraped thumbnails: Otodom `images[0].small`, Morizon `photos[0]` built into its
  `3x2_s` thumb URL, nieruchomosci ld+json `image`, OLX `photos[0].link` with `{width}x{height}` = 320x240. The page hot-links the
  portals' CDNs; nothing is copied into this repo.
- **Per-device features** (browser `localStorage`/`sessionStorage`, keys `flatscraper:v1:*`; nothing is sent anywhere and nothing is shared between devices; every access is wrapped in try/catch and falls back to memory when storage is blocked):
  - *New since last visit*: the baseline is the newest `last_seen` in the data she saw last time (not the wall clock, because the raw CSV is cached for ~5 min), fixed for the tab session so a refresh keeps the badges. First visit: falls back to "first seen in the last 24 h".
  - *Remembered filters and sort* (not the search text; a remembered "Ukryte" view is not restored). Values are sanitized against the current data on load.
  - *Favourites (★) and hide (✕)*: apply to every listing of a merged row. A favourite keeps a snapshot, so it stays in "Ulubione" (greyed, "ostatnio widziano: …") after the listing leaves the CSV. Hiding offers a short "Cofnij" undo. Capped at 2000 marks.
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

