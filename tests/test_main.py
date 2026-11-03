from datetime import datetime, timedelta, timezone

from main import run
from aptsearch.models import Listing
from aptsearch.storage import load_listings, save_listings, save_snapshot

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)


def test_failing_scraper_does_not_break_run(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("city: Warszawa\nmax_price: 4000\n")
    out = tmp_path / "l.csv"

    def bad(_):
        raise RuntimeError("boom")

    def good(_):
        return [
            Listing(site="s", id="1", url="u", title="ok", price_pln=3000),
            Listing(site="s", id="2", url="u", title="pricey", price_pln=9000),
        ]

    run([bad, good], cfg, out)
    assert list(load_listings(out)) == [("s", "1")]


def test_no_scrapers_writes_header_only(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("city: Warszawa\n")
    out = tmp_path / "l.csv"
    run([], cfg, out)
    assert out.read_text().startswith("site,id,url")


def setup(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("city: Warszawa\n")
    return cfg, tmp_path / "l.csv", tmp_path / "archive"


def listing(id, **kw):
    return Listing(site="s", id=id, url="u", title=id, price_pln=1000, **kw)


def stale_row(id, now=NOW):
    seen = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return listing(id, first_seen=seen, last_seen=seen)


def test_run_archives_new_listings_and_prunes_stale_rows(tmp_path):
    cfg, out, archive = setup(tmp_path)
    save_listings({("s", "old"): stale_row("old")}, out)
    run([lambda _: [listing("new")]], cfg, out, NOW)
    assert list(load_listings(out)) == [("s", "new")]  # stale row left the current file
    snapshot = archive / "2026-11" / "2026-11-03T06-00-00Z.csv"
    rows = load_listings(snapshot)
    assert list(rows) == [("s", "new")]  # snapshot holds only listings new in this run...
    assert rows[("s", "new")].first_seen == "2026-11-03T06:00:00Z"  # ...with merged fields


def test_pruned_row_stays_in_the_snapshot_that_saw_it(tmp_path):
    cfg, out, archive = setup(tmp_path)
    earlier = NOW - timedelta(days=10)
    old = {("s", "old"): stale_row("old")}
    save_listings(old, out)
    first = save_snapshot(old, earlier, archive)
    before = first.read_bytes()
    run([lambda _: [listing("new")]], cfg, out, NOW)
    assert ("s", "old") not in load_listings(out)
    assert first.read_bytes() == before and ("s", "old") in load_listings(first)


def test_total_outage_writes_no_snapshot_and_prunes_nothing(tmp_path):
    cfg, out, archive = setup(tmp_path)
    save_listings({("s", "old"): stale_row("old")}, out)

    def bad(_):
        raise RuntimeError("boom")

    run([bad], cfg, out, NOW)
    assert ("s", "old") in load_listings(out)
    assert not archive.exists()


def test_snapshot_holds_only_listings_new_in_that_run(tmp_path):
    cfg, out, archive = setup(tmp_path)
    run([lambda _: [listing("1"), listing("2")]], cfg, out, NOW)
    run([lambda _: [listing("1"), listing("2"), listing("3")]], cfg, out, NOW + timedelta(hours=6))
    first, second = sorted((archive / "2026-11").iterdir())
    assert first.name == "2026-11-03T06-00-00Z.csv" and second.name == "2026-11-03T12-00-00Z.csv"
    assert set(load_listings(first)) == {("s", "1"), ("s", "2")}
    assert set(load_listings(second)) == {("s", "3")}  # 1 and 2 were already known


def test_run_without_new_listings_writes_no_snapshot(tmp_path):
    cfg, out, archive = setup(tmp_path)
    run([lambda _: [listing("1")]], cfg, out, NOW)
    run([lambda _: [listing("1")]], cfg, out, NOW + timedelta(hours=6))  # nothing new
    assert [p.name for p in (archive / "2026-11").iterdir()] == ["2026-11-03T06-00-00Z.csv"]
    assert load_listings(out)[("s", "1")].last_seen == "2026-11-03T12:00:00Z"  # but it is still refreshed
