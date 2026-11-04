# Progress and handoff notes

Read this first in a new session, then `CLAUDE.md` (the spec and rules). Last updated 2026-11-04, after Step 15 (streets and house numbers from the text). **One thing is broken: the scheduled GitHub run does not fire, see "Open issue".**

## What this is
A Polish rental-listings scraper for Wrocław. It runs on GitHub Actions three times a day, writes `listings.csv`
(and small per-run archive snapshots), and a static page shows the results.
- Repo: `jradziejewski/flatscraper` (public). Page: https://jradziejewski.github.io/flatscraper/ (GitHub Pages, branch `main`, folder `/`).
- Config: `config.yaml` = Wrocław, max 5000 zł, min 30 m², min 2 rooms, all districts.

## Status: everything below is built, tested and pushed (HEAD `352c3b6`)
| Step | What |
|---|---|
| 1 | Skeleton, `Listing` model, CSV merge, config with filters |
| 2-5 | Scrapers: Otodom (`__NEXT_DATA__`), nieruchomosci-online (ld+json + tiles), Morizon (Nuxt `__NUXT_DATA__`), OLX (JSON API, fetched via system `curl`) |
| 6 | GitHub Actions workflow, cron `17 6,12,18 * * *` UTC (+ manual `workflow_dispatch`); commits `listings.csv`, `geocache.json`, `addresscache.json`, `archive/`. **The cron does not fire, see "Open issue"** |
| 7 | Archive: one snapshot per run holding only listings **new in that run**; listings not seen for 3 days are pruned from `listings.csv` |
| 8 | Static Polish page (`index.html` + `app.js`), no dependencies |
| 9 | District normalization to the 5 official districts (`aptsearch/districts.py`) |
| 10 | `street` column + duplicate merging across portals on the page (Strict) |
| 11 | Per-device page features: new since last visit, remembered filters, favourites/hide with undo |
| 12 | `photo` column (thumbnail URL from each portal's list data) + thumbnails on the page |
| 12b | Page revamp: toolbar + filter panel/bottom sheet + removable filter chips, clean desktop list, phone cards with the photo on top, design tokens, larger-photo `srcset`; desktop thumbnails 180x135 (`.thumb` in `index.html` and `PHOTO_SIZES` in `app.js` must agree, a test checks it) |
| 12d | Opened listings (title and portal chips) turn grey via CSS `:visited` |
| 13 | Map for each flat: `lat/lon/geo` columns, OLX coordinates, street geocoding through OSM (`geocode.py`, `geocache.json`), expandable OSM map on the page |
| 15 | Street + house number from titles, descriptions and the listing's own page (`streets.py`, `address.py`, `details.py`, `addresscache.json`), building-level map pins (`geo=address`) |
| 14 | *Not built:* monthly fee + floor (see Backlog; the number was skipped) |

## Open issue: the scheduled run does not fire (as of 2026-11-04 ~15:20 UTC)
The user wants GitHub Actions to run the scrape by itself. It has **never** run on schedule:
- Only one `scrape` run exists: the manual `workflow_dispatch` on 2026-11-03 21:59 UTC (before Steps 10-15). `GET .../actions/workflows/scrape.yml/runs?event=schedule` gives
  `total_count: 0`. Slots missed: 06:00 UTC and 12:17 UTC (cron was moved to minute 17 on 2026-11-04 to avoid the busy top of the hour; no change).
- Ruled out (public API): workflow state is `active`; repo is public, not a fork, not archived, default branch `main`; the deployed YAML is valid; manual dispatch
  works; no GitHub Actions incident in the status feed. Repo was created 2026-11-03 21:26 UTC.
- GitHub community threads describe the same symptom with no staff answer or confirmed fix: #203473 (a new public repo whose first schedule run came ~8 h after
  creation), #208729, #211641. Scheduled runs are best-effort. Ours is past 18 h, so "new repo lag" fits only partly.
- **Next steps, in order:** (1) watch the 18:17 UTC slot and tomorrow's 06:17 (advice is to wait 2-3 windows); (2) in the Actions UI disable then enable the `scrape`
  workflow (said to re-register schedules; unconfirmed); (3) a throwaway probe workflow on a frequent odd-minute cron (e.g. `*/15`) tells whether scheduling works for
  this repo at all (delete it afterwards); (4) GitHub Support ticket with the facts above; (5) dependable fallbacks if it stays dead: an external timer (e.g. cron-job.org)
  calling the `workflow_dispatch` API with a fine-grained token, or a systemd timer on the user's computer. The user prefers fixing GitHub Actions itself.
- **Also do one manual run now** (Actions > scrape > Run workflow): the bot has **never run the new code** (street/house extraction, page reading, geocoding, the two caches).
  Check the log shows `details:` and `geocode:` lines, no "Unknown district names", and that the commit includes `geocache.json` and `addresscache.json`.
  The live CSV on GitHub is currently the one generated locally on 2026-11-04 (pushed in `352c3b6`).

## Map of the code
```
main.py                  run(): scrapers -> normalize districts -> filter -> merge -> read listing pages (details) -> geocode -> snapshot new -> prune -> save
aptsearch/models.py      Listing (site,id,url,title,price_pln,area_m2,rooms,district,street,house,photo,lat,lon,geo,city,posted_at,first_seen,last_seen)
aptsearch/config.py      Config, load_config() (unknown keys fail), matches() (missing values pass)
aptsearch/storage.py     load/merge/save, prune (STALE_AFTER 3 days), save_snapshot; reads old CSVs without the street/house/photo/location columns; a location is never replaced by a less precise one
aptsearch/districts.py   WROCLAW table (59 neighbourhoods) + normalize_district(); unknown -> None, logged by main.py
aptsearch/streets.py     Wrocław street list (data/streets_wroclaw.json from OSM) + stem matcher; `python -m aptsearch.streets` refreshes it
aptsearch/address.py     find_address / resolve: street + house number from text
aptsearch/details.py     reads the listing's own page for rows still without a street (per-portal fields, addresscache.json, `python -m aptsearch.details`)
aptsearch/geocode.py     clean_point, load/save_cache (geocache.json), lookup + fill_street_coordinates (Nominatim), `python -m aptsearch.geocode` backfill
aptsearch/text.py        city_slug, clean_street, photo_url (http(s) only)
aptsearch/http.py        get() (urllib; browser UA, or an identifying one for OSM), get_curl() (OLX only: urllib gets 403), curl_status() (real HTTP status after a curl failure)
aptsearch/scrapers/      otodom, nieruchomosci, morizon, olx; SCRAPERS list in __init__.py
index.html, app.js       the page; pure functions exported for node tests, DOM code only in a browser
tests/                   308 tests (pytest); test_site.py drives app.js through node
.github/workflows/scrape.yml
archive/YYYY-MM/*.csv    immutable snapshots of new listings
geocache.json            street / building -> coordinates (or null), committed; addresscache.json: listing page already read -> street+house (or null)
aptsearch/data/          streets_wroclaw.json (2,571 OSM street names)
```

## Decisions that are not obvious from the code
- **Price** is the base rent. The extra monthly fee (czynsz) is *not* in `price_pln`. The same flat's price matched exactly across
  portals in 108 groups, so all portals report base rent.
- **Districts:** only Otodom/Morizon/OLX give the 5 official districts; nieruchomosci gives neighbourhoods. The table was built from
  Morizon/Otodom data (no conflicts) and cross-checked with pl.wikipedia "Podział administracyjny Wrocławia". Unmatched names become
  empty ("Brak dzielnicy") and are logged. Typo repair is deliberately strict (difflib cutoff 0.85: fixes "Krzyk", not "Krzyky").
- **`merge()`** updates `price_pln`, `district`, `street`, `house`, `photo`, the location (`lat`,`lon`,`geo` as a set) and `last_seen`; an empty value never overwrites
  a known one, and a location is replaced only by an equally or more precise one; rows are never deleted by `merge()` (pruning is a separate step and is
  skipped when a run finds nothing at all). Because it never erases a street, changing the extraction rules means regenerating from a clean CSV.
- **Duplicate merging is display-only, in `app.js`** (`groupDuplicates`). Strict rule: same price, area rounded to whole m², rooms,
  district, on different portals, AND the same street (distinctive words of one name contained in the other's); a street-less row
  needs the street named in its title/URL. OLX rows now mostly have a text-derived street, so they merge by the normal rule. Different streets never merge; two listings from one portal never merge.
  My estimate: spec-only matching would produce ~1 in 6 false merges, which is why street evidence is required.
- **"NOWE" baseline** = newest `last_seen` in the data she saw last time (not the wall clock; raw GitHub CSV is cached ~5 min).
- **Per-device storage** (`localStorage` keys `flatscraper:v1:*`): favourites keep a snapshot so they outlive the listing. Not shared
  between devices (we chose "browse only", no server).
- The page reads the CSV from `raw.githubusercontent.com` (CORS-enabled), so it never needs a redeploy for new data.
- OLX: Python's HTTP client gets 403 (also on its HTML pages), plain `curl` gets 200. We chose `curl` and not browser automation.
  It works from GitHub's runners (verified on the first run). OLX has no street field.
- **Photos (Step 12):** hot-linked from the portals' CDNs, never copied into the repo. Sizes: Otodom `small` 314x236, OLX 320x240 (we
  substitute the `{width}x{height}` placeholders), nieruchomosci ld+json `image`, Morizon `3x2_s` (450x300, 21 KB; `_m` is 600x400, 35 KB,
  not needed: the desktop thumbnail is 180x135). Morizon sends photo ids, so its URL is built in `scrapers/morizon.py` (`IMG_BASE`, the same URL its pages use).
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

- **Map (13) facts, measured 2026-11-04:** OLX gives `map.lat/lon` on every offer but only `radius 0 + show_detailed` offers are exact (75 of 302 rows);
  the rest are blurred (radius 8 or 1) and are `geo=area`: wide view, no pin. Otodom/Morizon/nieruchomosci have no coordinates in list data, so the
  street is geocoded: 241 of 249 distinct streets resolved; the 8 misses are mostly scraped text that is not a street ("mieszkanie na gaju - balkon
  - nowe budownictwo"), a typo and "Jana Pawła II" style names. 674 of 830 rows had a location after the backfill. OSM rules that matter: Nominatim
  allows 1 request per second with an identifying User-Agent and caching; the tile servers refuse requests without a Referer (403 tile), and a
  browser-looking User-Agent with a forged Referer gets an "Access blocked" (418) tile, so never fetch tiles with scripts. The map iframe needs WebGL.
  This machine has no GPU/WebGL browser, so tiles could not be drawn in my screenshots (only pin, caption, links, layout); **the user confirmed on their own
  device that the map works** ("it works well"). If a map ever looks blank, the fallback is a static grid of `<img>` tiles (no WebGL, not interactive).
- **A street-level pin is the middle of the street** (the caption says so); a building pin needs a house number from the text (Step 15). Same-named streets in other cities are
  rejected by the bounding box. A street not in `geocache.json` waits for the next run (cap 40 per run).

- **Opened listings (12d):** greyed with CSS `:visited` (user's choice over remembering clicks in `localStorage`, which would allow an
  "Obejrzane" tag and a hide-viewed filter but only count clicks made on this page). Verified with a headless Firefox profile that first
  visited one listing URL, then loaded the page.

- **Text addresses (15A, before page reading), measured 2026-11-04:** street coverage 456/830 rows (55%) -> 673/853 (79%); OLX 0% -> 79% of the offers seen in a run; house numbers
  on 79 rows, 71 of 73 address lookups resolved to a building. Extraction precision: Morizon rows with a known street 14/14 right where one was found;
  nieruchomosci titles 137/137; the Otodom/Morizon title disagreements were mostly portal typos or junk ("Komandroska", "Białowieska / Balkon").
  **OLX's own "exact" pin is not ground truth:** against the 21 OLX offers with an exact pin and a text address, the pin was closer to our building in 12
  and closer to the street midpoint in 9; the two big gaps (2.6 and 3.4 km) were business listings whose text states the address outright ("położone przy
  ul. Racławickiej 95"). Hence `address` ranks above `point`. A crude regex got only 9/15 right; the dictionary + stem + cue design 14/15 (the miss was
  the city word, now a stop rule).
- **Known limits of the extractor:** a title that starts with an inflected surname and has no cue ("Madalińskiego 57, garaż") is not guessed; a street only
  named in a landmark list is skipped; `merge()` never erases a street, so after changing the extraction rules, regenerate from a clean CSV (restore it
  with git, rerun `main.py`) rather than expecting old wrong streets to heal.

- **Page reading (15B), measured 2026-11-04:** streets 55% -> **88%** of rows (778/883; Otodom 80 -> 94%, Morizon 86 -> 99%, nieruchomosci 93 -> 97%, OLX 0 -> 73%),
  locations 81% -> **94%**, 102 buildings. The 105 rows still without a street are mostly OLX (88): a sample of 12 of them were all LIVE offers whose text simply
  names no street (estate names like "Browary Wrocławskie"), so "every listing has its street in the text" is not true for OLX. nieruchomosci pages also say
  "Lokalizacja przybliżona" (the portal hides the address); Morizon's own `propertyData.location.street` is null for its gaps.
- **Readers look at specific fields, never "everything on the page"** (a scan of all strings produced these wrong streets): Otodom `ad.owner.address` and
  `ad.agency.address` are the AGENCY's office; Morizon's page holds its own company address in a consent text and "Zieleniec przy ul. ..." (a park) in
  `environment`; nieruchomosci's ld+json has the agent's address and its description ends with the agency's office in Polish AND English
  ("MAGNUS Real Estate Agency, Wrocław, ul. Krakowska 52"). Also landmarks ("dojazd do International High School przy ul. Racławickiej"). The extractor skips
  those: landmark words within 3 words and one sentence, a phone label / phone number / postal code in the 40 characters before the cue, "agency" and
  "real estate". A broad "agency word nearby" rule would have thrown out good streets ("bez pośredników", "nieruchomości" are everyday words).
- **Bare street names in titles are NOT used** ("Mieszkanie 2 pokojowe Waniliowa"): on rows with a known street, 22 of 199 such hits contradicted it ("Rynek",
  "Nowa" from "NOWA CENA", "Mieszczańska" from the area "Kępa Mieszczańska"). Possible later: corroborate with a second source.
- **Robustness:** page-read failures are counted per portal (three dead OLX offers once starved Otodom because rows go in key order); a 404/410 is cached as
  "nothing to read"; for OLX curl only says "exit 22", so the real status is fetched once after a failure (410 removed, 200 live, verified). 25 pages per run.
- Otodom detail pages also carry coordinates (`ad.location.coordinates`, with `mapDetails.radius` 0 = exact, 500 = approximate): not used yet, a candidate
  for the 14 percent of rows still without a precise pin.

## How we work (user preferences)
- **One step at a time.** Show the file layout first, plan, get approval, implement, then stop and wait. Plan mode is used for this;
  `ExitPlanMode` is the approval point.
- **Commits:** granular, message style `where: what changed` (e.g. `site: add favourites and hide, stored per device`). Generated
  data (`listings.csv`, `archive/`) in its own commits. **Never push unless asked**; only fast-forward pushes, never rewrite pushed history.
- Verify visually: real headless-Firefox screenshots for page changes, and real data for scraper changes, not just unit tests.
- Output style is "Explanatory" (short `★ Insight` blocks).
- **Accuracy over coverage:** the user accepted "no street" over a wrong one, and wants numbers on real data (precision against rows with a known street, not just tests).
  When a measurement surprises, investigate it (e.g. OLX's "exact" pins turned out noisier than our text addresses).
- The user says "push" when they want a push; one-line replies like "push" are the cue. Always `git fetch` first.
- The user's own Firefox is running on this machine: never kill it.

## Running things locally
- Backfills (one-off, politely rate-limited, each rewrites `listings.csv` and its cache): `python -m aptsearch.streets` (street list), `python -m aptsearch.geocode`,
  `python -m aptsearch.details`. A full regeneration: `git show <old commit>:listings.csv > listings.csv`, `main.py`, then the two backfills.
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
- GitHub scheduled workflows are best-effort and ours does not fire at all (see "Open issue"). They are also paused after 60 days without repository
  activity. Pushes to `main` by the bot mean: `git fetch` / `git pull --rebase` before starting work.
- GitHub Pages caches `app.js`/`index.html` for 10 minutes (`max-age=600`), so after a push hard-refresh; a failed Pages deploy is retried by GitHub on its own.
- OSM: tiles answer 403 without a Referer (never `referrerpolicy="no-referrer"` on the map iframe); Nominatim understands only base-form street names, never the
  inflected form from running text; its fallback to the street when a building is unknown must not be taken for a building (checked via `address.house_number`).
- `curl --fail` exits 22 for any HTTP error: use `http.curl_status()` to tell a removed OLX offer (410) from a block (403/429).
- `tests/conftest.py` makes any real `http.get` raise, so no test can reach the network; tests pass their own `fetch`.

## Backlog (agreed order, each needs its own plan + approval)
**Step 14: monthly fee + floor.** Fee (czynsz) is in list data for OLX (`params` key `rent`, 98/104) and Otodom (`rentPrice.value`,
65/74); Morizon and nieruchomosci have none, so "total per month" can only be shown for about half the rows ("czynsz nieznany"
otherwise; a merged flat can take the fee from whichever portal has it). Median fee ≈ 890 zł on 2800 zł (+32%). Floor: OLX
`floor_select`, Otodom `floorNumber`, Morizon `floorFormatted`. Pets/furnished/parking/lift exist on a single portal only: not worth filters.

**Other ideas, not scheduled:** collapse the filters behind a "Filtry" button on phones; home-screen icon (PWA manifest); new-listing
alerts (ntfy/Telegram, using the archive snapshots); price-drop badge; greying listings not seen for a day; run pytest on push in
Actions; `uv.lock` for reproducible CI; some titles are single rooms in shared flats ("pokój do wynajęcia", area is the whole flat's).
**From the description work (not built, not requested):** deposit (kaucja, in 36% of OLX descriptions), "available from" (25%), "bez prowizji" (21%) as extra
facts/badges; Otodom detail-page coordinates (radius 0 = exact) for rows still without a precise pin; corroborating a bare street word in a title with a second
source; the ~88 OLX offers whose text names no street.

## Known limitations
- Merged rows exist only where streets match. OLX now has a street for ~73% of rows (text-derived), the rest cannot merge.
- Photo URLs are whatever the portal sent at the last sighting; a listing that left the portal may show the grey box. Favourites keep the
  URL in their snapshot, so a dead image there also falls back to the box. The desktop rows are taller now (135 px photo).
- A name missing from the district table becomes "Brak dzielnicy" until added (watch the Actions log for "Unknown district names").
- Each site is read only to 5 pages (~200 newest listings) per run, so an older live listing can drop out and be pruned after 3 days.
- ~105 rows have no street (OLX 88, Otodom 11, nieruchomosci 5, Morizon 1): their text or page names none, or only a landmark. Street-level pins are the middle of the
  street; OLX's `point` pins are sometimes km off in business listings, which is why a text `address` ranks above them.
