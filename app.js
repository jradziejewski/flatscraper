"use strict";

// Listings page: loads listings.csv, filters, sorts and renders it. No dependencies.
// Pure functions at the top are unit-tested under node (tests/test_site.py);
// the DOM code at the bottom only runs in a browser.

const RAW_URL = "https://raw.githubusercontent.com/jradziejewski/flatscraper/main/listings.csv";
const LOCAL_URL = "listings.csv"; // fallback, and what `python -m http.server` serves while developing
const NEW_WINDOW_MS = 24 * 3600 * 1000;
const STALE_AFTER_MS = 13 * 3600 * 1000; // the bot runs every 6 h
const TIME_ZONE = "Europe/Warsaw";
const NO_DISTRICT = "__none__"; // filter value for listings without a district

const SITE_NAMES = { otodom: "Otodom", morizon: "Morizon", olx: "OLX", nieruchomosci: "Nieruchomosci-online" };

// ---------- pure functions ----------

/** RFC 4180 CSV -> array of {header: string} objects. Handles quotes, commas and newlines in fields. */
function parseCSV(text) {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  const records = [];
  let record = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ",") { record.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      record.push(field); field = "";
      records.push(record); record = [];
    } else field += c;
  }
  if (field !== "" || record.length) { record.push(field); records.push(record); }
  const [header, ...body] = records.filter((r) => !(r.length === 1 && r[0] === ""));
  if (!header) return [];
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ""])));
}

const num = (s) => (s === "" || s == null || Number.isNaN(Number(s)) ? null : Number(s));
const ts = (s) => { const t = Date.parse(s); return Number.isNaN(t) ? null : t; };

/** Raw CSV strings -> typed rows used by the page. */
function normalize(record) {
  const price = num(record.price_pln);
  const area = num(record.area_m2);
  return {
    site: record.site,
    id: record.id,
    url: record.url,
    title: record.title,
    price,
    area,
    ppm: price != null && area ? price / area : null,
    rooms: num(record.rooms),
    district: record.district || null,
    street: record.street || null,
    house: record.house || null, // building number, only ever shown together with the street
    photo: safeUrl(record.photo), // listing data comes from scraped sites: only http(s) ever reaches an <img src>
    ...parseLocation(record),
    city: record.city || null,
    firstSeen: ts(record.first_seen),
    lastSeen: ts(record.last_seen),
  };
}

const fold = (s) => (s || "").toLocaleLowerCase("pl");

/** New = first seen after `since` (the data she last looked at), or within 24 h when there is no such moment. */
function isNew(r, now, since) {
  if (r.firstSeen == null) return false;
  return since != null ? r.firstSeen > since : now - r.firstSeen <= NEW_WINDOW_MS;
}

/** Like the scrapers' filters, a missing value never excludes a listing. */
function applyFilters(rows, f, now) {
  const q = fold(f.q).trim();
  return rows.filter((r) => {
    if (q && !(r.searchText ?? fold(`${r.title} ${r.district ?? ""}`)).includes(q)) return false;
    if (f.site && !(r.sites || [r.site]).includes(f.site)) return false;
    if (f.districts && f.districts.length && !f.districts.includes(r.district ?? NO_DISTRICT)) return false;
    if (f.maxPrice != null && r.price != null && r.price > f.maxPrice) return false;
    if (f.minArea != null && r.area != null && r.area < f.minArea) return false;
    if (f.minRooms != null && r.rooms != null && r.rooms < f.minRooms) return false;
    if (f.onlyNew && !isNew(r, now, f.since)) return false;
    const mark = f.marks ? markState(r, f.marks) : null;
    const view = f.view || "current";
    if (view === "current" && mark === "hidden") return false;
    if (view === "fav" && mark !== "fav") return false;
    if (view === "hidden" && mark !== "hidden") return false;
    return true;
  });
}

/** Sort a copy; empty values go last in both directions. */
function sortRows(rows, key, dir) {
  const sign = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const c = typeof x === "string" ? x.localeCompare(y, "pl") : x - y;
    return c * sign;
  });
}

// ---------- duplicate merging ----------
// The same flat is often listed on several portals. Rows are merged only when the specs match AND there is
// evidence it is the same place (the same street); see CLAUDE.md. Rows are never dropped: a merged row keeps
// a link to every portal's listing.

const SITE_PRIORITY = ["otodom", "morizon", "nieruchomosci", "olx"];
const sitePriority = (site) => { const i = SITE_PRIORITY.indexOf(site); return i < 0 ? 99 : i; };

const foldText = (s) => (s || "").toLowerCase().replace(/ł/g, "l").normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
const wordsOf = (s) => foldText(s).match(/[a-z0-9]+/g) || [];
const distinctive = (w) => w.length >= 4;

/** Words of a street name without its ul./al./plac prefix. */
function streetWords(street) {
  return wordsOf((street || "").replace(/^(?:(?:ul|al|pl|os)\.\s*|(?:ulica|aleja|plac|osiedle)\s+)/i, ""));
}

/** Same street if all distinctive words of the shorter name are in the longer one: "Sikorskiego" ~
 *  "gen. Władysława Sikorskiego" ~ "Generała Władysława Sikorskiego". Short words (gen., ks., św.) are ignored. */
function streetsMatch(a, b) {
  const wa = streetWords(a).filter(distinctive), wb = streetWords(b).filter(distinctive);
  if (!wa.length || !wb.length) return false;
  const [small, big] = wa.length <= wb.length ? [wa, wb] : [wb, wa];
  return small.every((w) => big.includes(w));
}

function slugWords(url) {
  const last = (url || "").split("?")[0].replace(/\/+$/, "").split("/").pop() || "";
  return wordsOf(last.replace(/\.html?$/i, ""));
}

/** For a row without a street (OLX): does its title or URL slug name the other row's street? */
function mentionsStreet(street, other) {
  const words = streetWords(street).filter(distinctive);
  if (!words.length) return false;
  const text = new Set([...wordsOf(other.title), ...slugWords(other.url)]);
  return text.has(words[words.length - 1]); // the last word is usually the distinctive one
}

/** Strict: two streets that differ never match; with no street on either side there is no evidence. */
function hasEvidence(a, b) {
  if (a.street && b.street) return streetsMatch(a.street, b.street);
  if (a.street) return mentionsStreet(a.street, b);
  if (b.street) return mentionsStreet(b.street, a);
  return false;
}

/** One display row for a set of listings of the same flat (a single listing is a group of one). */
function toView(members) {
  const ordered = [...members].sort((a, b) => sitePriority(a.site) - sitePriority(b.site));
  const first = (m) => m.firstSeen ?? Infinity;
  const rep = [...members].sort((a, b) => first(a) - first(b) || sitePriority(a.site) - sitePriority(b.site))[0];
  const earliest = Math.min(...members.map(first));
  return {
    ...rep,
    house: rep.house ?? ordered.find((m) => m.house && rep.street && streetsMatch(m.street, rep.street))?.house ?? null,
    firstSeen: Number.isFinite(earliest) ? earliest : null, // a flat is only "new" if its earliest listing is
    photo: ordered.find((m) => m.photo)?.photo ?? null, // the first portal (by priority) that has a picture
    loc: bestLocation(ordered),
    members: ordered,
    sites: ordered.map((m) => m.site),
    searchText: fold(`${members.map((m) => m.title).join(" ")} ${rep.district ?? ""}`),
  };
}

/** Normalized rows -> display rows, merging the same flat listed on several portals. */
function groupDuplicates(rows) {
  const bySpec = new Map();
  rows.forEach((r, i) => {
    if (r.price == null || r.area == null || r.rooms == null || !r.district) return;
    const key = [r.price, Math.round(r.area), r.rooms, r.district].join("|");
    if (!bySpec.has(key)) bySpec.set(key, []);
    bySpec.get(key).push(i);
  });
  const groupOf = new Map(); // row index -> array of member indices
  for (const idxs of bySpec.values()) {
    if (idxs.length < 2) continue;
    const parent = new Map(idxs.map((i) => [i, i]));
    const find = (i) => { while (parent.get(i) !== i) { parent.set(i, parent.get(parent.get(i))); i = parent.get(i); } return i; };
    for (let x = 0; x < idxs.length; x++) {
      for (let y = x + 1; y < idxs.length; y++) {
        const a = rows[idxs[x]], b = rows[idxs[y]];
        if (a.site !== b.site && hasEvidence(a, b)) parent.set(find(idxs[x]), find(idxs[y]));
      }
    }
    const components = new Map();
    idxs.forEach((i) => { const root = find(i); if (!components.has(root)) components.set(root, []); components.get(root).push(i); });
    for (const members of components.values()) {
      const sites = new Set(members.map((i) => rows[i].site));
      if (members.length >= 2 && sites.size === members.length) members.forEach((i) => groupOf.set(i, members));
    }
  }
  const views = [];
  const emitted = new Set();
  rows.forEach((r, i) => {
    const members = groupOf.get(i);
    if (!members) { views.push(toView([r])); return; }
    if (emitted.has(members)) return;
    emitted.add(members);
    views.push(toView(members.map((j) => rows[j])));
  });
  return views;
}

/** `merge` off shows every listing on its own row. */
function buildRows(rows, merge) {
  return merge ? groupDuplicates(rows) : rows.map((r) => toView([r]));
}

function formatPrice(n) {
  return n == null ? "—" : new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 0 }).format(n) + " zł";
}

function formatArea(n) {
  return n == null ? "—" : new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 1 }).format(n) + " m²";
}

function formatDate(ms) {
  return new Date(ms).toLocaleString("pl-PL", {
    timeZone: TIME_ZONE, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function relativeTime(ms, now) {
  if (ms == null) return "—";
  const min = Math.floor((now - ms) / 60000);
  if (min < 1) return "przed chwilą";
  if (min < 60) return `${min} min temu`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} godz. temu`;
  const d = Math.floor(h / 24);
  if (d < 30) return d === 1 ? "1 dzień temu" : `${d} dni temu`;
  return new Date(ms).toLocaleDateString("pl-PL", { timeZone: TIME_ZONE, day: "numeric", month: "short" });
}

/** Only ever link to http(s): listing data comes from scraped websites. */
function safeUrl(u) {
  return /^https?:\/\//i.test(u || "") ? u : null;
}

// ---------- photos ----------

/** Same picture at two sizes per portal, so phones (full-width cards) get a sharp one and desktop keeps the small one.
 *  `small` is what the scraper stored; the larger variant is the same CDN path with a bigger size token. */
const PHOTO_VARIANTS = [
  { find: /(\/image;s=)320x240$/, to: "$1640x480", widths: [320, 640] },                // OLX
  { find: /(;s=)314x236(;q=\d+)$/, to: "$1655x491$2", widths: [314, 655] },             // Otodom
  { find: /(\/3x2_)s(:fill_and_crop\/)/, to: "$1m$2", widths: [450, 600] },             // Morizon
];
const PHOTO_SIZES = "(max-width: 700px) 100vw, 180px"; // keep in step with `.thumb` on desktop in index.html

/** {src, srcset} for an <img>, or null when there is no usable (http/https) photo. srcset is null when the portal
 *  has no larger variant (nieruchomosci-online). Everything that reaches an <img> goes through here. */
function photoSources(url) {
  const src = safeUrl(url);
  if (!src) return null;
  for (const v of PHOTO_VARIANTS) {
    if (!v.find.test(src)) continue;
    const large = safeUrl(src.replace(v.find, v.to));
    if (large && large !== src) return { src, srcset: `${src} ${v.widths[0]}w, ${large} ${v.widths[1]}w` };
  }
  return { src, srcset: null };
}

// ---------- map ----------
// Each listing may have a location with a precision level (set by the bot): "point" is the real address (OLX, when it
// shows one), "street" is the middle of the street (geocoded, no house number), "area" is a blurred circle of several km.
// The map is an OpenStreetMap iframe that is only created when a map is opened: nothing is requested before that.

const GEO_LEVELS = ["area", "street", "point", "address"]; // worst to best (the same order as aptsearch/models.py)
const GEO_TEXT = {
  address: "adres z opisu ogłoszenia",
  point: "lokalizacja z ogłoszenia",
  street: "środek ulicy, numer domu nieznany",
  area: "przybliżona okolica, portal ukrywa dokładny adres",
};
// half-size of the view: [lon, lat] degrees. A building gets a close view, a blurred area a wide one.
const MAP_SPAN = { address: [0.0025, 0.0015], point: [0.006, 0.0035], street: [0.006, 0.0035], area: [0.07, 0.04] };

/** {lat, lon, geo} when all three are present and sane, else all null: a half-known location is no location. */
function parseLocation(record) {
  const lat = num(record.lat), lon = num(record.lon);
  const ok = lat != null && lon != null && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 && GEO_LEVELS.includes(record.geo);
  return ok ? { lat, lon, geo: record.geo } : { lat: null, lon: null, geo: null };
}

/** The most precise location among a flat's listings (a merged row may have an exact OLX point and a geocoded street). */
function bestLocation(members) {
  let best = null;
  for (const m of members) {
    if (m.lat == null || m.lon == null) continue;
    if (!best || GEO_LEVELS.indexOf(m.geo) > GEO_LEVELS.indexOf(best.geo)) best = { lat: m.lat, lon: m.lon, geo: m.geo };
  }
  return best;
}

const validLoc = (loc) => !!loc && Number.isFinite(loc.lat) && Number.isFinite(loc.lon) && Math.abs(loc.lat) <= 90 &&
  Math.abs(loc.lon) <= 180 && GEO_LEVELS.includes(loc.geo);
const coord = (n) => n.toFixed(6);

/** The OpenStreetMap embed for a location: numbers only, so nothing from a scraped site can end up in the URL.
 *  A street or point gets a pin; an "area" gets a wide view and no pin, so it does not look more exact than it is. */
function mapEmbedUrl(loc) {
  if (!validLoc(loc)) return null;
  const [dLon, dLat] = MAP_SPAN[loc.geo];
  const bbox = [loc.lon - dLon, loc.lat - dLat, loc.lon + dLon, loc.lat + dLat].map(coord).join(",");
  const marker = loc.geo === "area" ? "" : `&marker=${encodeURIComponent(`${coord(loc.lat)},${coord(loc.lon)}`)}`;
  return `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik${marker}`;
}

/** Links that open the same place in a full map app (Google's documented URL scheme opens the native app on phones). */
function mapLinks(loc) {
  if (!validLoc(loc)) return null;
  const lat = coord(loc.lat), lon = coord(loc.lon), zoom = loc.geo === "area" ? 13 : 17;
  return {
    google: `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`,
    osm: `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=${zoom}/${lat}/${lon}`,
  };
}

/** "Komedy 16": the street with its house number when the listing's text gave one. */
function streetLabel(r) {
  return r.street ? (r.house ? `${r.street} ${r.house}` : r.street) : null;
}

/** "Krzyki · Hubska — środek ulicy, numer domu nieznany." Says how exact the pin is. */
function mapCaption(loc, place) {
  const text = GEO_TEXT[loc.geo];
  return place ? `${place} — ${text}.` : `${text[0].toUpperCase()}${text.slice(1)}.`;
}

// ---------- active filters (shown as removable chips) ----------

/** One chip per active filter of the filter panel (not the search text or the view). `key` says which control to clear. */
function activeFilterChips(f) {
  const chips = [];
  if (f.site) chips.push({ key: "site", value: f.site, label: SITE_NAMES[f.site] || f.site });
  for (const d of f.districts || []) chips.push({ key: "district", value: d, label: d === NO_DISTRICT ? "Brak dzielnicy" : d });
  if (f.maxPrice != null) chips.push({ key: "maxPrice", value: f.maxPrice, label: `do ${formatPrice(f.maxPrice)}` });
  if (f.minArea != null) chips.push({ key: "minArea", value: f.minArea, label: `od ${formatArea(f.minArea)}` });
  if (f.minRooms != null) chips.push({ key: "minRooms", value: f.minRooms, label: `od ${f.minRooms} pok.` });
  if (f.onlyNew) chips.push({ key: "onlyNew", value: true, label: "Tylko nowe" });
  return chips;
}

// ---------- per-device storage (localStorage / sessionStorage are injected so tests can fake them) ----------

const LAST_VISIT_KEY = "flatscraper:v1:lastVisit";
const SESSION_BASELINE_KEY = "flatscraper:v1:baseline";

function readJSON(storage, key) {
  try {
    const raw = storage.getItem(key);
    return raw == null ? undefined : JSON.parse(raw);
  } catch { return undefined; }
}

function writeJSON(storage, key, value) {
  try { storage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}

/** In-memory stand-in used when the browser's storage is blocked (private mode etc.). */
function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: (k) => { m.delete(k); } };
}

/**
 * Baseline for "new since last visit" = the data she saw last time (the newest `last_seen` in the CSV then),
 * not the wall clock: GitHub caches the CSV for ~5 min, so a clock-based baseline could hide flats she never saw.
 * It is kept per tab session so a refresh doesn't make the NOWE badges vanish.
 * Returns null on a first visit (the page then falls back to "last 24 h").
 */
function startVisit(local, session, dataTime) {
  const stored = readJSON(local, LAST_VISIT_KEY);
  const inSession = readJSON(session, SESSION_BASELINE_KEY);
  const baseline = inSession !== undefined ? inSession : (typeof stored === "number" ? stored : null);
  writeJSON(session, SESSION_BASELINE_KEY, baseline);
  if (dataTime > 0) writeJSON(local, LAST_VISIT_KEY, dataTime);
  return typeof baseline === "number" ? baseline : null;
}

// ---------- favourites and hidden listings (kept in this browser only) ----------

const FAVS_KEY = "flatscraper:v1:favs";
const HIDDEN_KEY = "flatscraper:v1:hidden";
const MAX_MARKS = 2000;
const VIEWS = ["current", "fav", "hidden"];

const markKey = (m) => `${m.site}:${m.id}`;
const emptyMarks = () => ({ favs: {}, hidden: {} });

/** A favourite keeps the facts of the listing, so it still shows up after the portal removes it from our data. */
function snapshotOf(m, group, savedAt) {
  return {
    site: m.site, id: m.id, url: m.url, title: m.title, price: m.price, area: m.area, rooms: m.rooms,
    district: m.district, street: m.street, house: m.house ?? null, photo: m.photo ?? null, lat: m.lat ?? null, lon: m.lon ?? null, geo: m.geo ?? null, firstSeen: m.firstSeen, lastSeen: m.lastSeen, group, savedAt,
  };
}

function snapshotToRow(snap) {
  return { ...snap, ppm: snap.price != null && snap.area ? snap.price / snap.area : null, city: null };
}

/** Oldest marks go first when there are too many: hidden ones before favourites. */
function capMarks(marks, max = MAX_MARKS) {
  const overflow = Object.keys(marks.favs).length + Object.keys(marks.hidden).length - max;
  if (overflow <= 0) return marks;
  const hidden = { ...marks.hidden }, favs = { ...marks.favs };
  const oldest = (obj, time) => Object.keys(obj).sort((a, b) => time(obj[a]) - time(obj[b]));
  let left = overflow;
  for (const k of oldest(hidden, (t) => t)) { if (left-- <= 0) break; delete hidden[k]; }
  for (const k of oldest(favs, (f) => f.savedAt ?? 0)) { if (left-- <= 0) break; delete favs[k]; }
  return { favs, hidden };
}

const isText = (v) => typeof v === "string";
const numOrNull = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Storage content is untrusted: keep only well-formed entries. */
function sanitizeMarks(favsRaw, hiddenRaw) {
  const marks = emptyMarks();
  if (favsRaw && typeof favsRaw === "object" && !Array.isArray(favsRaw)) {
    for (const [key, f] of Object.entries(favsRaw)) {
      if (!f || typeof f !== "object" || !isText(f.site) || !isText(f.id) || !isText(f.title) || key !== markKey(f)) continue;
      marks.favs[key] = {
        site: f.site, id: f.id, url: isText(f.url) ? f.url : "", title: f.title, price: numOrNull(f.price),
        area: numOrNull(f.area), rooms: numOrNull(f.rooms), district: isText(f.district) ? f.district : null,
        street: isText(f.street) ? f.street : null, house: isText(f.house) ? f.house : null, photo: isText(f.photo) ? safeUrl(f.photo) : null,
        ...parseLocation({ lat: numOrNull(f.lat), lon: numOrNull(f.lon), geo: f.geo }),
        firstSeen: numOrNull(f.firstSeen), lastSeen: numOrNull(f.lastSeen),
        group: Array.isArray(f.group) ? f.group.filter(isText) : [key], savedAt: numOrNull(f.savedAt) ?? 0,
      };
    }
  }
  if (hiddenRaw && typeof hiddenRaw === "object" && !Array.isArray(hiddenRaw)) {
    for (const [key, t] of Object.entries(hiddenRaw)) if (typeof t === "number" && /^[^:]+:.+/.test(key)) marks.hidden[key] = t;
  }
  return marks;
}

const loadMarks = (storage) => sanitizeMarks(readJSON(storage, FAVS_KEY), readJSON(storage, HIDDEN_KEY));

function saveMarks(storage, marks) {
  const a = writeJSON(storage, FAVS_KEY, marks.favs);
  const b = writeJSON(storage, HIDDEN_KEY, marks.hidden);
  return a && b;
}

/** "fav" | "hidden" | null for a display row. A favourite wins if its listings disagree (e.g. after a merge). */
function markState(view, marks) {
  let hidden = false;
  for (const m of view.members) {
    const k = markKey(m);
    if (marks.favs[k]) return "fav";
    if (marks.hidden[k]) hidden = true;
  }
  return hidden ? "hidden" : null;
}

/** mode: "fav" | "hidden" | null (clear). Applies to every listing in the row, so it survives re-grouping. */
function setMark(marks, view, mode, now) {
  const group = view.members.map(markKey);
  const favs = { ...marks.favs }, hidden = { ...marks.hidden };
  for (const m of view.members) {
    const k = markKey(m);
    delete favs[k];
    delete hidden[k];
    if (mode === "fav") favs[k] = snapshotOf(m, group, now);
    else if (mode === "hidden") hidden[k] = now;
  }
  return capMarks({ favs, hidden });
}

/** Keep favourites' snapshots current while their listings are still in the data (price changes etc.). */
function refreshSnapshots(marks, rows) {
  const favs = { ...marks.favs };
  let changed = false;
  for (const r of rows) {
    const k = markKey(r), old = favs[k];
    if (!old) continue;
    const fresh = snapshotOf(r, old.group, old.savedAt);
    if (JSON.stringify(fresh) !== JSON.stringify(old)) { favs[k] = fresh; changed = true; }
  }
  return changed ? { marks: { ...marks, favs }, changed } : { marks, changed };
}

/** Favourites whose listings have left the data, as display rows (flagged `gone`), one per flat. */
function goneViews(marks, rows) {
  const live = new Set(rows.map(markKey));
  const groups = new Map();
  for (const f of Object.values(marks.favs)) {
    if (live.has(markKey(f)) || f.group.some((k) => live.has(k))) continue; // still shown through a live listing
    const id = [...f.group].sort().join("|");
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(f);
  }
  return [...groups.values()].map((snaps) => ({ ...toView(snaps.map(snapshotToRow)), gone: true }));
}

// ---------- remembered filters ----------

const PREFS_KEY = "flatscraper:v1:prefs";
const SORT_KEYS = ["title", "price", "area", "ppm", "rooms", "district", "site", "firstSeen"];
const DEFAULT_SORT = { key: "firstSeen", dir: "desc" };
const ROOM_CHOICES = [1, 2, 3, 4];

/**
 * What is worth remembering between visits. The search text is deliberately left out: a stale search term
 * left over from last time would look like the list is broken.
 */
function prefsFromState({ site, districts, maxPrice, minArea, minRooms, onlyNew, view }, merge, sort) {
  return { site, districts, maxPrice, minArea, minRooms, onlyNew, view, merge, sort };
}

/** Anything coming back from storage is untrusted: keep only values that still make sense for the current data. */
function sanitizePrefs(raw, known) {
  const p = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const amount = (v) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);
  const sort = p.sort && SORT_KEYS.includes(p.sort.key) && ["asc", "desc"].includes(p.sort.dir)
    ? { key: p.sort.key, dir: p.sort.dir } : { ...DEFAULT_SORT };
  return {
    site: known.sites.includes(p.site) ? p.site : "",
    districts: Array.isArray(p.districts) ? p.districts.filter((d) => known.districts.includes(d)) : [],
    maxPrice: amount(p.maxPrice),
    minArea: amount(p.minArea),
    minRooms: ROOM_CHOICES.includes(p.minRooms) ? p.minRooms : null,
    onlyNew: p.onlyNew === true,
    view: p.view === "fav" ? "fav" : "current", // a leftover "hidden" view would look like the page is empty
    merge: p.merge !== false,
    sort,
  };
}

const loadPrefs = (storage, known) => sanitizePrefs(readJSON(storage, PREFS_KEY), known);
const savePrefs = (storage, prefs) => writeJSON(storage, PREFS_KEY, prefs);

/** "dziś, 18:30" / "wczoraj, 18:30" / "3 lis, 18:30" (Warsaw time). */
function describeSince(ms, now) {
  const day = (t) => new Date(t).toLocaleDateString("sv-SE", { timeZone: TIME_ZONE });
  const time = new Date(ms).toLocaleTimeString("pl-PL", { timeZone: TIME_ZONE, hour: "2-digit", minute: "2-digit" });
  if (day(ms) === day(now)) return `dziś, ${time}`;
  if (day(ms) === day(now - 24 * 3600 * 1000)) return `wczoraj, ${time}`;
  return formatDate(ms);
}

// ---------- manual update: start the scrape workflow from the page ----------
// A static page cannot hold a secret, so the owner pastes a fine-grained token (this repo only, "Actions: read and write")
// once per device. It lives in this browser's localStorage and is only ever sent to api.github.com.

const REPO = "jradziejewski/flatscraper";
const WORKFLOW = "scrape.yml";
const TOKEN_KEY = "flatscraper:v1:ghToken";
const API = `https://api.github.com/repos/${REPO}`;
const RUN_SLACK_MS = 60 * 1000; // the browser's clock may run a little behind GitHub's

/** The pasted token if it looks like a GitHub one (and nothing else), otherwise null: junk is never sent or stored. */
function cleanToken(value) {
  const t = typeof value === "string" ? value.trim() : "";
  return /^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(t) ? t : null;
}

function apiHeaders(token) {
  return { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
}

/** The workflow_dispatch call: 204 with no body when it worked (it does not say which run it started). */
function dispatchRequest(token) {
  return {
    url: `${API}/actions/workflows/${WORKFLOW}/dispatches`,
    init: { method: "POST", headers: { ...apiHeaders(token), "Content-Type": "application/json" }, body: JSON.stringify({ ref: "main" }) },
  };
}

const runsUrl = () => `${API}/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=5`;
const headCommitUrl = () => `${API}/commits/main`;

/** The dispatch does not return a run id, so take the newest manual run created after the click. */
function pickRun(runs, sinceMs) {
  const mine = (Array.isArray(runs) ? runs : []).filter((r) => r && Date.parse(r.created_at) >= sinceMs - RUN_SLACK_MS);
  mine.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  return mine[0] || null;
}

/** {label, done, ok} for a run as the API describes it. */
function runPhase(run) {
  if (!run) return { label: "Uruchamianie…", done: false, ok: false };
  if (run.status === "completed") {
    if (run.conclusion === "success") return { label: "Wczytywanie nowych danych…", done: true, ok: true };
    return { label: run.conclusion === "cancelled" ? "Aktualizacja została anulowana." : "Aktualizacja nie powiodła się.", done: true, ok: false };
  }
  return { label: run.status === "in_progress" ? "Pobieranie ogłoszeń…" : "W kolejce…", done: false, ok: false };
}

/** A URL pinned to one commit is never served from the ~5 min cache of the `main` one. */
function freshCsvUrl(sha) {
  return typeof sha === "string" && /^[0-9a-f]{40}$/.test(sha) ? `https://raw.githubusercontent.com/${REPO}/${sha}/listings.csv` : null;
}

if (typeof module !== "undefined") {
  module.exports = { cleanToken, apiHeaders, dispatchRequest, runsUrl, headCommitUrl, pickRun, runPhase, freshCsvUrl, TOKEN_KEY, parseCSV, normalize, applyFilters, sortRows, formatPrice, formatArea, relativeTime, safeUrl, NEW_WINDOW_MS, NO_DISTRICT, groupDuplicates, buildRows, streetsMatch,
    photoSources, activeFilterChips, parseLocation, bestLocation, mapEmbedUrl, mapLinks, mapCaption, streetLabel, GEO_LEVELS,
    isNew, startVisit, describeSince, memoryStorage, readJSON, writeJSON,
    prefsFromState, sanitizePrefs, loadPrefs, savePrefs, PREFS_KEY,
    markKey, emptyMarks, loadMarks, saveMarks, setMark, markState, refreshSnapshots, goneViews, capMarks, sanitizeMarks, MAX_MARKS, FAVS_KEY, HIDDEN_KEY, VIEWS };
}

// ---------- browser UI ----------

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const state = { rows: [], views: [], baseline: null, visitStarted: false, prefsReady: false, marks: emptyMarks(), sort: { ...DEFAULT_SORT }, openMap: null };

  function browserStorage(name) {
    try { const s = window[name]; s.getItem("flatscraper:probe"); return s; } catch { return memoryStorage(); }
  }
  const localStore = browserStorage("localStorage");
  const sessionStore = browserStorage("sessionStorage");

  const SORT_PRESETS = {
    newest: ["firstSeen", "desc"],
    oldest: ["firstSeen", "asc"],
    "price-asc": ["price", "asc"],
    "price-desc": ["price", "desc"],
    "area-desc": ["area", "desc"],
    "ppm-asc": ["ppm", "asc"],
    "rooms-desc": ["rooms", "desc"],
  };

  // The three views are radio buttons styled as a segmented control.
  const getView = () => (document.querySelector('input[name="show"]:checked') || {}).value || "current";
  function setView(view) {
    const radio = document.querySelector(`input[name="show"][value="${VIEWS.includes(view) ? view : "current"}"]`);
    if (radio) radio.checked = true;
  }

  function readFilters() {
    const n = (id) => (($(id).value || "").trim() === "" ? null : Number($(id).value));
    return {
      q: $("q").value, site: $("site").value,
      districts: [...document.querySelectorAll("#districtPanel input:checked")].map((i) => i.value),
      maxPrice: n("maxPrice"), minArea: n("minArea"), minRooms: n("minRooms"),
      onlyNew: $("onlyNew").checked, since: state.baseline,
      view: getView(), marks: state.marks,
    };
  }

  function el(tag, props = {}, text) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    if (text != null) node.textContent = text;
    return node;
  }

  // ----- filter panel: a dropdown under the toolbar on desktop, a bottom sheet on phones -----

  function setPanel(open) {
    $("filterPanel").hidden = !open;
    $("backdrop").hidden = !open; // only visible on phones (CSS)
    $("filtersToggle").setAttribute("aria-expanded", String(open));
    document.body.classList.toggle("sheet-open", open);
    if (open) $("site").focus({ preventScroll: true });
  }
  const panelOpen = () => !$("filterPanel").hidden;

  function clearPanelFilters() {
    $("site").value = "";
    document.querySelectorAll("#districtPanel input").forEach((i) => { i.checked = false; });
    $("maxPrice").value = "";
    $("minArea").value = "";
    $("minRooms").value = "";
    $("onlyNew").checked = false;
  }

  /** Clear the one control a chip stands for. */
  function removeFilter(chip) {
    if (chip.key === "district") {
      document.querySelectorAll("#districtPanel input").forEach((i) => { if (i.value === chip.value) i.checked = false; });
    } else if (chip.key === "onlyNew") $("onlyNew").checked = false;
    else $(chip.key).value = "";
    render();
  }

  function renderChips(filters) {
    const chips = activeFilterChips(filters);
    const frag = document.createDocumentFragment();
    chips.forEach((chip) => {
      const b = el("button", { type: "button", className: "chip-x" }, `${chip.label} ✕`);
      b.setAttribute("aria-label", `Usuń filtr: ${chip.label}`);
      b.addEventListener("click", () => removeFilter(chip));
      frag.append(b);
    });
    if (chips.length > 1) {
      const all = el("button", { type: "button", className: "chip-clear" }, "Wyczyść wszystkie");
      all.addEventListener("click", () => { clearPanelFilters(); render(); });
      frag.append(all);
    }
    $("chips").replaceChildren(frag);
    $("chips").hidden = chips.length === 0;
    $("filterCount").textContent = String(chips.length);
    $("filterCount").hidden = chips.length === 0;
  }

  // ----- rows -----

  function portalChips(r) {
    const box = el("div", { className: "portals" });
    r.members.forEach((m) => {
      const name = SITE_NAMES[m.site] || m.site;
      const href = safeUrl(m.url);
      box.append(href
        ? el("a", { className: "chip", href, target: "_blank", rel: "noopener noreferrer" }, name)
        : el("span", { className: "chip" }, name));
    });
    return box;
  }

  let toastTimer = null;
  function hideToast() { clearTimeout(toastTimer); $("toast").hidden = true; state.undoMarks = null; }

  /** A mis-tap on ✕ is easy on a phone, so hiding offers an undo for a few seconds. */
  function showUndo(text, previous) {
    state.undoMarks = previous;
    $("toastText").textContent = text;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 8000);
  }

  function setMarkAndRender(view, mode) {
    const previous = state.marks;
    state.marks = setMark(state.marks, view, mode, Date.now());
    saveMarks(localStore, state.marks);
    if (mode === "hidden") showUndo("Ogłoszenie ukryte.", previous); else hideToast();
    render();
  }

  function actionsCell(r, mark, showing) {
    const td = el("td", { className: "actions" });
    const fav = el("button", { type: "button", className: "act fav", textContent: mark === "fav" ? "★" : "☆" });
    fav.setAttribute("aria-pressed", String(mark === "fav"));
    fav.setAttribute("aria-label", `${mark === "fav" ? "Usuń z ulubionych" : "Dodaj do ulubionych"}: ${r.title}`);
    fav.title = mark === "fav" ? "Usuń z ulubionych" : "Dodaj do ulubionych";
    fav.addEventListener("click", () => setMarkAndRender(r, mark === "fav" ? null : "fav"));
    const hide = el("button", { type: "button", className: "act hide", textContent: showing === "hidden" ? "↩" : "✕" });
    const hideText = showing === "hidden" ? "Przywróć ogłoszenie" : "Ukryj ogłoszenie";
    hide.setAttribute("aria-label", `${hideText}: ${r.title}`);
    hide.title = hideText;
    hide.addEventListener("click", () => setMarkAndRender(r, showing === "hidden" ? null : "hidden"));
    td.append(fav, hide);
    return td;
  }

  /** Fixed-size box, so rows don't jump while pictures load. A missing or dead image leaves a plain placeholder. */
  function photoCell(r) {
    const td = el("td", { className: "photo" });
    const href = safeUrl(r.url);
    // the title is the accessible link; the picture is only a bigger click target for the same page
    const box = href
      ? el("a", { className: "thumb", href, target: "_blank", rel: "noopener noreferrer", tabIndex: -1 })
      : el("span", { className: "thumb" });
    box.setAttribute("aria-hidden", "true");
    const sources = photoSources(r.photo);
    if (sources) {
      const img = el("img", { src: sources.src, alt: "", loading: "lazy", decoding: "async", referrerPolicy: "no-referrer" });
      if (sources.srcset) { img.srcset = sources.srcset; img.sizes = PHOTO_SIZES; }
      img.addEventListener("error", () => {
        if (img.hasAttribute("srcset")) { // the larger variant failed: fall back to the stored picture once
          img.removeAttribute("srcset");
          img.removeAttribute("sizes");
          img.src = sources.src;
          return;
        }
        img.remove(); // portals rotate image URLs; keep the placeholder
        td.classList.add("no-photo");
      });
      box.append(img);
    } else td.classList.add("no-photo");
    td.append(box);
    return td;
  }

  // ----- map: one map open at a time, as an extra row under its listing -----

  const rowKey = (r) => markKey(r.members[0]);
  const safeId = (key) => key.replace(/[^\w-]/g, "_");
  const mapRowId = (key) => `map-${safeId(key)}`;
  const mapButtonId = (key) => `mapbtn-${safeId(key)}`;

  function toggleMap(r) {
    const key = rowKey(r);
    state.openMap = state.openMap === key ? null : key;
    render();
    const button = document.getElementById(mapButtonId(key));
    if (button) button.focus({ preventScroll: true }); // render() replaced the button the keyboard was on
    const row = state.openMap && document.getElementById(mapRowId(key));
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function mapButton(r) {
    const key = rowKey(r), open = state.openMap === key;
    const b = el("button", { type: "button", className: "chip map-btn", id: mapButtonId(key) }, open ? "Ukryj mapę ▴" : "Mapa ▾");
    b.setAttribute("aria-expanded", String(open));
    if (open) b.setAttribute("aria-controls", mapRowId(key));
    b.addEventListener("click", () => toggleMap(r));
    return b;
  }

  /** The row under an open listing: how exact the pin is, the map, and links to open it in a full map app. */
  function mapRow(r) {
    const src = mapEmbedUrl(r.loc), links = mapLinks(r.loc);
    if (!src || !links) return null;
    const tr = el("tr", { className: "map-row", id: mapRowId(rowKey(r)) });
    const box = el("div", { className: "map-box" });
    box.append(el("p", { className: "map-note" }, mapCaption(r.loc, [r.district, streetLabel(r)].filter(Boolean).join(" · "))));
    // Not "no-referrer", unlike the photos: OpenStreetMap's tile servers refuse requests without a Referer (403 "Access blocked"),
    // and its usage policy asks for one. The default sends only this site's origin, never the path or any search text.
    const frame = el("iframe", { className: "map-frame", src, title: `Mapa: ${r.title || "ogłoszenie"}`, loading: "lazy", referrerPolicy: "strict-origin-when-cross-origin" });
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin"); // OpenStreetMap's own page needs its scripts, nothing else
    box.append(frame);
    const open = (href, text) => el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
    const more = el("p", { className: "map-links" });
    more.append(open(links.google, "Otwórz w Google Maps ↗"), open(links.osm, "Otwórz w OpenStreetMap ↗"));
    box.append(more);
    tr.append(el("td", { colSpan: 6 }));
    tr.firstChild.append(box);
    return tr;
  }

  /** Title (with the NOWE badge), a muted line "district · street · added", and the portals. */
  function mainCell(r, now) {
    const td = el("td", { className: "main" });
    const line = el("div", { className: "line" });
    const href = safeUrl(r.url);
    const title = r.title || "(bez tytułu)";
    if (isNew(r, now, state.baseline)) line.append(el("span", { className: "badge" }, "NOWE"));
    line.append(href
      ? el("a", { className: "title-link", href, target: "_blank", rel: "noopener noreferrer" }, title)
      : el("span", { className: "title-link" }, title));
    const when = r.gone ? `ostatnio widziano: ${relativeTime(r.lastSeen, now)}` : relativeTime(r.firstSeen, now);
    const portals = portalChips(r);
    if (r.loc) portals.append(mapButton(r)); // no location, no button: the page never shows a map it cannot place
    td.append(line, el("div", { className: "meta" }, [r.district, streetLabel(r), when].filter(Boolean).join(" · ")), portals);
    return td;
  }

  function priceCell(r) {
    const td = el("td", { className: "price" });
    td.append(el("span", { className: "price-main" }, formatPrice(r.price)));
    if (r.ppm != null) td.append(el("span", { className: "price-sub" }, formatPrice(r.ppm).replace(" zł", " zł/m²")));
    return td;
  }

  function renderRow(r, now, showing) {
    const mark = markState(r, state.marks);
    const open = r.loc && state.openMap === rowKey(r);
    const tr = el("tr", { className: [r.gone ? "is-gone" : mark === "hidden" ? "is-hidden" : "", open ? "is-open" : ""].filter(Boolean).join(" ") });
    tr.append(
      photoCell(r),
      mainCell(r, now),
      priceCell(r),
      el("td", { className: "num area" }, formatArea(r.area)),
      el("td", { className: "num rooms" }, r.rooms == null ? "—" : String(r.rooms)),
      actionsCell(r, mark, showing),
    );
    return tr;
  }

  function render() {
    const now = Date.now();
    const filters = readFilters();
    const gone = goneViews(state.marks, state.rows);
    const pool = filters.view === "fav" ? state.views.concat(gone) : state.views;
    const rows = sortRows(applyFilters(pool, filters, now), state.sort.key, state.sort.dir);
    const countMarked = (mode) => state.views.filter((v) => markState(v, state.marks) === mode).length;
    $("favTab").textContent = `Ulubione (${countMarked("fav") + gone.length})`;
    $("hiddenTab").textContent = `Ukryte (${countMarked("hidden")})`;
    renderChips(filters);
    if (state.openMap && !rows.some((r) => r.loc && rowKey(r) === state.openMap)) state.openMap = null; // its row is gone
    const frag = document.createDocumentFragment();
    rows.forEach((r) => {
      frag.append(renderRow(r, now, filters.view));
      const map = r.loc && state.openMap === rowKey(r) ? mapRow(r) : null;
      if (map) frag.append(map);
    });
    $("rows").replaceChildren(frag);
    const merged = state.rows.length - state.views.length;
    const newCount = state.views.filter((v) => isNew(v, now, state.baseline)).length;
    $("count").textContent = `Pokazano ${rows.length} z ${state.views.length}` +
      (merged > 0 ? ` · połączono ${merged} ${plural(merged, "duplikat", "duplikaty", "duplikatów")}` : "") +
      ` · ${state.baseline != null ? "nowe od ostatniej wizyty" : "nowe (24 h)"}: ${newCount}`;
    $("panelDone").textContent = `Pokaż ${rows.length} ${plural(rows.length, "ogłoszenie", "ogłoszenia", "ogłoszeń")}`;
    $("empty").hidden = rows.length > 0;
    $("empty").textContent = filters.onlyNew && newCount === 0
      ? "Nic nowego od ostatniej wizyty. Wyłącz „Tylko nowe”, żeby zobaczyć wszystkie ogłoszenia."
      : "Brak ogłoszeń spełniających kryteria.";
    document.querySelectorAll("th[data-key]").forEach((th) => {
      const active = th.dataset.key === state.sort.key;
      th.setAttribute("aria-sort", active ? (state.sort.dir === "asc" ? "ascending" : "descending") : "none");
    });
    const preset = Object.entries(SORT_PRESETS).find(([, [k, d]]) => k === state.sort.key && d === state.sort.dir);
    $("sort").value = preset ? preset[0] : "custom";
    if (state.prefsReady) savePrefs(localStore, prefsFromState(filters, $("merge").checked, state.sort));
  }

  /** Put remembered values back into the controls (after the options they refer to exist). */
  function applyPrefs(p) {
    $("site").value = p.site;
    document.querySelectorAll("#districtPanel input").forEach((i) => { i.checked = p.districts.includes(i.value); });
    $("maxPrice").value = p.maxPrice ?? "";
    $("minArea").value = p.minArea ?? "";
    $("minRooms").value = p.minRooms == null ? "" : String(p.minRooms);
    $("onlyNew").checked = p.onlyNew;
    setView(p.view);
    $("merge").checked = p.merge;
    state.sort = p.sort;
  }

  const plural = (n, one, few, many) =>
    n === 1 ? one : n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? few : many;

  function rebuild() {
    state.views = buildRows(state.rows, $("merge").checked);
    render();
  }

  const districtLabel = (v) => (v === NO_DISTRICT ? "Brak dzielnicy" : v);

  function buildDistrictPanel() {
    const counts = new Map();
    state.rows.forEach((r) => { const k = r.district ?? NO_DISTRICT; counts.set(k, (counts.get(k) || 0) + 1); });
    const keys = [...counts.keys()].filter((k) => k !== NO_DISTRICT).sort((a, b) => a.localeCompare(b, "pl"));
    if (counts.has(NO_DISTRICT)) keys.push(NO_DISTRICT);
    const frag = document.createDocumentFragment();
    keys.forEach((k) => {
      const label = el("span", {}, districtLabel(k));
      label.append(el("small", {}, String(counts.get(k))));
      const tog = el("label", { className: "tog" });
      tog.append(el("input", { type: "checkbox", value: k }), label);
      frag.append(tog);
    });
    $("districtPanel").replaceChildren(frag);
  }

  function fillSelect(select, values, labelOf = (v) => v) {
    select.querySelectorAll("option:not([value=''])").forEach((o) => o.remove());
    values.forEach((v) => select.append(el("option", { value: v }, labelOf(v))));
  }

  function setData(records) {
    // A reload (after a manual update) rebuilds the site and district controls, which would reset them: keep what is set now.
    const kept = state.prefsReady ? { ...prefsFromState(readFilters(), $("merge").checked, state.sort), view: getView() } : null;
    state.rows = records.map(normalize);
    const unique = (key) => [...new Set(state.rows.map((r) => r[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pl"));
    fillSelect($("site"), unique("site"), (s) => SITE_NAMES[s] || s);
    buildDistrictPanel();
    if (kept) {
      const known = { sites: unique("site"), districts: [...new Set(state.rows.map((r) => r.district ?? NO_DISTRICT))] };
      applyPrefs({ ...sanitizePrefs(kept, known), view: kept.view });
    }
    const cities = unique("city");
    $("city").textContent = cities.length === 1 ? ` — ${cities[0]}` : "";
    const last = Math.max(0, ...state.rows.map((r) => r.lastSeen || 0));
    const status = $("updated");
    status.textContent = last ? `dane z ${formatDate(last)}` : "";
    if (last && Date.now() - last > STALE_AFTER_MS) {
      status.textContent += " · ⚠ dane mogą być nieaktualne";
      status.classList.add("warn");
    }
    if (!state.visitStarted) { // once per page load, after the data is on screen
      state.visitStarted = true;
      state.baseline = startVisit(localStore, sessionStore, last);
      $("onlyNewHint").textContent = state.baseline != null
        ? `(od ostatniej wizyty: ${describeSince(state.baseline, Date.now())})` : "(24 h)";
    }
    if (!state.marksLoaded) { // favourites: load, then refresh their snapshots from the live data
      state.marksLoaded = true;
      const refreshed = refreshSnapshots(loadMarks(localStore), state.rows);
      state.marks = refreshed.marks;
      if (refreshed.changed) saveMarks(localStore, state.marks);
    }
    if (!state.prefsReady) {
      const known = { sites: unique("site"), districts: [...new Set(state.rows.map((r) => r.district ?? NO_DISTRICT))] };
      applyPrefs(loadPrefs(localStore, known));
      state.prefsReady = true;
    }
    $("app").hidden = false;
    $("status").hidden = true;
    $("refresh").hidden = false;
    rebuild();
  }

  async function fetchText(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.text();
  }

  async function load() {
    $("status").hidden = false;
    $("status-text").textContent = "Ładowanie…";
    $("retry").hidden = true;
    try {
      let text;
      try { text = await fetchText(RAW_URL); }
      catch { text = await fetchText(LOCAL_URL); }
      setData(parseCSV(text));
    } catch (err) {
      $("status-text").textContent = "Nie udało się wczytać danych.";
      $("retry").hidden = false;
      console.error(err);
    }
  }

  // ----- manual update -----

  const POLL_MS = 10 * 1000;
  const GIVE_UP_MS = 15 * 60 * 1000; // the workflow's own timeout-minutes
  const NO_RUN_MS = 2 * 60 * 1000;   // a dispatch whose run never shows up
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const storedToken = () => cleanToken(readJSON(localStore, TOKEN_KEY));
  function setRefreshStatus(text, link) {
    const box = $("refreshStatus");
    box.replaceChildren(text);
    const href = link && safeUrl(link);
    if (href) box.append(" ", el("a", { href, target: "_blank", rel: "noopener noreferrer" }, "Zobacz przebieg"));
  }
  function syncToken() { $("tokenForget").hidden = !storedToken(); }
  function openTokenForm(message) {
    $("tokenForm").hidden = false;
    $("tokenError").textContent = message || "";
    $("tokenInput").value = "";
    $("tokenInput").focus();
  }
  function closeTokenForm() { $("tokenForm").hidden = true; $("tokenInput").value = ""; }
  function forgetToken() { try { localStore.removeItem(TOKEN_KEY); } catch { /* storage blocked: nothing to forget */ } syncToken(); }

  async function api(url, token, init = {}) {
    const res = await fetch(url, { cache: "no-store", ...init, headers: { ...apiHeaders(token), ...(init.headers || {}) } });
    return res;
  }

  /** Start the workflow, follow its run, then show the data it committed. Resolves when everything has settled. */
  async function startRefresh(token) {
    if (state.refreshing) return;
    state.refreshing = true;
    $("refresh").disabled = true;
    try {
      setRefreshStatus("Uruchamianie…");
      const t0 = Date.now();
      const { url, init } = dispatchRequest(token);
      const started = await api(url, token, init);
      if (started.status === 401 || started.status === 403 || started.status === 404) {
        forgetToken();
        setRefreshStatus("");
        openTokenForm("GitHub odrzucił token (potrzebne: dostęp do tego repozytorium i uprawnienie Actions: Read and write).");
        return;
      }
      if (started.status !== 204) throw new Error(`HTTP ${started.status}`);

      let run = null;
      while (true) {
        await sleep(POLL_MS);
        const elapsed = Date.now() - t0;
        const res = await api(runsUrl(), token);
        if (res.ok) run = pickRun((await res.json()).workflow_runs, t0);
        const phase = runPhase(run);
        setRefreshStatus(phase.label, run && run.html_url);
        if (phase.done) {
          if (!phase.ok) return;
          break;
        }
        if (!run && elapsed > NO_RUN_MS) { setRefreshStatus("Nie widać uruchomienia — sprawdź zakładkę Actions w repozytorium."); return; }
        if (elapsed > GIVE_UP_MS) { setRefreshStatus("Aktualizacja trwa zbyt długo — sprawdź zakładkę Actions.", run && run.html_url); return; }
      }

      const head = await api(headCommitUrl(), token);
      const csvUrl = head.ok ? freshCsvUrl((await head.json()).sha) : null;
      const before = new Set(state.rows.map(markKey));
      setData(parseCSV(await fetchText(csvUrl || RAW_URL)));
      const added = state.rows.filter((r) => !before.has(markKey(r))).length;
      setRefreshStatus(added > 0 ? `Zaktualizowano · nowych ogłoszeń: ${added}.` : "Zaktualizowano · brak nowych ogłoszeń.");
    } catch (err) {
      console.error(err);
      setRefreshStatus("Nie udało się zaktualizować. Spróbuj ponownie za chwilę.");
    } finally {
      state.refreshing = false;
      $("refresh").disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("refresh").addEventListener("click", () => {
      const token = storedToken();
      if (token) startRefresh(token); else openTokenForm();
    });
    $("tokenForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const token = cleanToken($("tokenInput").value);
      if (!token) { $("tokenError").textContent = "To nie wygląda na token GitHub (zaczyna się od github_pat_)."; return; }
      writeJSON(localStore, TOKEN_KEY, token);
      closeTokenForm();
      syncToken();
      startRefresh(token);
    });
    $("tokenCancel").addEventListener("click", closeTokenForm);
    $("tokenForget").addEventListener("click", () => { forgetToken(); setRefreshStatus("Token usunięty z tej przeglądarki."); });
    syncToken();
    ["q", "site", "maxPrice", "minArea", "minRooms", "onlyNew"].forEach((id) => {
      $(id).addEventListener("input", render);
    });
    document.querySelectorAll('input[name="show"]').forEach((radio) => radio.addEventListener("change", render));
    $("districtPanel").addEventListener("change", render);
    $("filtersToggle").addEventListener("click", () => setPanel(!panelOpen()));
    $("panelDone").addEventListener("click", () => { setPanel(false); $("filtersToggle").focus(); });
    $("backdrop").addEventListener("click", () => setPanel(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && panelOpen()) { setPanel(false); $("filtersToggle").focus(); }
    });
    $("clearFilters").addEventListener("click", () => { clearPanelFilters(); render(); });
    $("merge").addEventListener("input", rebuild);
    $("sort").addEventListener("change", (e) => {
      const preset = SORT_PRESETS[e.target.value];
      if (preset) { state.sort = { key: preset[0], dir: preset[1] }; render(); }
    });
    document.querySelectorAll("th[data-key] button").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.closest("th").dataset.key;
        const first = key === "price" || key === "ppm" || key === "title" || key === "district" || key === "site" ? "asc" : "desc";
        state.sort = state.sort.key === key
          ? { key, dir: state.sort.dir === "asc" ? "desc" : "asc" }
          : { key, dir: first };
        render();
      });
    });
    $("toastUndo").addEventListener("click", () => {
      if (state.undoMarks) { state.marks = state.undoMarks; saveMarks(localStore, state.marks); }
      hideToast();
      render();
    });
    $("retry").addEventListener("click", load);
    load();
  });
}
