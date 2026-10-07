"""State Bank of Pakistan: key indicators snapshot, press releases, circulars.

Everything comes from the SBP homepage, which carries a live panel with the
policy rate, reserves, KIBOR, latest T-bill/PIB cut-offs and USD/PKR.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..common import NewsItem, clean_ws, http, log

SBP_HOME = "https://www.sbp.org.pk/"
_NUM = r"([\d,]+(?:\.\d+)?)"


def _f(x: str | None) -> float | None:
    try:
        return float(x.replace(",", "")) if x else None
    except ValueError:
        return None


def _tenors(block: str, unit: str) -> dict:
    out = {}
    for t, v in re.findall(rf"(\d+-{unit})\s+(Bids Rejected|[\d.]+%)", block):
        out[t] = None if v.startswith("Bids") else float(v.rstrip("%"))
    return out


def parse_indicators(text: str) -> dict:
    t = re.sub(r"(\d)\s*-\s+(?=[A-Za-z])", r"\1-", clean_ws(text))  # "25- September" -> "25-September"
    snap: dict = {}
    if m := re.search(r"SBP Policy Rate\s+([\d.]+)%", t):
        snap["policy_rate"] = float(m.group(1))
    if m := re.search(r"Ceiling\)? Rate\s+([\d.]+)%", t):
        snap["ceiling"] = float(m.group(1))
    if m := re.search(r"Floor\)? Rate\s+([\d.]+)%", t):
        snap["floor"] = float(m.group(1))
    if m := re.search(r"Reserves \(USD million\)\s*As on\s*(.+?)\s+SBP.{1,3}s Reserves\s+" + _NUM +
                      r"\s+Bank.{1,3}s Reserves\s+" + _NUM + r"\s+Total Reserves\s+" + _NUM, t):
        snap["reserves"] = {"as_on": clean_ws(m.group(1).replace(" - ", "-")),
                            "sbp": _f(m.group(2)), "banks": _f(m.group(3)), "total": _f(m.group(4))}
    if m := re.search(r"overnight repo rate\s+As on\s+(\S+)\s+([\d.]+)%", t):
        snap["overnight_repo"] = {"as_on": m.group(1), "rate": float(m.group(2))}
    if m := re.search(r"KIBOR\s+As on\s+(.+?)\s+Tenor BID Offer\s+3-M\s+([\d.]+)\s+([\d.]+)\s+6-M\s+([\d.]+)\s+([\d.]+)"
                      r"\s+12-M\s+([\d.]+)\s+([\d.]+)", t):
        snap["kibor"] = {"as_on": clean_ws(m.group(1).replace(" - ", "-")),
                         "3M": [float(m.group(2)), float(m.group(3))],
                         "6M": [float(m.group(4)), float(m.group(5))],
                         "12M": [float(m.group(6)), float(m.group(7))]}
    if m := re.search(r"Upcoming Auction\s+(.+?)\s+USD/\s?PKR", t):
        snap["upcoming_auctions"] = m.group(1)
    if m := re.search(r"USD/\s?PKR Rates\s+As on\s+(.+?)\s+M2M Revaluation Rate\s+([\d.]+)\s+Weighted Average Rate"
                      r"\s+BID\s+([\d.]+)\s+Offer\s+([\d.]+)", t):
        snap["usdpkr"] = {"as_on": clean_ws(m.group(1).replace(" - ", "-")), "m2m": float(m.group(2)),
                          "bid": float(m.group(3)), "offer": float(m.group(4))}
    if m := re.search(r"MTBs\s+Tenor Cut-off Yield\s+(.+?)\(as on\s+(.+?)\)", t):
        snap["mtb"] = {"as_on": m.group(2).strip(), "yields": _tenors(m.group(1), "M")}
    if m := re.search(r"Fixed\s*-\s*Rate PIB\s+Tenor Cut-off Yield\s+(.+?)\(as on\s+(.+?)\)", t):
        snap["pib"] = {"as_on": m.group(2).strip(), "yields": _tenors(m.group(1), "Y")}
    return snap


def _strip_date_prefix(text: str) -> tuple[str, str]:
    m = re.match(r"^([A-Z][a-z]+ \d{1,2},? \d{4})\s+(.*)$", text)
    return (m.group(2), m.group(1)) if m else (text, "")


def fetch() -> tuple[dict, list[NewsItem]]:
    """Return (indicator snapshot, news items for press releases & circulars)."""
    try:
        r = http().get(SBP_HOME, timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("SBP fetch failed: %s", e)
        return {}, []
    soup = BeautifulSoup(r.content, "lxml")
    snap = parse_indicators(soup.get_text(" ", strip=True))
    if not snap.get("policy_rate"):
        log.warning("SBP indicators not parsed — page layout may have changed")

    items: list[NewsItem] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(SBP_HOME, a["href"])
        text = clean_ws(a.get_text(" "))
        if href in seen or len(text) < 15:
            continue
        if "/press-release/" in href and href.lower().endswith(".pdf"):
            title = re.sub(r"^Read (more|More) about\s+", "", text)
            title = re.sub(r"^[A-Z][a-z]+ \d{1,2} \d{4}\s+", "", title)
            kind = "SBP Press Release"
        elif "/circulars/" in href or "/notification/" in href:
            title, _ = _strip_date_prefix(text)
            kind = "SBP Circular"
        else:
            continue
        seen.add(href)
        items.append(NewsItem(title=f"{kind}: {title}", url=href, source="State Bank of Pakistan",
                              published=None, pk=True, official=True))
    log.info("SBP: %d indicators, %d press/circular links", len(snap), len(items))
    return snap, items
