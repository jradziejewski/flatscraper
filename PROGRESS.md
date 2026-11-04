# Progress and handoff notes

Read this first in a new session, then `CLAUDE.md` (the spec and rules). Last updated 2026-11-04, after Step 12b.

## What this is
A Polish rental-listings scraper for Wrocław. It runs on GitHub Actions three times a day, writes `listings.csv`
(and small per-run archive snapshots), and a static page shows the results.
- Repo: `jradziejewski/flatscraper` (public). Page: https://jradziejewski.github.io/flatscraper/ (GitHub Pages, branch `main`, folder `/`).
- Config: `config.yaml` = Wrocław, max 5000 zł, min 30 m², min 2 rooms, all districts.

## Status: everything below is built and tested. Steps 1-12 are pushed; Step 12b (revamp) is committed locally, **not pushed yet**
| Step | What |
|---|---|
| 1 | Skeleton, `Listing` model, CSV merge, config with filters |
| 2-5 | Scrapers: Otodom (`__NEXT_DATA__`), nieruchomosci-online (ld+json + tiles), Morizon (Nuxt `__NUXT_DATA__`), OLX (JSON API, fetched via system `curl`) |
| 6 | GitHub Actions workflow: cron `0 6,12,18 * * *` UTC (+ manual `workflow_dispatch`), commits `listings.csv` and `archive/` |
| 7 | Archive: one snapshot per run holding only listings **new in that run**; listings not seen for 3 days are pruned from `listings.csv` |
| 8 | Static Polish page (`index.html` + `app.js`), no dependencies |
| 9 | District normalization to the 5 official districts (`aptsearch/districts.py`) |
| 10 | `street` column + duplicate merging across portals on the page (Strict) |
| 11 | Per-device page features: new since last visit, remembered filters, favourites/hide with undo |
| 12 | `photo` column (thumbnail URL from each portal's list data) + thumbnails on the page |
| 12b | Page revamp: toolbar + filter panel/bottom sheet + removable filter chips, clean desktop list, phone cards with the photo on top, design tokens, larger-photo `srcset`; desktop thumbnails 180x135 (`.thumb` in `index.html` and `PHOTO_SIZES` in `app.js` must agree, a test checks it) |

**Not yet seen live:** the Actions bot has not run since the pushes of Steps 10 and 11, so the CSV on GitHub still has no `street`
column and the page shows no merged duplicates yet. After the next run (06:00/12:00/18:00 UTC, or run it from Actions → scrape →
Run workflow) check: the CSV header has `street` (and `photo` once Step 12 is pushed), the count line says "połączono N duplikatów",
the thumbnails show, and the Actions log has no new "Unknown district names" warning. The first run after an upgrade rewrites the
whole CSV once (big diff, expected).

## Map of the code
```
main.py                  run(): scrapers -> normalize districts -> filter -> merge -> snapshot new -> prune -> save
aptsearch/models.py      Listing (site,id,url,title,price_pln,area_m2,rooms,district,street,photo,city,posted_at,first_seen,last_seen)
aptsearch/config.py      Config, load_config() (unknown keys fail), matches() (missing values pass)
aptsearch/storage.py     load/merge/save, prune (STALE_AFTER 3 days), save_snapshot; reads old CSVs without the street/photo columns
aptsearch/districts.py   WROCLAW table (59 neighbourhoods) + normalize_district(); unknown -> None, logged by main.py
aptsearch/text.py        city_slug, clean_street, photo_url (http(s) only)
aptsearch/http.py        get() (urllib, browser UA) and get_curl() (OLX only: urllib gets 403)
aptsearch/scrapers/      otodom, nieruchomosci, morizon, olx; SCRAPERS list in __init__.py
index.html, app.js       the page; pure functions exported for node tests, DOM code only in a browser
tests/                   173 tests (pytest); test_site.py drives app.js through node
.github/workflows/scrape.yml
archive/YYYY-MM/*.csv    immutable snapshots of new listings
```

## Decisions that are not obvious from the code
- **Price** is the base rent. The extra monthly fee (czynsz) is *not* in `price_pln`. The same flat's price matched exactly across
  portals in 108 groups, so all portals report base rent.
- **Districts:** only Otodom/Morizon/OLX give the 5 official districts; nieruchomosci gives neighbourhoods. The table was built from
  Morizon/Otodom data (no conflicts) and cross-checked with pl.wikipedia "Podział administracyjny Wrocławia". Unmatched names become
  empty ("Brak dzielnicy") and are logged. Typo repair is deliberately strict (difflib cutoff 0.85: fixes "Krzyk", not "Krzyky").
- **`merge()`** updates `price_pln`, `district`, `street`, `photo`, `last_seen`; an empty value never overwrites a known one; rows are never
  deleted by `merge()` (pruning is a separate step and is skipped when a run finds nothing at all).
- **Duplicate merging is display-only, in `app.js`** (`groupDuplicates`). Strict rule: same price, area rounded to whole m², rooms,
  district, on different portals, AND the same street (distinctive words of one name contained in the other's); a street-less row
  (OLX) needs the street named in its title/URL. Different streets never merge; two listings from one portal never merge.
  My estimate: spec-only matching would produce ~1 in 6 false merges, which is why street evidence is required.
- **"NOWE" baseline** = newest `last_seen` in the data she saw last time (not the wall clock; raw GitHub CSV is cached ~5 min).
- **Per-device storage** (`localStorage` keys `flatscraper:v1:*`): favourites keep a snapshot so they outlive the listing. Not shared
  between devices (we chose "browse only", no server).
- The page reads the CSV from `raw.githubusercontent.com` (CORS-enabled), so it never needs a redeploy for new data.
- OLX: Python's HTTP client gets 403 (also on its HTML pages), plain `curl` gets 200. We chose `curl` and not browser automation.
  It works from GitHub's runners (verified on the first run). OLX has no street field.
- **Photos (Step 12):** hot-linked from the portals' CDNs, never copied into the repo. Sizes: Otodom `small` 314x236, OLX 320x240 (we
  substitute the `{width}x{height}` placeholders), nieruchomosci ld+json `image`, Morizon `3x2_s` (450x300, 21 KB; `_m` is 600x400, 35 KB,
  not needed at 96x72). Morizon sends photo ids, so its URL is built in `scrapers/morizon.py` (`IMG_BASE`, the same URL its pages use).
  All four CDNs answered `200 image/jpeg` with no Referer, and the `<img>` sends `no-referrer` anyway (checked 2026-11-04). On a live run
  (748 listings) every one had a photo except a single Otodom listing the portal itself sent none for; the 40 rows from before the column
  stay empty until they are seen again.
  A merged row shows the first portal (by priority) that has a picture. A portal may rotate or block CDN URLs later: the `error` handler
  just leaves the grey box, so nothing breaks. CSV grew from ~205 KB to ~325 KB.

- **Revamp (12b) choices:** the user wanted a list on desktop and cards on phones; the pains they named were filters taking too much
  room, key info hard to spot, and a dated look. Filters moved into a panel (dropdown / bottom sheet) behind a "Filtry" button; the
  count badge and chips show what is active. The toolbar is sticky on desktop only (on a phone it is three rows, too tall to pin).
  "Pokoje od" stayed a `<select>` (the plan said segmented buttons; not worth the extra code). `form#filters` is `display: contents`
  so the sticky toolbar spans the whole page. The view (Aktualne/Ulubione/Ukryte) is a radio group, no longer `select#show`.
  Verify with the throwaway preview server: `?do=open|chips|fav` style hooks were injected by the server at `DOMContentLoaded`+1.5 s
  (not `load`: the server holds `load` for 3 s and the screenshot fires on it).

## How we work (user preferences)
- **One step at a time.** Show the file layout first, plan, get approval, implement, then stop and wait. Plan mode is used for this;
  `ExitPlanMode` is the approval point.
- **Commits:** granular, message style `where: what changed` (e.g. `site: add favourites and hide, stored per device`). Generated
  data (`listings.csv`, `archive/`) in its own commits. **Never push unless asked**; only fast-forward pushes, never rewrite pushed history.
- Verify visually: real headless-Firefox screenshots for page changes, and real data for scraper changes, not just unit tests.
- Output style is "Explanatory" (short `★ Insight` blocks).
- The user's own Firefox is running on this machine: never kill it.

## Running things locally
- `uv` is **not installed**. Use a venv: `python3 -m venv <dir> && <dir>/bin/pip install pyyaml pytest`, then `<dir>/bin/python -m pytest -q`
  and `<dir>/bin/python main.py` (a live run: ~45 s, hits all four sites, rewrites `listings.csv`, may add an `archive/` file).
- `node` is available (page tests); `gh` CLI is not (so no workflow runs from here; use the GitHub web UI).
- **Headless Firefox is a snap:** it cannot read `/tmp`. Use `~/snap/firefox/common/<dir>` for `--profile` and screenshots, pass
  `--no-remote --profile <dir> --screenshot <file> --window-size=W,H <url>`, and delete the directory afterwards. Pages load data
  asynchronously, so serve a small local server whose page includes an image that answers after ~3 s to hold the `load` event.
  The page prefers the live GitHub CSV; a throwaway server should rewrite `RAW_URL` to `listings.csv` to test unpushed data.
- Don't use `pkill -f <pattern>` with a pattern that appears in your own command line (it kills the shell).

## Gotchas already paid for
- An element with `id="reset"` inside a `<form>` shadows `form.reset()`; there is a test guarding form-member names.
- Site fixtures: tests run offline against trimmed real responses in `tests/fixtures/`; sites change data between fetches.
- Otodom area can arrive as an int (`60`): scrapers coerce to float so the CSV round-trips byte-identical.
- Morizon titles contain HTML entities (`&amp;`), unescaped in the parser.
- nieruchomosci province markup alternates `<span>` and `<a>`; the fixture covers both.
- GitHub scheduled workflows run late sometimes, and are paused after 60 days without repository activity (not sure whether the
  bot's own commits count). Pushes to `main` by the bot mean: `git pull --rebase` before starting work.

## Backlog (agreed order, each needs its own plan + approval)
**Step 13: monthly fee + floor.** Fee (czynsz) is in list data for OLX (`params` key `rent`, 98/104) and Otodom (`rentPrice.value`,
65/74); Morizon and nieruchomosci have none, so "total per month" can only be shown for about half the rows ("czynsz nieznany"
otherwise; a merged flat can take the fee from whichever portal has it). Median fee ≈ 890 zł on 2800 zł (+32%). Floor: OLX
`floor_select`, Otodom `floorNumber`, Morizon `floorFormatted`. Pets/furnished/parking/lift exist on a single portal only: not worth filters.

**Other ideas, not scheduled:** collapse the filters behind a "Filtry" button on phones; home-screen icon (PWA manifest); new-listing
alerts (ntfy/Telegram, using the archive snapshots); price-drop badge; greying listings not seen for a day; run pytest on push in
Actions; `uv.lock` for reproducible CI; tidy the outdated "Step 1 (current)" section in `CLAUDE.md`; some titles are single rooms in
shared flats ("pokój do wynajęcia", area is the whole flat's).

## Known limitations
- OLX flats rarely merge (no street field). Merged rows exist only where streets match.
- Photo URLs are whatever the portal sent at the last sighting; a listing that left the portal may show the grey box. Favourites keep the
  URL in their snapshot, so a dead image there also falls back to the box. The desktop rows are taller now (72 px photo vs ~42 px text).
- A name missing from the district table becomes "Brak dzielnicy" until added (watch the Actions log for "Unknown district names").
- Each site is read only to 5 pages (~200 newest listings) per run, so an older live listing can drop out and be pruned after 3 days.
- `CLAUDE.md` still has an old "Step 1 (current)" layout section that no longer matches the repo.
