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
