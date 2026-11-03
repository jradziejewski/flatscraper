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
    photo: safeUrl(record.photo), // listing data comes from scraped sites: only http(s) ever reaches an <img src>
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
    firstSeen: Number.isFinite(earliest) ? earliest : null, // a flat is only "new" if its earliest listing is
    photo: ordered.find((m) => m.photo)?.photo ?? null, // the first portal (by priority) that has a picture
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
    district: m.district, street: m.street, photo: m.photo ?? null, firstSeen: m.firstSeen, lastSeen: m.lastSeen, group, savedAt,
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
        street: isText(f.street) ? f.street : null, photo: isText(f.photo) ? safeUrl(f.photo) : null,
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

if (typeof module !== "undefined") {
  module.exports = { parseCSV, normalize, applyFilters, sortRows, formatPrice, formatArea, relativeTime, safeUrl, NEW_WINDOW_MS, NO_DISTRICT, groupDuplicates, buildRows, streetsMatch,
    isNew, startVisit, describeSince, memoryStorage, readJSON, writeJSON,
    prefsFromState, sanitizePrefs, loadPrefs, savePrefs, PREFS_KEY,
    markKey, emptyMarks, loadMarks, saveMarks, setMark, markState, refreshSnapshots, goneViews, capMarks, sanitizeMarks, MAX_MARKS, FAVS_KEY, HIDDEN_KEY, VIEWS };
}

// ---------- browser UI ----------

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const state = { rows: [], views: [], baseline: null, visitStarted: false, prefsReady: false, marks: emptyMarks(), sort: { ...DEFAULT_SORT } };

  function browserStorage(name) {
    try { const s = window[name]; s.getItem("flatscraper:probe"); return s; } catch { return memoryStorage(); }
  }
  const localStore = browserStorage("localStorage");
  const sessionStore = browserStorage("sessionStorage");

  const SORT_PRESETS = {
    newest: ["firstSeen", "desc"],
    "price-asc": ["price", "asc"],
    "price-desc": ["price", "desc"],
    "area-desc": ["area", "desc"],
    "ppm-asc": ["ppm", "asc"],
  };

  function readFilters() {
    const n = (id) => (($(id).value || "").trim() === "" ? null : Number($(id).value));
    return {
      q: $("q").value, site: $("site").value,
      districts: [...document.querySelectorAll("#districtPanel input:checked")].map((i) => i.value),
      maxPrice: n("maxPrice"), minArea: n("minArea"), minRooms: n("minRooms"),
      onlyNew: $("onlyNew").checked, since: state.baseline,
      view: $("show").value, marks: state.marks,
    };
  }

  function el(tag, props = {}, text) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    if (text != null) node.textContent = text;
    return node;
  }

  function cell(cls, text, label) {
    const td = el("td", { className: cls }, text);
    td.dataset.label = label;
    return td;
  }

  function siteCell(r) {
    const td = el("td", { className: "site" });
    td.dataset.label = "Portal";
    r.members.forEach((m) => {
      const name = SITE_NAMES[m.site] || m.site;
      const href = safeUrl(m.url);
      td.append(href
        ? el("a", { className: "chip", href, target: "_blank", rel: "noopener noreferrer" }, name)
        : el("span", { className: "chip" }, name));
    });
    return td;
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

  /** Fixed-size box, so rows don't jump while pictures load. A missing or broken picture leaves the empty box. */
  function photoCell(r) {
    const td = el("td", { className: "photo" });
    const href = safeUrl(r.url);
    // the title is the accessible link; the picture is only a bigger click target for the same page
    const box = href
      ? el("a", { className: "thumb", href, target: "_blank", rel: "noopener noreferrer", tabIndex: -1 })
      : el("span", { className: "thumb" });
    box.setAttribute("aria-hidden", "true");
    const src = safeUrl(r.photo);
    if (src) {
      const img = el("img", {
        src, alt: "", width: 96, height: 72, loading: "lazy", decoding: "async", referrerPolicy: "no-referrer",
      });
      img.addEventListener("error", () => img.remove()); // portals rotate image URLs; keep the empty box
      box.append(img);
    }
    td.append(box);
    return td;
  }

  function renderRow(r, now, showing) {
    const mark = markState(r, state.marks);
    const tr = el("tr", { className: r.gone ? "is-gone" : mark === "hidden" ? "is-hidden" : "" });
    const titleTd = el("td", { className: "title" });
    const href = safeUrl(r.url);
    const title = r.title || "(bez tytułu)";
    if (isNew(r, now, state.baseline)) titleTd.append(el("span", { className: "badge" }, "NOWE"));
    titleTd.append(href ? el("a", { href, target: "_blank", rel: "noopener noreferrer" }, title) : el("span", {}, title));
    tr.append(
      actionsCell(r, mark, showing),
      photoCell(r),
      titleTd,
      cell("num price", formatPrice(r.price), "Cena"),
      cell("num area", formatArea(r.area), "Powierzchnia"),
      cell("num ppm", r.ppm == null ? "—" : formatPrice(r.ppm).replace(" zł", " zł/m²"), "zł/m²"),
      cell("num rooms", r.rooms == null ? "—" : String(r.rooms), "Pokoje"),
      cell("district", r.district || "—", "Dzielnica"),
      siteCell(r),
      cell("added", r.gone ? `ostatnio widziano: ${relativeTime(r.lastSeen, now)}` : relativeTime(r.firstSeen, now), "Dodano"),
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
    document.querySelector("#show option[value=fav]").textContent = `Ulubione (${countMarked("fav") + gone.length})`;
    document.querySelector("#show option[value=hidden]").textContent = `Ukryte (${countMarked("hidden")})`;
    updateDistrictSummary(filters.districts);
    const body = $("rows");
    const frag = document.createDocumentFragment();
    rows.forEach((r) => frag.append(renderRow(r, now, filters.view)));
    body.replaceChildren(frag);
    const merged = state.rows.length - state.views.length;
    const newCount = state.views.filter((v) => isNew(v, now, state.baseline)).length;
    $("count").textContent = `Pokazano ${rows.length} z ${state.views.length}` +
      (merged > 0 ? ` · połączono ${merged} ${plural(merged, "duplikat", "duplikaty", "duplikatów")}` : "") +
      ` · ${state.baseline != null ? "nowe od ostatniej wizyty" : "nowe (24 h)"}: ${newCount}`;
    $("empty").hidden = rows.length > 0;
    $("empty").textContent = filters.onlyNew && newCount === 0
      ? "Nic nowego od ostatniej wizyty. Odznacz „Tylko nowe”, żeby zobaczyć wszystkie ogłoszenia."
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
    $("show").value = p.view;
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
      const opt = el("label", { className: "opt" });
      opt.append(el("input", { type: "checkbox", value: k }), el("span", {}, districtLabel(k)), el("span", { className: "cnt" }, String(counts.get(k))));
      frag.append(opt);
    });
    $("districtPanel").replaceChildren(frag);
  }

  function updateDistrictSummary(selected) {
    $("districtSummary").textContent = selected.length === 0 ? "Wszystkie"
      : selected.length <= 2 ? selected.map(districtLabel).join(", ")
      : `${selected.length} wybrane`;
  }

  function fillSelect(select, values, labelOf = (v) => v) {
    select.querySelectorAll("option:not([value=''])").forEach((o) => o.remove());
    values.forEach((v) => select.append(el("option", { value: v }, labelOf(v))));
  }

  function setData(records) {
    state.rows = records.map(normalize);
    const unique = (key) => [...new Set(state.rows.map((r) => r[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pl"));
    fillSelect($("site"), unique("site"), (s) => SITE_NAMES[s] || s);
    buildDistrictPanel();
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

  document.addEventListener("DOMContentLoaded", () => {
    ["q", "site", "district", "maxPrice", "minArea", "minRooms", "onlyNew", "show"].forEach((id) => {
      $(id).addEventListener("input", render);
    });
    // close the district dropdown on an outside click or Escape
    document.addEventListener("click", (e) => { if (!$("district").contains(e.target)) $("district").open = false; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("district").open = false; });
    $("clearFilters").addEventListener("click", () => {
      $("filters").reset(); // also restores "Łącz duplikaty" to its default (on)
      rebuild();
    });
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
