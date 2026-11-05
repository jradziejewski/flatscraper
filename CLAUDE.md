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
  `site, id, url, title, price_pln, area_m2, rooms, district, street, house, photo, lat, lon, geo, city, posted_at, first_seen, last_seen`.
  `street` is the street name without "ul."/"al." (`text.clean_street`), from the portal's own field, or from the listing's text where the portal
  gives none (OLX never does): see "Street and house number from the text" below. `house` is the building number ("16", "92A"), only ever from text.
  `photo` is the URL of the first list thumbnail, taken from the list data each scraper already fetches (no extra
  requests); only http(s) URLs are kept (`text.photo_url`), empty where a site sends none.
  `lat`, `lon` and `geo` are a location with its precision, worst to best (`GEO_LEVELS` in `models.py`): `area` (OLX blurs most offers to a circle
  of several km), `street` (the middle of the street), `point` (the pin OLX shows when it has `show_detailed` and radius 0; in business listings it was
  off by km), `address` (a building geocoded from a house number the seller wrote in the text; it beats OLX's own pin).
  `load_listings` still reads older CSVs that lack the `street`, `house`, `photo` or location columns.
- Results merge into `listings.csv`, keyed by `(site, id)`:
  - new rows get `first_seen=now`
  - existing rows update `price_pln`, `district`, `street`, `house`, `photo`, the location (`lat`, `lon`, `geo` move together) and `last_seen`; an empty price, district, street, house, photo or location never overwrites a known one, and a location is only replaced by an equally or more precise one (OLX's blurred area must not undo a building)
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
- **Locations:** OLX sends coordinates (`scrapers/olx.py`). For the other portals `aptsearch/geocode.py` turns the street into a point through
  OpenStreetMap's Nominatim, after the merge and before the snapshot: at most 40 lookups per run, one per second, with a User-Agent that names the
  repo (no personal address), hits outside the city's bounding box (`CITY_BOXES`, Wrocław only) rejected, and every answer, including "not found"
  (`null`), cached in `geocache.json` (committed, so a street is looked up once). Geocoding is a nicety: a failure is logged and the run goes on.
  A row with a house number gets an address lookup (`"Street 16, City"`), accepted only if the answer carries that house number on a road with the
  street's name (Nominatim otherwise quietly answers with the street itself); no building found falls back to the street point.
  `python -m aptsearch.geocode` fills the whole `listings.csv` in one go (the backfill). Street coordinates derive from OpenStreetMap data,
  © OpenStreetMap contributors, ODbL.
- **Street and house number from the text** (`aptsearch/address.py`, `streets.py`, `data/streets_wroclaw.json`): OLX sends the full description on every
  offer, Morizon the first ~300 characters, Otodom and nieruchomosci only the title in the list. `find_address` needs a cue word (`ul.`, `ulicy`, `al.`,
  `plac`...) right before a name that is in the city's street list (2,571 Wrocław names from one Overpass query: `python -m aptsearch.streets`), matched by
  word stem so inflected forms work ("Białowieskiej" gives "Białowieska"; the geocoder only understands the base form). It skips a street named after
  blisko/obok/w pobliżu (a neighbour), the city's own name, a square or estate not introduced by przy/na and without a number (landmark lists), and numbers
  that are rooms, areas or ranges. A title starting with "<Street> <number>" needs no cue but must be in the base form. `resolve()`: the portal's street
  always wins and the text only adds the number; when the portal has none, the text's street fills the gap. Only Wrocław has a list. A wrong street is worse
  than none (it misleads the map and the duplicate merging), so every rule errs towards finding nothing.
  **Listing pages** (`aptsearch/details.py`): rows still without a street get their own page read once (Otodom `ad.title`/`description`, Morizon
  `propertyData.description` and the page title, nieruchomosci's description block and a title that is the portal's address line, OLX's single-offer API
  for rows the list no longer returns). Only those fields: pages also hold the agency's office, the portal's company address and nearby parks. The answer,
  including "nothing", is cached in `addresscache.json` (entries of gone listings are dropped); 25 pages per run, 2 s apart, failures counted per portal;
  `python -m aptsearch.details` backfills. Streets in running text near a landmark (school, shop, stop, park), in a contact block (phone label, phone
  number, postal code) or named after "agency" are not the flat's. Bare street names in titles are not trusted (too many ordinary words).
- Filters live in `config.yaml` (`city`, `max_price`, `min_area`, `min_rooms`, `districts`). Only matching listings are saved.
- A GitHub Actions workflow runs on cron (`17 6,12,18 * * *` UTC = 07:17/13:17/19:17 Warsaw in winter; minute 17 on purpose, GitHub drops runs at the top of the hour) and can be started by hand (`workflow_dispatch`). It commits `listings.csv`, `geocache.json`, `addresscache.json` and `archive/` only if they changed (`permissions: contents: write`).

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
- **Duplicates:** the same flat is often listed on several portals, so the page merges those rows ("Łącz duplikaty", on by default; off shows every listing). Strict rule (`groupDuplicates` in `app.js`): same price, area (rounded to a whole m²), rooms and district on different portals, plus evidence of the same place, which means the same street (one name's distinctive words contained in the other's, so "Sikorskiego" matches "gen. Władysława Sikorskiego"). A row without a street matches only if its title or URL names the other row's street (OLX rows now mostly have a text-derived street and merge by the normal rule). Different streets never merge, two listings from one portal are never combined, and a merged row links to every portal's listing. Nothing is removed from the data; merging is display only. It is the only place that decides what counts as the same flat, so alerts or exports would need the same rule.
- **Opened listings:** a title or portal chip you have already opened turns grey (`a.title-link:visited`, `a.chip:visited`, token `--visited`).
  It is pure CSS, so it uses the browser's history: it also counts visits made on the portal itself, nothing is stored by the page, and
  it resets with the browser history. Browsers only allow colour changes on `:visited` and keep it unreadable from scripts, so there is no
  "viewed" tag or filter. Keep the `:visited` rules before `:hover`.
- **Photos:** `photoCell` in `app.js`. 180×135 on desktop; full-width 3:2 (max 230 px) at the top of the phone card, with NOWE and ★ ✕
  overlaid. `photoSources` gives each `<img>` a `srcset` with a larger variant for Otodom, OLX and Morizon (same CDN path, bigger size
  token), so phones fetch a sharp image and desktop the small one; nieruchomosci has one size. The image is lazy-loaded, sent with
  `referrerpolicy="no-referrer"`, and its `src`/`srcset` only come from `photoSources` (which runs `safeUrl`). The box has a fixed
  size, so a missing or dead image (portals rotate CDN URLs) leaves a plain placeholder and the title link keeps working; if only the
  larger variant fails, the stored one is tried first. A merged row shows the picture of the first portal (by priority) that has one.
  Favourites keep the photo URL in their snapshot. Scraped thumbnails: Otodom `images[0].small`, Morizon `photos[0]` built into its
  `3x2_s` thumb URL, nieruchomosci ld+json `image`, OLX `photos[0].link` with `{width}x{height}` = 320x240. The page hot-links the
  portals' CDNs; nothing is copied into this repo.
- **Map:** a "Mapa ▾" button next to the portals opens one map at a time as an extra row under the listing (the bottom of the card on phones),
  only for rows that have a location; a merged row uses its most precise one (`bestLocation`: address > point > street > area). It is an OpenStreetMap
  embed iframe built by `mapEmbedUrl` from numbers only (a building gets a close window), created only when opened (nothing is requested from OSM before that), with
  `sandbox="allow-scripts allow-same-origin"`. The caption says how exact the pin is, an `area` gets a wide view and no pin, and links open the
  place in Google Maps or OSM. Do not set `referrerpolicy="no-referrer"` on it: OSM's tile servers answer 403 "Access blocked" without a Referer.
  The embed page is MapLibre/WebGL, which headless Firefox without a GPU cannot draw (the pin, buttons and attribution still show).
- **Per-device features** (browser `localStorage`/`sessionStorage`, keys `flatscraper:v1:*`; nothing is sent anywhere and nothing is shared between devices; every access is wrapped in try/catch and falls back to memory when storage is blocked):
  - *New since last visit*: the baseline is the newest `last_seen` in the data she saw last time (not the wall clock, because the raw CSV is cached for ~5 min), fixed for the tab session so a refresh keeps the badges. First visit: falls back to "first seen in the last 24 h".
  - *Remembered filters and sort* (not the search text; a remembered "Ukryte" view is not restored). Values are sanitized against the current data on load.
  - *Favourites (★) and hide (✕)*: apply to every listing of a merged row. A favourite keeps a snapshot, so it stays in "Ulubione" (greyed, "ostatnio widziano: …") after the listing leaves the CSV. Hiding offers a short "Cofnij" undo. Capped at 2000 marks.
- **Manual update ("Aktualizuj teraz"):** the button under the title starts the `scrape` workflow from the page (GitHub's cron is unreliable). A static page cannot hold a secret, so the owner
  creates a **fine-grained token** (only this repo, permission *Actions: Read and write*) and pastes it once per device; it is kept in `localStorage` (`flatscraper:v1:ghToken`), checked by
  `cleanToken` (`github_pat_`/`ghp_` shapes only) and sent only to `api.github.com`; "Zapomnij token" removes it. Flow in `app.js`: `dispatchRequest` (POST `.../workflows/scrape.yml/dispatches`,
  `{"ref":"main"}`, 204 and no run id) -> poll `runsUrl` every 10 s with `pickRun` (newest `workflow_dispatch` run created after the click, 60 s clock slack) and `runPhase` for the status text
  -> on success read the head commit of `main` and load `freshCsvUrl(sha)` (a commit-pinned raw URL, so no 5 min cache) -> `setData`, which keeps the current filters. A 401/403/404 forgets the token and
  reopens the form. Polling uses the token too (unauthenticated calls are capped at 60/h). Anyone with the token can only start scrapes. The workflow's `concurrency` group queues double clicks.
- It fetches `listings.csv` from `raw.githubusercontent.com` (CORS-enabled, cached ~5 min) and falls back to the relative `listings.csv`. So it never needs redeploying when the bot commits new data.
- One-time setup: repo Settings → Pages → Source "Deploy from a branch" → `main`, folder `/ (root)`. `.nojekyll` skips the Jekyll build.
- Listing fields come from scraped sites, so `app.js` fills the page with `textContent` only (never `innerHTML`) and links only `http(s)` URLs. A test enforces this.
- Local preview: `python -m http.server`, then open http://localhost:8000. Page tests (`tests/test_site.py`) use `node` and are skipped when it is missing.

## Layout

```
.
├── main.py                 # run(): scrapers -> districts -> filter -> merge -> listing pages -> geocode -> snapshot -> prune -> save
├── config.yaml  pyproject.toml  .python-version
├── listings.csv            # generated, committed (the current view)
├── geocache.json           # generated, committed: street/building -> coordinates (or null)
├── addresscache.json       # generated, committed: listing page already read -> street+house (or null)
├── archive/YYYY-MM/*.csv   # immutable snapshots of listings new in a run
├── index.html  app.js      # the static page
├── aptsearch/
│   ├── models.py config.py storage.py districts.py text.py http.py
│   ├── streets.py address.py details.py geocode.py
│   ├── data/streets_wroclaw.json
│   └── scrapers/           # otodom, morizon, olx, nieruchomosci (SCRAPERS in __init__.py)
├── tests/                  # pytest; test_site.py drives app.js through node; fixtures/ are trimmed real responses
└── .github/workflows/scrape.yml
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
# one-off backfills (rate-limited, each rewrites listings.csv and its cache)
uv run python -m aptsearch.streets    # refresh the street list from OSM
uv run python -m aptsearch.geocode    # coordinates for every street / building
uv run python -m aptsearch.details    # read the listing page of rows still without a street
```

`PROGRESS.md` holds the current state, the open issue (the scheduled run does not fire), measurements and the backlog: read it first.

