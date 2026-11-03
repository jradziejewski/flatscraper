from datetime import datetime, timezone

import pytest

from aptsearch.config import Config, load_config, matches
from aptsearch.models import Listing
from aptsearch.storage import load_listings, merge, save_listings

T1 = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)


def mk(**kw):
    base = dict(site="otodom", id="1", url="http://x/1", title="Flat", price_pln=3000)
    return Listing(**{**base, **kw})


def test_merge_new_and_update():
    rows = merge({}, [mk()], T1)
    row = rows[("otodom", "1")]
    assert row.first_seen == row.last_seen == "2026-01-01T07:00:00Z"
    rows = merge(rows, [mk(price_pln=3200)], T2)
    row = rows[("otodom", "1")]
    assert row.price_pln == 3200
    assert row.first_seen == "2026-01-01T07:00:00Z"
    assert row.last_seen == "2026-01-01T13:00:00Z"


def test_none_price_keeps_known_and_rows_never_deleted():
    rows = merge({}, [mk(), mk(id="2")], T1)
    rows = merge(rows, [mk(price_pln=None)], T2)
    assert rows[("otodom", "1")].price_pln == 3000
    assert ("otodom", "2") in rows


def test_csv_roundtrip_is_byte_identical(tmp_path):
    p = tmp_path / "l.csv"
    rows = merge({}, [mk(id="2", title="Zażółć, \"gęślą\""), mk(area_m2=40.5, rooms=2)], T1)
    save_listings(rows, p)
    first = p.read_bytes()
    save_listings(load_listings(p), p)
    assert p.read_bytes() == first
    assert b"\r" not in first
    assert load_listings(p) == rows


def test_matches_filters_only_on_known_values():
    cfg = Config(city="Warszawa", max_price=4000, min_area=30, min_rooms=2, districts=["Mokotów"])
    assert matches(mk(), cfg)  # unknowns pass
    assert not matches(mk(price_pln=5000), cfg)
    assert not matches(mk(area_m2=20), cfg)
    assert not matches(mk(rooms=1), cfg)
    assert matches(mk(district="MOKOTÓW"), cfg)
    assert not matches(mk(district="Wola"), cfg)
    assert matches(mk(city="warszawa"), cfg)
    assert not matches(mk(city="Kraków"), cfg)


def test_unknown_config_key_fails(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("city: Warszawa\nmax_prise: 1\n")
    with pytest.raises(ValueError):
        load_config(p)
