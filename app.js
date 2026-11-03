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
    url: record.url,
    title: record.title,
    price,
    area,
    ppm: price != null && area ? price / area : null,
    rooms: num(record.rooms),
    district: record.district || null,
    city: record.city || null,
    firstSeen: ts(record.first_seen),
    lastSeen: ts(record.last_seen),
  };
}

const fold = (s) => (s || "").toLocaleLowerCase("pl");

/** Like the scrapers' filters, a missing value never excludes a listing. */
function applyFilters(rows, f, now) {
  const q = fold(f.q).trim();
  return rows.filter((r) => {
    if (q && !(fold(r.title).includes(q) || fold(r.district).includes(q))) return false;
    if (f.site && r.site !== f.site) return false;
    if (f.districts && f.districts.length && !f.districts.includes(r.district ?? NO_DISTRICT)) return false;
    if (f.maxPrice != null && r.price != null && r.price > f.maxPrice) return false;
    if (f.minArea != null && r.area != null && r.area < f.minArea) return false;
    if (f.minRooms != null && r.rooms != null && r.rooms < f.minRooms) return false;
    if (f.onlyNew && !(r.firstSeen != null && now - r.firstSeen <= NEW_WINDOW_MS)) return false;
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

if (typeof module !== "undefined") {
  module.exports = { parseCSV, normalize, applyFilters, sortRows, formatPrice, formatArea, relativeTime, safeUrl, NEW_WINDOW_MS, NO_DISTRICT };
}

// ---------- browser UI ----------

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const state = { rows: [], sort: { key: "firstSeen", dir: "desc" } };

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
      onlyNew: $("onlyNew").checked,
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

  function renderRow(r, now) {
    const tr = el("tr");
    const titleTd = el("td", { className: "title" });
    const href = safeUrl(r.url);
    const title = r.title || "(bez tytułu)";
    if (r.firstSeen != null && now - r.firstSeen <= NEW_WINDOW_MS) titleTd.append(el("span", { className: "badge" }, "NOWE"));
    titleTd.append(href ? el("a", { href, target: "_blank", rel: "noopener noreferrer" }, title) : el("span", {}, title));
    tr.append(
      titleTd,
      cell("num price", formatPrice(r.price), "Cena"),
      cell("num area", formatArea(r.area), "Powierzchnia"),
      cell("num ppm", r.ppm == null ? "—" : formatPrice(r.ppm).replace(" zł", " zł/m²"), "zł/m²"),
      cell("num rooms", r.rooms == null ? "—" : String(r.rooms), "Pokoje"),
      cell("district", r.district || "—", "Dzielnica"),
      cell("site", SITE_NAMES[r.site] || r.site, "Portal"),
      cell("added", relativeTime(r.firstSeen, now), "Dodano"),
    );
    return tr;
  }

  function render() {
    const now = Date.now();
    const filters = readFilters();
    const rows = sortRows(applyFilters(state.rows, filters, now), state.sort.key, state.sort.dir);
    updateDistrictSummary(filters.districts);
    const body = $("rows");
    const frag = document.createDocumentFragment();
    rows.forEach((r) => frag.append(renderRow(r, now)));
    body.replaceChildren(frag);
    $("count").textContent = `Pokazano ${rows.length} z ${state.rows.length}`;
    $("empty").hidden = rows.length > 0;
    document.querySelectorAll("th[data-key]").forEach((th) => {
      const active = th.dataset.key === state.sort.key;
      th.setAttribute("aria-sort", active ? (state.sort.dir === "asc" ? "ascending" : "descending") : "none");
    });
    const preset = Object.entries(SORT_PRESETS).find(([, [k, d]]) => k === state.sort.key && d === state.sort.dir);
    $("sort").value = preset ? preset[0] : "custom";
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
    $("app").hidden = false;
    $("status").hidden = true;
    render();
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
    ["q", "site", "district", "maxPrice", "minArea", "minRooms", "onlyNew"].forEach((id) => {
      $(id).addEventListener("input", render);
    });
    // close the district dropdown on an outside click or Escape
    document.addEventListener("click", (e) => { if (!$("district").contains(e.target)) $("district").open = false; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("district").open = false; });
    $("clearFilters").addEventListener("click", () => {
      $("filters").reset();
      render();
    });
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
    $("retry").addEventListener("click", load);
    load();
  });
}
