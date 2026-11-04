import subprocess
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def get(url: str, timeout: float = 20, user_agent: str = USER_AGENT) -> str:
    """`user_agent` is only overridden for APIs that ask clients to identify themselves (the OSM geocoder)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept-Language": "pl,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def get_curl(url: str, timeout: int = 20) -> str:
    """Fetch via the system `curl`. OLX answers curl but returns 403 to Python's
    HTTP client, so only the OLX scraper uses this."""
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--fail", "--location", "--max-time", str(timeout),
         "--user-agent", USER_AGENT, "--header", "Accept-Language: pl,en;q=0.8", url],
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8")
