import csv
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aptsearch.models import FIELDS

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "index.html").read_text(encoding="utf-8")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(script: str, payload=None):
    """Run `script` with app.js's exports available as `lib`; payload comes in as JSON on stdin."""
    wrapper = f"""
const lib = require({json.dumps(str(ROOT / 'app.js'))});
const input = JSON.parse(require('fs').readFileSync(0, 'utf8') || 'null');
const out = (() => {{ {script} }})();
process.stdout.write(JSON.stringify(out));
"""
    result = subprocess.run(
        ["node", "-e", wrapper], input=json.dumps(payload), capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


@needs_node
def test_js_parser_matches_python_csv_on_the_real_file():
    text = (ROOT / "listings.csv").read_text(encoding="utf-8")
    with open(ROOT / "listings.csv", encoding="utf-8", newline="") as f:
        expected = list(csv.DictReader(f))
    got = run_node("return lib.parseCSV(input);", text)
    assert len(got) == len(expected) > 0
    assert got == expected  # every field of every row, incl. titles with commas


@needs_node
def test_js_parser_edge_cases():
    text = 'a,b,c\r\n"x, y","say ""hi""",z\r\n"multi\nline",,\r\n'
    assert run_node("return lib.parseCSV(input);", text) == [
        {"a": "x, y", "b": 'say "hi"', "c": "z"},
        {"a": "multi\nline", "b": "", "c": ""},
    ]
    assert run_node("return lib.parseCSV(input);", "") == []
    assert run_node("return lib.parseCSV(input);", "a,b\n") == []


ROWS = [
    {"site": "otodom", "title": "Tani Rynek", "price_pln": "2000", "area_m2": "30", "rooms": "1", "district": "Stare Miasto", "first_seen": "2026-11-03T10:00:00Z", "last_seen": "2026-11-03T10:00:00Z"},
    {"site": "olx", "title": "Duże ŻOLIBORZ", "price_pln": "4000", "area_m2": "80", "rooms": "3", "district": "Krzyki", "first_seen": "2026-10-01T10:00:00Z", "last_seen": "2026-11-03T10:00:00Z"},
    {"site": "morizon", "title": "Bez danych", "price_pln": "", "area_m2": "", "rooms": "", "district": "", "first_seen": "2026-11-02T10:00:00Z", "last_seen": "2026-11-03T10:00:00Z"},
]
NOW = "Date.parse('2026-11-03T12:00:00Z')"


@needs_node
def test_filters_only_exclude_on_known_values():
    script = f"""
const rows = input.map(lib.normalize), now = {NOW};
const titles = (f) => lib.applyFilters(rows, f, now).map(r => r.title);
return {{
  maxPrice: titles({{maxPrice: 3000}}),
  minRooms: titles({{minRooms: 2}}),
  minArea: titles({{minArea: 50}}),
  site: titles({{site: 'olx'}}),
  district: titles({{districts: ['Krzyki']}}),
  multi: titles({{districts: ['Krzyki', 'Stare Miasto']}}),
  noDistrict: titles({{districts: [lib.NO_DISTRICT]}}),
  mixed: titles({{districts: ['Krzyki', lib.NO_DISTRICT]}}),
  allDistricts: titles({{districts: []}}),
  search: titles({{q: 'żoliborz'}}),
  onlyNew: titles({{onlyNew: true}}),
}};
"""
    out = run_node(script, ROWS)
    assert out["maxPrice"] == ["Tani Rynek", "Bez danych"]  # missing price passes
    assert out["minRooms"] == ["Duże ŻOLIBORZ", "Bez danych"]
    assert out["minArea"] == ["Duże ŻOLIBORZ", "Bez danych"]
    assert out["site"] == ["Duże ŻOLIBORZ"] and out["district"] == ["Duże ŻOLIBORZ"]
    assert out["multi"] == ["Tani Rynek", "Duże ŻOLIBORZ"]  # any of the chosen districts
    assert out["noDistrict"] == ["Bez danych"]  # the "Brak dzielnicy" option
    assert out["mixed"] == ["Duże ŻOLIBORZ", "Bez danych"]
    assert len(out["allDistricts"]) == 3  # nothing ticked = no district filter
    assert out["search"] == ["Duże ŻOLIBORZ"]  # case-insensitive, Polish letters
    assert out["onlyNew"] == ["Tani Rynek"]  # first seen within 24 h of "now"


@needs_node
def test_sort_puts_empty_values_last_in_both_directions():
    script = """
const rows = input.map(lib.normalize);
const t = (k, d) => lib.sortRows(rows, k, d).map(r => r.title);
return {asc: t('price', 'asc'), desc: t('price', 'desc'), newest: t('firstSeen', 'desc'), ppm: t('ppm', 'asc'), original: rows.map(r => r.title)};
"""
    out = run_node(script, ROWS)
    assert out["asc"] == ["Tani Rynek", "Duże ŻOLIBORZ", "Bez danych"]
    assert out["desc"] == ["Duże ŻOLIBORZ", "Tani Rynek", "Bez danych"]
    assert out["newest"] == ["Tani Rynek", "Bez danych", "Duże ŻOLIBORZ"]
    assert out["ppm"][-1] == "Bez danych"
    assert out["original"] == [r["title"] for r in ROWS]  # input is not reordered


@needs_node
def test_relative_time_and_safe_url():
    out = run_node(
        f"""const now = {NOW}, m = 60000;
return [lib.relativeTime(now - 10000, now), lib.relativeTime(now - 5 * m, now), lib.relativeTime(now - 125 * m, now),
        lib.relativeTime(now - 26 * 60 * m, now), lib.relativeTime(now - 3 * 24 * 60 * m, now), lib.relativeTime(null, now),
        lib.safeUrl('https://x.pl/a'), lib.safeUrl('javascript:alert(1)'), lib.safeUrl(''), lib.safeUrl('//evil.test')];"""
    )
    assert out == ["przed chwilą", "5 min temu", "2 godz. temu", "1 dzień temu", "3 dni temu", "—",
                   "https://x.pl/a", None, None, None]


def test_page_only_reads_columns_that_exist():
    normalize_src = re.search(r"function normalize\(record\) \{.*?\n\}\n", APP_JS, re.S).group(0)
    used = set(re.findall(r"\brecord\.(\w+)", normalize_src))
    assert used and used <= set(FIELDS), used - set(FIELDS)


@needs_node
def test_every_sortable_header_maps_to_a_normalized_key():
    keys = re.findall(r'<th[^>]*data-key="(\w+)"', INDEX_HTML)
    row = run_node("return lib.normalize(input);", {f: "" for f in FIELDS})
    assert keys and set(keys) <= set(row), set(keys) - set(row)


def test_every_element_the_script_looks_up_exists_in_the_page():
    wanted = set(re.findall(r'\$\("(\w+)"\)', APP_JS))
    # the filter inputs are wired from one list: ["q", "site", ...].forEach(...)
    filter_list = re.search(r'\[((?:"\w+",?\s*)+)\]\.forEach', APP_JS).group(1)
    wanted |= set(re.findall(r'"(\w+)"', filter_list))
    present = set(re.findall(r'\bid="(\w+)"', INDEX_HTML))
    assert len(wanted) > 10 and wanted <= present, wanted - present


def test_form_children_do_not_shadow_form_methods():
    # a child with id="reset" replaces form.reset (named access on forms), so reset() throws
    form = re.search(r'<form id="filters".*?</form>', INDEX_HTML, re.S).group(0)
    child_names = set(re.findall(r'\b(?:id|name)="(\w+)"', form)) - {"filters"}
    form_members = {"reset", "submit", "elements", "length", "action", "method", "target", "name", "id",
                    "enctype", "encoding", "acceptCharset", "autocomplete", "noValidate", "requestSubmit",
                    "checkValidity", "reportValidity"}
    assert child_names and not (child_names & form_members), child_names & form_members


def test_page_never_writes_untrusted_html():
    # listing fields come from scraped sites; everything must go through textContent
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", APP_JS)


def test_page_wiring():
    assert '<script src="app.js"></script>' in INDEX_HTML
    assert (ROOT / ".nojekyll").exists()
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote.stdout.strip())
    if remote.returncode or not m:
        pytest.skip("no GitHub origin remote")
    assert f"raw.githubusercontent.com/{m.group(1)}/main/listings.csv" in APP_JS


# ---------- duplicate merging ----------

def rec(site, id, **over):
    base = {"site": site, "id": id, "url": f"https://{site}.test/{id}", "title": f"Flat {id}", "price_pln": "3000",
            "area_m2": "45", "rooms": "2", "district": "Krzyki", "street": "", "first_seen": "2026-11-03T10:00:00Z",
            "last_seen": "2026-11-03T10:00:00Z"}
    return {**base, **over}


def groups(records, merge=True):
    """Run buildRows in node; returns [[(site, id), ...], ...] for each display row (representative first)."""
    script = """
const rows = input.records.map(lib.normalize).map((r, i) => ({...r, id: input.records[i].id}));
return lib.buildRows(rows, input.merge).map(v => ({rep: v.id, members: v.members.map(m => m.site + ':' + m.id), sites: v.sites,
  firstSeen: v.firstSeen, searchText: v.searchText}));
"""
    return run_node(script, {"records": records, "merge": merge})


def merged(records):
    return sorted(sorted(g["members"]) for g in groups(records) if len(g["members"]) > 1)


@needs_node
def test_merges_same_specs_on_the_same_street():
    out = merged([rec("otodom", "a", street="Hubska"), rec("morizon", "b", street="hubska")])
    assert out == [["morizon:b", "otodom:a"]]


@needs_node
@pytest.mark.parametrize("a, b", [
    (dict(street="Hubska"), dict(street="Nowowiejska")),                       # different streets: never merge
    (dict(street=""), dict(street="")),                                       # no street on either side: no evidence
    (dict(street="Hubska"), dict(street="Hubska", price_pln="3100")),         # different price
    (dict(street="Hubska"), dict(street="Hubska", rooms="3")),                # different rooms
    (dict(street="Hubska"), dict(street="Hubska", district="Fabryczna")),     # different district
    (dict(street="Hubska"), dict(street="Hubska", area_m2="47")),             # different area
    (dict(street="Hubska"), dict(street="Hubska", price_pln="")),             # missing spec: cannot compare
    (dict(street="Hubska"), dict(street="Hubska", district="")),
])
def test_does_not_merge_without_matching_specs_and_street(a, b):
    assert merged([rec("otodom", "a", **a), rec("morizon", "b", **b)]) == []


@needs_node
def test_area_is_compared_rounded_to_whole_square_metres():
    assert merged([rec("otodom", "a", street="Hubska", area_m2="45.4"), rec("morizon", "b", street="Hubska", area_m2="45")]) != []
    assert merged([rec("otodom", "a", street="Hubska", area_m2="45.6"), rec("morizon", "b", street="Hubska", area_m2="45")]) == []


@needs_node
def test_street_names_match_when_one_is_contained_in_the_other():
    out = merged([rec("otodom", "a", street="gen. Władysława Sikorskiego"), rec("nieruchomosci", "b", street="Sikorskiego")])
    assert out == [["nieruchomosci:b", "otodom:a"]]


@needs_node
def test_street_matching_ignores_abbreviated_ranks():
    script = "return input.map(([a, b]) => lib.streetsMatch(a, b));"
    out = run_node(script, [
        ["gen. Władysława Sikorskiego", "Generała Władysława Sikorskiego"], ["Sikorskiego", "Generała Władysława Sikorskiego"],
        ["ks. Kordeckiego", "Kordeckiego"], ["Plac Grunwaldzki", "pl. Grunwaldzki"],
        ["Hubska", "Nowowiejska"], ["Kościuszki", "Kościuszki Tadeusza Nowa"], ["ul. Os", "Os"], ["", "Hubska"],
    ])
    assert out == [True, True, True, True, False, True, False, False]


@needs_node
def test_a_listing_without_a_street_matches_only_if_its_title_names_the_street():
    named = rec("olx", "o", title="Mieszkanie na wynajem, Wrocław, ul. Kościuszki 118")
    unnamed = rec("olx", "p", title="Przytulne mieszkanie blisko centrum")
    assert merged([rec("otodom", "a", street="Tadeusza Kościuszki"), named]) == [["olx:o", "otodom:a"]]
    assert merged([rec("otodom", "a", street="Tadeusza Kościuszki"), unnamed]) == []
    # the URL slug counts as well as the title
    assert merged([rec("otodom", "a", street="Hubska"), rec("olx", "q", title="x", url="https://olx.test/d/oferta/mieszkanie-hubska-ID1.html")]) != []


@needs_node
def test_never_merges_two_listings_from_the_same_site():
    two_same_site = [rec("otodom", "a", street="Hubska"), rec("otodom", "b", street="Hubska"), rec("morizon", "c", street="Hubska")]
    assert merged(two_same_site) == []  # identical specs twice on one portal: probably distinct flats
    assert len(groups(two_same_site)) == 3


@needs_node
def test_three_portal_group_representative_and_first_seen():
    out = groups([
        rec("olx", "o", title="ul. Hubska 3", first_seen="2026-11-03T10:00:00Z"),
        rec("otodom", "a", street="Hubska", first_seen="2026-11-02T08:00:00Z"),
        rec("morizon", "m", street="Hubska", first_seen="2026-11-03T09:00:00Z"),
    ])
    assert len(out) == 1
    g = out[0]
    assert g["rep"] == "a"  # earliest first seen
    assert g["members"] == ["otodom:a", "morizon:m", "olx:o"]  # portal priority order
    assert g["firstSeen"] == run_node("return Date.parse('2026-11-02T08:00:00Z');")  # earliest of the three
    assert all(t in g["searchText"] for t in ("flat a", "flat m", "ul. hubska 3"))  # lowercased titles of all members


@needs_node
def test_representative_tie_goes_to_portal_priority():
    out = groups([rec("olx", "o", title="Hubska", first_seen="2026-11-03T10:00:00Z"), rec("morizon", "m", street="Hubska", first_seen="2026-11-03T10:00:00Z")])
    assert out[0]["rep"] == "m"


@needs_node
def test_turning_merging_off_shows_every_listing():
    records = [rec("otodom", "a", street="Hubska"), rec("morizon", "b", street="Hubska"), rec("olx", "c", price_pln="1")]
    assert len(groups(records, merge=True)) == 2
    assert len(groups(records, merge=False)) == 3


@needs_node
def test_filters_see_every_member_of_a_merged_row():
    script = """
const rows = input.map(lib.normalize).map((r, i) => ({...r, id: input[i].id}));
const views = lib.buildRows(rows, true), now = Date.parse('2026-11-03T12:00:00Z');
const ids = (f) => lib.applyFilters(views, f, now).map(v => v.id);
return {total: views.length, olx: ids({site: 'olx'}), morizon: ids({site: 'morizon'}), search: ids({q: 'LIPOWA'}),
        onlyNew: ids({onlyNew: true})};
"""
    # one flat cross-posted: first seen on otodom 3 days ago, on olx an hour ago
    out = run_node(script, [
        rec("otodom", "a", street="Lipowa", title="Stary wpis", first_seen="2026-10-31T10:00:00Z"),
        rec("olx", "o", title="ul. Lipowa 5", first_seen="2026-11-03T11:00:00Z"),
        rec("morizon", "m", price_pln="9000", first_seen="2026-11-03T11:30:00Z"),
    ])
    assert out["total"] == 2
    assert out["olx"] == ["a"] and out["morizon"] == ["m"]  # portal filter matches any member
    assert out["search"] == ["a"]  # text search covers every member's title
    assert out["onlyNew"] == ["m"]  # the merged flat is old: its earliest listing is 3 days old


@needs_node
def test_merging_on_the_real_file_only_ever_combines_identical_specs():
    text = (ROOT / "listings.csv").read_text(encoding="utf-8")
    script = """
const rows = lib.parseCSV(input).map(lib.normalize);
const views = lib.buildRows(rows, true);
const problems = [];
for (const v of views) {
  if (v.members.length < 2) continue;
  const sites = new Set(v.members.map(m => m.site));
  if (sites.size !== v.members.length) problems.push('repeated portal: ' + v.title);
  const key = (m) => [m.price, Math.round(m.area), m.rooms, m.district].join('|');
  if (new Set(v.members.map(key)).size !== 1) problems.push('specs differ: ' + v.title);
}
return {rows: rows.length, views: views.length, hidden: rows.length - views.length, problems};
"""
    out = run_node(script, text)
    assert out["problems"] == []
    assert out["views"] <= out["rows"] and out["hidden"] == out["rows"] - out["views"]


# ---------- new since the last visit ----------

T0 = "Date.parse('2026-11-04T12:00:00Z')"


@needs_node
def test_start_visit_uses_the_previous_visit_and_survives_a_refresh():
    out = run_node("""
const local = lib.memoryStorage(), session = lib.memoryStorage();
const d = (iso) => Date.parse(iso);
const first = lib.startVisit(local, session, d('2026-11-03T10:00:00Z'));       // first ever visit
const refresh = lib.startVisit(local, session, d('2026-11-03T10:00:00Z'));     // same tab refreshed
const newerData = lib.startVisit(local, session, d('2026-11-03T16:00:00Z'));   // same tab, newer data
const newSession = lib.startVisit(local, lib.memoryStorage(), d('2026-11-04T07:00:00Z'));  // next day, new tab
const another = lib.startVisit(local, lib.memoryStorage(), d('2026-11-04T08:00:00Z'));
return {first, refresh, newerData, newSession: new Date(newSession).toISOString(), another: new Date(another).toISOString()};
""")
    assert out["first"] is None  # nothing to compare with: the page falls back to "last 24 h"
    assert out["refresh"] is None and out["newerData"] is None  # the baseline is fixed for the whole tab session
    assert out["newSession"] == "2026-11-03T16:00:00.000Z"  # the data she saw at her previous visit
    assert out["another"] == "2026-11-04T07:00:00.000Z"


@needs_node
def test_start_visit_ignores_garbage_and_blocked_storage():
    out = run_node("""
const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
const garbage = lib.memoryStorage(); garbage.setItem('flatscraper:v1:lastVisit', '{not json');
const textual = lib.memoryStorage(); textual.setItem('flatscraper:v1:lastVisit', '"yesterday"');
return [lib.startVisit(broken, broken, 5), lib.startVisit(garbage, lib.memoryStorage(), 5), lib.startVisit(textual, lib.memoryStorage(), 5),
        lib.startVisit(lib.memoryStorage(), lib.memoryStorage(), 0)];
""")
    assert out == [None, None, None, None]


@needs_node
def test_is_new_with_and_without_a_baseline():
    out = run_node(f"""
const now = {T0}, h = 3600e3;
const row = (hoursAgo) => ({{firstSeen: now - hoursAgo * h}});
return {{
  fallback: [lib.isNew(row(2), now, null), lib.isNew(row(23), now, null), lib.isNew(row(25), now, null), lib.isNew({{firstSeen: null}}, now, null)],
  withBaseline: [lib.isNew(row(2), now, now - 5 * h), lib.isNew(row(5), now, now - 5 * h), lib.isNew(row(30), now, now - 40 * h), lib.isNew(row(30), now, now - 5 * h)],
  filter: lib.applyFilters([row(2), row(30)], {{onlyNew: true, since: now - 40 * h}}, now).length,
  filterFallback: lib.applyFilters([row(2), row(30)], {{onlyNew: true}}, now).length,
}};
""")
    assert out["fallback"] == [True, True, False, False]
    assert out["withBaseline"] == [True, False, True, False]  # strictly after the baseline
    assert out["filter"] == 2 and out["filterFallback"] == 1  # a 40 h-old baseline reaches the 30 h-old flat


@needs_node
def test_describe_since():
    out = run_node(f"""
const now = Date.parse('2026-11-04T12:00:00Z');   // 13:00 in Warsaw
const d = (iso) => lib.describeSince(Date.parse(iso), now);
return [d('2026-11-04T09:30:00Z'), d('2026-11-03T17:30:00Z'), d('2026-10-30T09:00:00Z')];
""")
    assert out[0] == "dziś, 10:30" and out[1] == "wczoraj, 18:30"
    assert out[2].startswith("30 paź") and out[2].endswith("10:00")


# ---------- remembered filters ----------

KNOWN = "{sites: ['olx', 'otodom'], districts: ['Krzyki', 'Fabryczna', lib.NO_DISTRICT]}"


@needs_node
def test_prefs_round_trip():
    out = run_node(f"""
const known = {KNOWN};
const storage = lib.memoryStorage();
const filters = {{q: 'balkon', site: 'olx', districts: ['Krzyki', lib.NO_DISTRICT], maxPrice: 3500, minArea: 40, minRooms: 2, onlyNew: true, since: 123, view: 'fav', marks: {{}}}};
const prefs = lib.prefsFromState(filters, false, {{key: 'price', dir: 'asc'}});
const saved = lib.savePrefs(storage, prefs);
return {{saved, prefs, loaded: lib.loadPrefs(storage, known), raw: JSON.parse(storage.getItem(lib.PREFS_KEY))}};
""")
    assert out["saved"] is True
    assert "q" not in out["prefs"] and "q" not in out["raw"] and "since" not in out["raw"]  # search text is never remembered
    assert "marks" not in out["raw"]  # favourites have their own storage keys
    assert out["loaded"] == out["prefs"]
    assert out["loaded"] == {"site": "olx", "districts": ["Krzyki", "__none__"], "maxPrice": 3500, "minArea": 40,
                             "minRooms": 2, "onlyNew": True, "view": "fav", "merge": False, "sort": {"key": "price", "dir": "asc"}}


@needs_node
def test_prefs_drop_values_that_no_longer_make_sense():
    out = run_node(f"""
const known = {KNOWN};
const s = (o) => lib.sanitizePrefs(o, known);
return [
  s({{site: 'zniknął', districts: ['Krzyki', 'Atlantyda', 5, null], maxPrice: -5, minArea: '40', minRooms: 9, onlyNew: 'yes', merge: 0, sort: {{key: 'hack', dir: 'asc'}}}}),
  s({{sort: {{key: 'area', dir: 'sideways'}}}}),
  s(null), s('text'), s([1, 2]), s(undefined),
];
""")
    default = {"site": "", "districts": [], "maxPrice": None, "minArea": None, "minRooms": None, "onlyNew": False,
               "view": "current", "merge": True, "sort": {"key": "firstSeen", "dir": "desc"}}
    # values that are present but wrong are dropped one by one; "merge: 0" is not exactly false, so it stays on
    assert out[0] == {**default, "districts": ["Krzyki"]}
    assert out[1] == default and out[2] == default and out[3] == default and out[4] == default and out[5] == default


@needs_node
def test_a_remembered_hidden_view_is_not_restored():
    out = run_node(f"""const known = {KNOWN}; return ['hidden', 'fav', 'current', 'x', null].map(v => lib.sanitizePrefs({{view: v}}, known).view);""")
    assert out == ["current", "fav", "current", "current", "current"]  # "Ukryte" would look like an empty page next time


@needs_node
def test_prefs_survive_garbage_and_broken_storage():
    out = run_node(f"""
const known = {KNOWN};
const garbage = lib.memoryStorage(); garbage.setItem(lib.PREFS_KEY, '{{oops');
const broken = {{getItem() {{ throw new Error('x'); }}, setItem() {{ throw new Error('x'); }}}};
return [lib.loadPrefs(garbage, known).sort.key, lib.loadPrefs(broken, known).sort.key, lib.savePrefs(broken, {{}})];
""")
    assert out == ["firstSeen", "firstSeen", False]  # defaults, and saving reports failure instead of throwing


# ---------- photos ----------

@needs_node
def test_photo_is_normalized_and_only_http_urls_survive():
    out = run_node("return input.map(p => lib.normalize({site: 'x', photo: p}).photo);",
                   ["https://img.test/a.jpg", "http://img.test/a.jpg", "", "javascript:alert(1)", "data:image/png;base64,AAAA", "//img.test/a.jpg"])
    assert out == ["https://img.test/a.jpg", "http://img.test/a.jpg", None, None, None, None]
    assert run_node("return lib.normalize({site: 'x'}).photo;", None) is None  # old CSV without the column


@needs_node
def test_a_merged_row_takes_the_photo_of_the_first_portal_that_has_one():
    flats = [
        rec("otodom", "a", street="Hubska", photo=""),                          # highest priority, but no picture
        rec("morizon", "m", street="Hubska", photo="https://img.test/m.jpg"),
        rec("nieruchomosci", "n", street="Hubska", photo="https://img.test/n.jpg"),
        rec("olx", "o", price_pln="2000", photo="https://img.test/o.jpg"),
        rec("olx", "p", price_pln="2100", photo=""),
    ]
    out = run_node(f"const rows = {json.dumps(flats)}.map(lib.normalize);"
                   "return lib.buildRows(rows, true).map(v => [v.members.length, v.photo]);")
    by_size = sorted(out, key=lambda x: (-x[0], x[1] or ""))  # the merged row first, then the single ones
    assert by_size == [[3, "https://img.test/m.jpg"], [1, None], [1, "https://img.test/o.jpg"]]
    # with merging off every listing keeps its own picture
    off = run_node(f"const rows = {json.dumps(flats)}.map(lib.normalize); return lib.buildRows(rows, false).map(v => v.photo);")
    assert off == [None, "https://img.test/m.jpg", "https://img.test/n.jpg", "https://img.test/o.jpg", None]


@needs_node
def test_favourites_keep_the_photo_and_do_not_trust_stored_ones():
    out = run_node(f"const rows = {json.dumps([rec('olx', 'o', photo='https://img.test/o.jpg')])}.map(lib.normalize);"
                   """
const views = lib.buildRows(rows, true);
const marks = lib.setMark(lib.emptyMarks(), views[0], 'fav', 1);
const gone = lib.goneViews(marks, []);
const storage = lib.memoryStorage();
lib.saveMarks(storage, marks);
const reloaded = lib.loadMarks(storage);
const refreshed = lib.refreshSnapshots(reloaded, rows);   // same data: the snapshot must not look "changed" on every visit
const evil = lib.sanitizeMarks({'olx:z': {site: 'olx', id: 'z', title: 't', photo: 'javascript:alert(1)'},
                                'olx:y': {site: 'olx', id: 'y', title: 't', photo: 5},
                                'olx:w': {site: 'olx', id: 'w', title: 't'}}, {});
return {saved: marks.favs['olx:o'].photo, gone: gone.map(g => g.photo), reloaded: reloaded.favs['olx:o'].photo,
        changed: refreshed.changed, evil: ['olx:z', 'olx:y', 'olx:w'].map(k => evil.favs[k].photo)};
""")
    assert out == {"saved": "https://img.test/o.jpg", "gone": ["https://img.test/o.jpg"], "reloaded": "https://img.test/o.jpg",
                   "changed": False, "evil": [None, None, None]}


def test_images_only_ever_get_a_checked_url():
    cell = re.search(r"function photoCell\(r\) \{.*?\n  \}\n", APP_JS, re.S).group(0)
    # every src/srcset comes out of photoSources(), which runs the URL through safeUrl
    assert "photoSources(r.photo)" in cell and "src: sources.src" in cell and "sources.srcset" in cell
    assert not re.search(r"\.src\s*=\s*r\.|src:\s*r\.", cell)  # never straight from the row
    assert 'referrerPolicy: "no-referrer"' in cell and 'loading: "lazy"' in cell
    assert '"error"' in cell  # a dead image URL falls back to the placeholder
    helper = re.search(r"function photoSources\(url\) \{.*?\n\}\n", APP_JS, re.S).group(0)
    assert helper.count("safeUrl(") >= 2  # the stored URL and the derived larger one
    assert INDEX_HTML.count('class="photo"') == 1  # the header cell for the photo column


@needs_node
def test_photo_sources_offer_a_larger_variant_for_the_portals_that_have_one():
    out = run_node("return input.map(u => lib.photoSources(u));", [
        "https://ireland.apollo.olxcdn.com:443/v1/files/abc-PL/image;s=320x240",
        "https://ireland.apollo.olxcdn.com/v1/files/JWT/image;s=314x236;q=80",
        "https://img1.staticmorizon.com.pl/thumb/ID/3x2_s:fill_and_crop/mieszkanie-65-m.jpg",
        "https://i.st-nieruchomosci-online.pl/h198ssl/mieszkanie-wroclaw.jpg",
        "https://other.test/a.jpg?s=320x240",
    ])
    olx, otodom, morizon, nieruch, other = out
    assert olx == {"src": "https://ireland.apollo.olxcdn.com:443/v1/files/abc-PL/image;s=320x240",
                   "srcset": "https://ireland.apollo.olxcdn.com:443/v1/files/abc-PL/image;s=320x240 320w, "
                             "https://ireland.apollo.olxcdn.com:443/v1/files/abc-PL/image;s=640x480 640w"}
    assert otodom["srcset"].endswith("image;s=655x491;q=80 655w") and otodom["srcset"].startswith(otodom["src"] + " 314w")
    assert morizon["srcset"].endswith("/3x2_m:fill_and_crop/mieszkanie-65-m.jpg 600w") and "/3x2_s:" in morizon["src"]
    assert nieruch == {"src": "https://i.st-nieruchomosci-online.pl/h198ssl/mieszkanie-wroclaw.jpg", "srcset": None}  # one size only
    assert other["srcset"] is None  # unknown URL shapes pass through untouched


def test_srcset_sizes_hint_matches_the_desktop_thumbnail_width():
    # `sizes` tells the browser how wide the picture is on desktop; if the CSS width changes and this does not,
    # desktop silently downloads the wrong variant
    desktop = re.search(r"\n  \.thumb \{ display: block; width: (\d+)px;", INDEX_HTML).group(1)
    sizes = re.search(r'const PHOTO_SIZES = "\(max-width: 700px\) 100vw, (\d+)px"', APP_JS).group(1)
    assert desktop == sizes == "180"


@needs_node
def test_photo_sources_reject_anything_that_is_not_http():
    out = run_node("return input.map(u => lib.photoSources(u));",
                   [None, "", "javascript:alert(1)", "data:image/png;base64,AAAA", "//x.test/a.jpg", "ftp://x.test/a.jpg"])
    assert out == [None] * 6


@needs_node
def test_active_filter_chips_describe_only_the_panel_filters():
    out = run_node("""
const label = (f) => lib.activeFilterChips(f).map(c => [c.key, c.value, c.label.replace(/\u00a0/g, ' ')]);
return {
  none: label({q: 'x', site: '', districts: [], maxPrice: null, minArea: null, minRooms: null, onlyNew: false, view: 'fav'}),
  all: label({site: 'olx', districts: ['Krzyki', lib.NO_DISTRICT], maxPrice: 4000, minArea: 40, minRooms: 2, onlyNew: true}),
  zero: label({maxPrice: 0, minArea: 0}),
  missing: label({}),
};""")
    assert out["none"] == []  # the search text and the view are not "filters"
    assert out["all"] == [
        ["site", "olx", "OLX"], ["district", "Krzyki", "Krzyki"], ["district", "__none__", "Brak dzielnicy"],
        ["maxPrice", 4000, "do 4000 zł"], ["minArea", 40, "od 40 m²"], ["minRooms", 2, "od 2 pok."], ["onlyNew", True, "Tylko nowe"],
    ]
    assert [c[0] for c in out["zero"]] == ["maxPrice", "minArea"]  # 0 is a value, not "empty"
    assert out["missing"] == []


def test_the_view_is_a_radio_group_not_a_select():
    assert 'id="show"' not in INDEX_HTML
    radios = re.findall(r'<input type="radio" name="show" value="(\w+)"', INDEX_HTML)
    assert radios == ["current", "fav", "hidden"]
    assert re.search(r'<input type="radio" name="show" value="current" checked>', INDEX_HTML)  # Aktualne is the default
    # every sort option the dropdown offers is a preset the script knows (or the hidden "custom" one)
    options = set(re.findall(r'<option value="([\w-]+)"', re.search(r'<select id="sort">.*?</select>', INDEX_HTML, re.S).group(0)))
    presets = set(re.findall(r'^\s+"?([\w-]+)"?: \["\w+", "(?:asc|desc)"\],?$', APP_JS, re.M))
    assert options - {"custom"} <= presets, options - presets


def test_filter_panel_is_wired_for_keyboard_and_screen_readers():
    assert re.search(r'<button id="filtersToggle"[^>]*aria-expanded="false"[^>]*aria-controls="filterPanel"', INDEX_HTML)
    assert re.search(r'<div id="filterPanel"[^>]*\bhidden\b', INDEX_HTML)  # closed until opened
    assert 'e.key === "Escape"' in APP_JS and '"aria-expanded"' in APP_JS  # Escape closes it, the state is announced
    assert "[hidden] { display: none !important; }" in INDEX_HTML  # author display rules must not defeat `hidden`


# ---------- map ----------

@needs_node
def test_location_needs_lat_lon_and_a_known_precision():
    out = run_node("""return input.map(r => { const o = lib.normalize({site: 'x', ...r}); return [o.lat, o.lon, o.geo]; });""", [
        {"lat": "51.1", "lon": "17.03", "geo": "street"},
        {"lat": "51.1", "lon": "17.03", "geo": "point"},
        {"lat": "51.1", "lon": "17.03", "geo": "area"},
        {"lat": "51.1", "lon": "17.03", "geo": ""},            # precision unknown
        {"lat": "51.1", "lon": "17.03", "geo": "constructor"}, # not a level, however it looks
        {"lat": "51.1", "lon": "", "geo": "street"},           # half a location is none
        {"lat": "", "lon": "", "geo": ""},
        {"lat": "99", "lon": "17.03", "geo": "street"},        # not on Earth
        {"lat": "51.1", "lon": "x", "geo": "street"},
        {},                                                    # an old CSV without the columns
    ])
    assert out[:3] == [[51.1, 17.03, "street"], [51.1, 17.03, "point"], [51.1, 17.03, "area"]]
    assert out[3:] == [[None, None, None]] * 7


@needs_node
def test_a_merged_row_shows_its_most_precise_location():
    flats = [
        rec("otodom", "a", street="Hubska", lat="51.10", lon="17.00", geo="street"),   # geocoded street
        rec("olx", "o", street="", title="Hubska 2 pokoje", lat="51.11", lon="17.01", geo="point"),  # exact point, merges via its title
        rec("morizon", "m", price_pln="2000", lat="51.20", lon="17.10", geo="area"),
        rec("nieruchomosci", "n", price_pln="2100"),                                    # no location at all
    ]
    out = run_node(f"const rows = {json.dumps(flats)}.map(lib.normalize);"
                   "return lib.buildRows(rows, true).map(v => [v.members.map(m => m.site).sort(), v.loc]);")
    by_first = {tuple(sites): loc for sites, loc in out}
    assert by_first[("olx", "otodom")] == {"lat": 51.11, "lon": 17.01, "geo": "point"}  # point beats street, whichever portal it is from
    assert by_first[("morizon",)] == {"lat": 51.2, "lon": 17.1, "geo": "area"}
    assert by_first[("nieruchomosci",)] is None
    equal = run_node("return lib.bestLocation([{lat: 1, lon: 2, geo: 'street'}, {lat: 3, lon: 4, geo: 'street'}, {lat: null, lon: null, geo: null}]);")
    assert equal == {"lat": 1, "lon": 2, "geo": "street"}  # a tie keeps the first (portal priority)


@needs_node
def test_map_urls_are_built_from_numbers_only():
    out = run_node("""
const street = {lat: 51.1, lon: 17.03, geo: 'street'};
const area = {lat: 51.13, lon: 16.98, geo: 'area'};
return {street: lib.mapEmbedUrl(street), area: lib.mapEmbedUrl(area), links: lib.mapLinks(street), areaLinks: lib.mapLinks(area),
        bad: [null, {}, {lat: 'x', lon: 1, geo: 'street'}, {lat: 51, lon: 17, geo: 'nope'}, {lat: 51, lon: 17}, {lat: 91, lon: 17, geo: 'point'},
              {lat: NaN, lon: 17, geo: 'point'}, {lat: 51, lon: '17', geo: 'point'}].map(l => [lib.mapEmbedUrl(l), lib.mapLinks(l)])};
""")
    assert out["street"] == ("https://www.openstreetmap.org/export/embed.html?bbox=17.024000%2C51.096500%2C17.036000%2C51.103500"
                             "&layer=mapnik&marker=51.100000%2C17.030000")
    assert "marker" not in out["area"] and "bbox=16.910000%2C51.090000%2C17.050000%2C51.170000" in out["area"]  # wide view, no pin
    assert out["links"] == {"google": "https://www.google.com/maps/search/?api=1&query=51.100000,17.030000",
                            "osm": "https://www.openstreetmap.org/?mlat=51.100000&mlon=17.030000#map=17/51.100000/17.030000"}
    assert out["areaLinks"]["osm"].endswith("#map=13/51.130000/16.980000")
    assert out["bad"] == [[None, None]] * 8  # anything that is not a valid number pair gives no map and no links


@needs_node
def test_map_caption_says_how_exact_the_pin_is():
    out = run_node("return ['point', 'street', 'area'].map(g => [lib.mapCaption({geo: g}, 'Krzyki · Hubska'), lib.mapCaption({geo: g}, '')]);")
    assert out[0] == ["Krzyki · Hubska — lokalizacja z ogłoszenia.", "Lokalizacja z ogłoszenia."]
    assert out[1][0] == "Krzyki · Hubska — środek ulicy, numer domu nieznany."
    assert out[2][1].startswith("Przybliżona okolica")  # the honest wording for a blurred location


@needs_node
def test_favourites_keep_the_location_and_do_not_trust_stored_ones():
    out = run_node(f"const rows = {json.dumps([rec('olx', 'o', lat='51.1', lon='17.0', geo='street')])}.map(lib.normalize);"
                   """
const views = lib.buildRows(rows, true);
const marks = lib.setMark(lib.emptyMarks(), views[0], 'fav', 1);
const gone = lib.goneViews(marks, []);
const storage = lib.memoryStorage();
lib.saveMarks(storage, marks);
const reloaded = lib.loadMarks(storage);
const refreshed = lib.refreshSnapshots(reloaded, rows);   // same data: must not look "changed" on every visit
const evil = lib.sanitizeMarks({'olx:a': {site: 'olx', id: 'a', title: 't', lat: 'x', lon: 5, geo: 'street'},
                                'olx:b': {site: 'olx', id: 'b', title: 't', lat: 51, lon: 17, geo: 'bogus'},
                                'olx:c': {site: 'olx', id: 'c', title: 't', lat: 120, lon: 17, geo: 'point'},
                                'olx:d': {site: 'olx', id: 'd', title: 't'}}, {});
return {gone: gone.map(g => g.loc), reloaded: reloaded.favs['olx:o'].lat, changed: refreshed.changed,
        evil: ['olx:a', 'olx:b', 'olx:c', 'olx:d'].map(k => [evil.favs[k].lat, evil.favs[k].geo])};
""")
    assert out == {"gone": [{"lat": 51.1, "lon": 17.0, "geo": "street"}], "reloaded": 51.1, "changed": False,
                   "evil": [[None, None]] * 4}  # a favourite that left the CSV can still open its map


@needs_node
def test_a_building_found_from_the_text_beats_every_other_location_and_gets_a_close_view():
    out = run_node("""
const rows = [
  {site: 'olx', lat: 51.2, lon: 17.2, geo: 'point'},      // OLX's own pin (business listings are sometimes km off)
  {site: 'otodom', lat: 51.1, lon: 17.0, geo: 'address'}, // the address the seller wrote in the text
  {site: 'morizon', lat: 51.3, lon: 17.3, geo: 'street'},
].map(r => lib.normalize({id: r.site, ...r}));
return {best: lib.bestLocation(rows), levels: lib.GEO_LEVELS,
        span: ['address', 'point', 'street', 'area'].map(g => lib.mapEmbedUrl({lat: 51.1, lon: 17.0, geo: g}).match(/bbox=([^&]+)/)[1]),
        pin: lib.mapEmbedUrl({lat: 51.1, lon: 17.0, geo: 'address'}).includes('marker='),
        caption: lib.mapCaption({geo: 'address'}, 'Krzyki · Komedy 16')};
""")
    assert out["best"] == {"lat": 51.1, "lon": 17.0, "geo": "address"} and out["levels"] == ["area", "street", "point", "address"]
    assert out["span"][0] == "16.997500%2C51.098500%2C17.002500%2C51.101500"  # about 350 m wide: one building and its neighbours
    assert out["span"][1] == out["span"][2] and out["span"][3].startswith("16.930000")
    assert out["pin"] is True
    assert out["caption"] == "Krzyki · Komedy 16 — adres z opisu ogłoszenia."


@needs_node
def test_house_numbers_are_shown_with_the_street_and_kept_for_favourites():
    out = run_node("""
const rows = [
  {site: 'olx', id: 'o', street: 'Hubska', house: '', title: 'x'},
  {site: 'otodom', id: 'a', street: 'Hubska', house: '12', title: 'y'},
  {site: 'morizon', id: 'b', street: '', house: '7', title: 'z'},
].map(r => lib.normalize({price_pln: '3000', area_m2: '45', rooms: '2', district: 'Krzyki', url: 'https://' + r.site + '.test/' + r.id, ...r}));
const views = lib.buildRows(rows, true);
const merged = views.find(v => v.members.length === 2);
const marks = lib.setMark(lib.emptyMarks(), merged, 'fav', 1);
const storage = lib.memoryStorage(); lib.saveMarks(storage, marks);
const reloaded = lib.loadMarks(storage);
const evil = lib.sanitizeMarks({'olx:q': {site: 'olx', id: 'q', title: 't', house: 5}}, {});
return {labels: rows.map(lib.streetLabel), merged: [merged.street, merged.house, lib.streetLabel(merged)],
        snap: reloaded.favs['otodom:a'].house, evil: evil.favs['olx:q'].house, same: lib.refreshSnapshots(reloaded, rows).changed};
""")
    assert out["labels"] == ["Hubska", "Hubska 12", None]       # no street, no label: a number alone means nothing
    assert out["merged"] == ["Hubska", "12", "Hubska 12"]       # the merged row takes the number from the member that has one
    assert out["snap"] == "12" and out["evil"] is None and out["same"] is False


def test_the_map_iframe_is_built_only_from_checked_numbers():
    row = re.search(r"function mapRow\(r\) \{.*?\n  \}\n", APP_JS, re.S).group(0)
    assert "src = mapEmbedUrl(r.loc)" in row and "{ className: \"map-frame\", src," in row  # nothing but the checked URL reaches src
    # OSM's tile servers answer 403 to requests without a Referer, so the map must NOT be "no-referrer" like the photos are
    assert 'referrerPolicy: "strict-origin-when-cross-origin"' in row and 'no-referrer"' not in row.split("const frame")[1].split("\n")[0] and 'loading: "lazy"' in row
    assert 'setAttribute("sandbox", "allow-scripts allow-same-origin")' in row  # OSM's page needs scripts; nothing else is allowed
    assert 'rel: "noopener noreferrer"' in row
    embed = re.search(r"function mapEmbedUrl\(loc\) \{.*?\n\}\n", APP_JS, re.S).group(0)
    assert "https://www.openstreetmap.org/export/embed.html" in embed and "r.title" not in embed
    # nothing is requested from OpenStreetMap until a map is opened: the only iframe is created inside mapRow
    assert INDEX_HTML.count("<iframe") == 0 and APP_JS.count('el("iframe"') == 1


def test_a_map_button_exists_only_for_rows_with_a_location_and_is_a_real_button():
    main = re.search(r"function mainCell\(r, now\) \{.*?\n  \}\n", APP_JS, re.S).group(0)
    assert "if (r.loc) portals.append(mapButton(r))" in main
    button = re.search(r"function mapButton\(r\) \{.*?\n  \}\n", APP_JS, re.S).group(0)
    assert 'type: "button"' in button and '"aria-expanded"' in button
    assert "openMap" in APP_JS and "state.openMap = null" in APP_JS  # one open at a time, closed when its row disappears


# ---------- already-opened links ----------

def test_opened_links_are_greyed_by_the_browser_in_both_themes():
    # a token for both colour schemes...
    assert len(re.findall(r"--visited: #[0-9a-fA-F]{6};", INDEX_HTML)) == 2
    # ...used for the title and the portal chips (the only links that open a listing)
    assert re.search(r"a\.title-link:visited \{ color: var\(--visited\); \}", INDEX_HTML)
    assert re.search(r"a\.chip:visited \{ color: var\(--visited\); \}", INDEX_HTML)
    # browsers silently ignore :visited rules that change anything but colours, so only colour properties may appear in them
    for rule in re.findall(r"[^{}]*:visited[^{}]*\{([^}]*)\}", INDEX_HTML):
        props = {p.split(":")[0].strip() for p in rule.split(";") if p.strip()}
        assert props <= {"color", "background-color", "border-color", "outline-color"}, props
    # a visited link must still react to hover: its rule has to come first, equal specificity means the later rule wins
    assert INDEX_HTML.index("a.title-link:visited") < INDEX_HTML.index("a.title-link:hover")
    assert INDEX_HTML.index("a.chip:visited") < INDEX_HTML.index("a.chip:hover")


# ---------- favourites and hidden listings ----------

def view_rows(records):
    """Normalized rows of `records` plus the helper to build display rows, as JS source."""
    return f"const rows = {json.dumps(records)}.map(lib.normalize); const views = lib.buildRows(rows, true);"


FLATS = [
    rec("otodom", "a", street="Hubska", title="Flat A", last_seen="2026-11-03T10:00:00Z"),
    rec("morizon", "m", street="Hubska", title="Flat A on morizon"),          # same flat as "a" -> merged row
    rec("olx", "o", price_pln="2000", title="Other flat"),
    rec("olx", "p", price_pln="2100", title="Third flat"),
]


@needs_node
def test_marks_apply_to_every_listing_of_a_merged_row():
    out = run_node(view_rows(FLATS) + """
let marks = lib.emptyMarks();
const merged = views.find(v => v.members.length === 2), single = views.find(v => v.id === 'o');
marks = lib.setMark(marks, merged, 'fav', 1000);
const afterFav = {keys: Object.keys(marks.favs).sort(), group: marks.favs['otodom:a'].group, state: lib.markState(merged, marks), other: lib.markState(single, marks)};
marks = lib.setMark(marks, merged, 'hidden', 2000);       // hiding un-favourites it
const afterHide = {favs: Object.keys(marks.favs), hidden: Object.keys(marks.hidden).sort(), state: lib.markState(merged, marks)};
marks = lib.setMark(marks, merged, 'fav', 3000);          // starring un-hides it
const afterRefav = {favs: Object.keys(marks.favs).sort(), hidden: Object.keys(marks.hidden), state: lib.markState(merged, marks)};
marks = lib.setMark(marks, merged, null, 4000);
return {afterFav, afterHide, afterRefav, cleared: [Object.keys(marks.favs).length, Object.keys(marks.hidden).length, lib.markState(merged, marks)]};
""")
    assert out["afterFav"] == {"keys": ["morizon:m", "otodom:a"], "group": ["otodom:a", "morizon:m"], "state": "fav", "other": None}
    assert out["afterHide"] == {"favs": [], "hidden": ["morizon:m", "otodom:a"], "state": "hidden"}
    assert out["afterRefav"] == {"favs": ["morizon:m", "otodom:a"], "hidden": [], "state": "fav"}
    assert out["cleared"] == [0, 0, None]


@needs_node
def test_a_favourite_wins_when_the_listings_of_a_row_disagree():
    out = run_node(view_rows(FLATS) + """
const merged = views.find(v => v.members.length === 2);
// she hid the morizon listing earlier and starred the otodom one later: the merged row counts as a favourite
const marks = {favs: lib.setMark(lib.emptyMarks(), {members: [rows[0]]}, 'fav', 1).favs, hidden: {'morizon:m': 5}};
return [lib.markState(merged, marks), lib.markState({members: [rows[1]]}, marks)];
""")
    assert out == ["fav", "hidden"]


@needs_node
def test_view_filter_current_favourites_and_hidden():
    out = run_node(view_rows(FLATS) + """
const now = Date.parse('2026-11-04T12:00:00Z');
let marks = lib.emptyMarks();
marks = lib.setMark(marks, views.find(v => v.id === 'o'), 'hidden', 1);
marks = lib.setMark(marks, views.find(v => v.members.length === 2), 'fav', 2);
const ids = (view) => lib.applyFilters(views, {view, marks}, now).map(v => v.id).sort();
return {current: ids('current'), fav: ids('fav'), hidden: ids('hidden'), none: ids(undefined),
        noMarks: lib.applyFilters(views, {view: 'fav'}, now).length};
""")
    assert out["current"] == ["a", "p"]  # the hidden flat is excluded, the favourite stays
    assert out["fav"] == ["a"] and out["hidden"] == ["o"]
    assert out["none"] == out["current"]  # default view
    assert out["noMarks"] == 0


@needs_node
def test_favourites_keep_a_snapshot_that_outlives_the_listing():
    out = run_node(view_rows(FLATS) + """
let marks = lib.setMark(lib.emptyMarks(), views.find(v => v.id === 'o'), 'fav', 1000);
// 1) still in the data: nothing is "gone"
const stillLive = lib.goneViews(marks, rows).length;
// 2) the listing disappears from the CSV
const without = rows.filter(r => r.id !== 'o');
const gone = lib.goneViews(marks, without);
// 3) a price change while it is live refreshes the snapshot
const repriced = rows.map(r => r.id === 'o' ? {...r, price: 1900} : r);
const refreshed = lib.refreshSnapshots(marks, repriced);
const unchanged = lib.refreshSnapshots(marks, rows);
return {stillLive, gone: gone.map(g => ({id: g.id, gone: g.gone, title: g.title, price: g.price, members: g.members.length})),
        refreshedPrice: refreshed.marks.favs['olx:o'].price, changed: [refreshed.changed, unchanged.changed], savedAt: refreshed.marks.favs['olx:o'].savedAt};
""")
    assert out["stillLive"] == 0
    assert out["gone"] == [{"id": "o", "gone": True, "title": "Other flat", "price": 2000, "members": 1}]
    assert out["refreshedPrice"] == 1900 and out["changed"] == [True, False] and out["savedAt"] == 1000


@needs_node
def test_a_gone_merged_favourite_is_one_row_and_a_live_sibling_covers_it():
    out = run_node(view_rows(FLATS) + """
const marks = lib.setMark(lib.emptyMarks(), views.find(v => v.members.length === 2), 'fav', 1);
const bothGone = lib.goneViews(marks, rows.filter(r => r.id !== 'a' && r.id !== 'm'));
const oneGone = lib.goneViews(marks, rows.filter(r => r.id !== 'a'));  // morizon listing still live: shown through it
return {bothGone: bothGone.map(g => g.members.map(m => m.site)), oneGone: oneGone.length};
""")
    assert out["bothGone"] == [["otodom", "morizon"]]  # one row with both portals' links
    assert out["oneGone"] == 0


@needs_node
def test_marks_storage_round_trip_garbage_and_cap():
    out = run_node(view_rows(FLATS) + """
const storage = lib.memoryStorage();
let marks = lib.setMark(lib.emptyMarks(), views.find(v => v.id === 'o'), 'fav', 10);
marks = lib.setMark(marks, views.find(v => v.id === 'p'), 'hidden', 20);
const saved = lib.saveMarks(storage, marks), loaded = lib.loadMarks(storage);
const bad = lib.memoryStorage();
bad.setItem(lib.FAVS_KEY, JSON.stringify({'olx:o': {site: 'olx', id: 'WRONG', title: 't'}, 'x': 5, 'olx:z': {site: 'olx', id: 'z', title: 'ok', price: 'abc', lastSeen: 7, group: [1, 'olx:z']}}));
bad.setItem(lib.HIDDEN_KEY, JSON.stringify({'olx:p': 5, 'nocolon': 5, 'olx:q': 'x'}));
const sanitized = lib.loadMarks(bad);
const broken = {getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); }};
// cap: 5 hidden (old) + 3 favourites, limit 6 -> the 2 oldest hidden go first
const big = {favs: {a: {savedAt: 1}, b: {savedAt: 2}, c: {savedAt: 3}}, hidden: {h1: 10, h2: 11, h3: 12, h4: 13, h5: 14}};
const capped = lib.capMarks(big, 6);
const capped2 = lib.capMarks(big, 2);
return {saved, same: JSON.stringify(loaded) === JSON.stringify(marks),
        sanitizedFavs: Object.keys(sanitized.favs), z: sanitized.favs['olx:z'], sanitizedHidden: Object.keys(sanitized.hidden),
        brokenLoad: lib.loadMarks(broken), brokenSave: lib.saveMarks(broken, marks),
        capped: [Object.keys(capped.hidden), Object.keys(capped.favs)], capped2: [Object.keys(capped2.hidden), Object.keys(capped2.favs)]};
""")
    assert out["saved"] is True and out["same"] is True
    assert out["sanitizedFavs"] == ["olx:z"]  # wrong key/id and non-objects are dropped
    assert out["z"]["price"] is None and out["z"]["lastSeen"] == 7 and out["z"]["group"] == ["olx:z"]
    assert out["sanitizedHidden"] == ["olx:p"]
    assert out["brokenLoad"] == {"favs": {}, "hidden": {}} and out["brokenSave"] is False
    assert out["capped"] == [["h3", "h4", "h5"], ["a", "b", "c"]]  # oldest hidden dropped first
    assert out["capped2"] == [[], ["b", "c"]]  # a smaller cap drops all hidden ones, then the oldest favourite


# ---------- manual update button ----------

FAKE_PAT = "github_pat_" + "A1b2C3d4E5" * 6


@needs_node
@pytest.mark.parametrize("value, ok", [
    (FAKE_PAT, True),
    (f"  {FAKE_PAT}\n", True),
    ("ghp_" + "x" * 36, True),
    ("", False), ("   ", False), (None, False), (42, False),
    ("hunter2", False),
    ("github_pat_short", False),
    (FAKE_PAT + '"', False),
    (FAKE_PAT + "\nX-Evil: 1", False),
    ("Bearer " + FAKE_PAT, False),
])
def test_clean_token_accepts_only_github_looking_tokens(value, ok):
    got = run_node("return lib.cleanToken(input);", value)
    assert got == (value.strip() if ok else None)


@needs_node
def test_dispatch_request_is_the_exact_workflow_dispatch_call():
    req = run_node("return lib.dispatchRequest(input);", FAKE_PAT)
    assert req["url"] == "https://api.github.com/repos/jradziejewski/flatscraper/actions/workflows/scrape.yml/dispatches"
    assert req["init"]["method"] == "POST"
    assert json.loads(req["init"]["body"]) == {"ref": "main"}
    headers = req["init"]["headers"]
    assert headers["Authorization"] == f"Bearer {FAKE_PAT}"
    assert headers["Accept"] == "application/vnd.github+json"
    assert FAKE_PAT not in json.dumps({**req, "init": {**req["init"], "headers": {}}})  # the token is only in Authorization


@needs_node
def test_pick_run_takes_the_newest_manual_run_created_after_the_click():
    click = 1_700_000_000_000
    iso = lambda ms: run_node("return new Date(input).toISOString();", ms)
    runs = [
        {"id": 1, "created_at": iso(click - 10 * 60 * 1000)},  # an older run: not ours
        {"id": 2, "created_at": iso(click - 20 * 1000)},       # within the clock slack
        {"id": 3, "created_at": iso(click + 5 * 1000)},
    ]
    assert run_node("return lib.pickRun(input.runs, input.t)?.id ?? null;", {"runs": runs, "t": click}) == 3
    assert run_node("return lib.pickRun(input.runs, input.t)?.id ?? null;", {"runs": runs[:1], "t": click}) is None
    assert run_node("return lib.pickRun(input.runs, input.t)?.id ?? null;", {"runs": runs[:2], "t": click}) == 2
    assert run_node("return lib.pickRun(input.runs, input.t);", {"runs": None, "t": click}) is None


@needs_node
@pytest.mark.parametrize("run, done, ok", [
    (None, False, False),
    ({"status": "queued"}, False, False),
    ({"status": "in_progress"}, False, False),
    ({"status": "completed", "conclusion": "success"}, True, True),
    ({"status": "completed", "conclusion": "failure"}, True, False),
    ({"status": "completed", "conclusion": "cancelled"}, True, False),
])
def test_run_phase(run, done, ok):
    phase = run_node("return lib.runPhase(input);", run)
    assert (phase["done"], phase["ok"]) == (done, ok) and phase["label"]


@needs_node
def test_fresh_csv_url_is_pinned_to_a_commit():
    sha = "a" * 40
    assert run_node("return lib.freshCsvUrl(input);", sha) == f"https://raw.githubusercontent.com/jradziejewski/flatscraper/{sha}/listings.csv"
    for bad in ["main", "abc", "g" * 40, "a" * 41, "../" + "a" * 37, None]:
        assert run_node("return lib.freshCsvUrl(input);", bad) is None


def test_token_form_hides_what_is_typed_and_is_not_autofilled():
    tag = re.search(r'<input id="tokenInput"[^>]*>', INDEX_HTML).group(0)
    assert 'type="password"' in tag and 'autocomplete="off"' in tag
    assert "console.log" not in APP_JS  # nothing may print the token
