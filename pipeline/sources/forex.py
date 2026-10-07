"""Open-market currency rates (forex.pk) — what people actually pay at exchange companies."""
from __future__ import annotations

from bs4 import BeautifulSoup

from ..common import http, log

URL = "https://www.forex.pk/open_market_rates.asp"
WANTED = {"USD": "US Dollar", "SAR": "Saudi Riyal", "AED": "UAE Dirham", "EUR": "Euro", "GBP": "British Pound"}


def open_market() -> dict:
    """symbol -> {"buy": float, "sell": float}"""
    try:
        soup = BeautifulSoup(http().get(URL, timeout=25).content, "lxml")
    except Exception as e:  # noqa: BLE001
        log.warning("forex.pk failed: %s", e)
        return {}
    out = {}
    for tr in soup.find_all("tr"):
        c = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(c) >= 4 and c[1] in WANTED and c[1] not in out:
            try:
                out[c[1]] = {"buy": float(c[2]), "sell": float(c[3])}
            except ValueError:
                continue
    return out
