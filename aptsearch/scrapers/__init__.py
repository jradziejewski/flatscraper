from collections.abc import Callable

from ..config import Config
from ..models import Listing
from . import morizon, nieruchomosci, otodom

Scraper = Callable[[Config], list[Listing]]

SCRAPERS: list[Scraper] = [otodom.scrape, nieruchomosci.scrape, morizon.scrape]
