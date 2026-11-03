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
