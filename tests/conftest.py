import pytest


@pytest.fixture(autouse=True)
def no_real_geocoding(monkeypatch):
    """No test may reach the real OSM geocoder: a run on rows with a street would otherwise make live requests.
    Tests that exercise geocoding pass their own `fetch`."""
    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not call the network")
    monkeypatch.setattr("aptsearch.http.get", refuse)
