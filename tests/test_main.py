from main import run
from aptsearch.models import Listing
from aptsearch.storage import load_listings


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
