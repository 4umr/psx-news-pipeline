"""PSX company pages (dps.psx.com.pk/company/SYMBOL).

Each page carries the company's latest filings — including "Material Information",
the most price-sensitive disclosures — plus annual/quarterly EPS history used to
put new results in context (year-on-year growth).
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..common import clean_ws, http, log

BASE = "https://dps.psx.com.pk"


def _num(s: str) -> float | None:
    s = s.replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def _doc_url(a) -> str:
    if a is None:
        return ""
    href = a.get("href", "")
    if href and not href.startswith("javascript"):
        return href if href.startswith("http") else BASE + href
    if a.get("data-images"):
        return f"{BASE}/download/image/{a['data-images'].split(',')[0]}"
    return ""


def company(symbol: str) -> dict | None:
    try:
        r = http().get(f"{BASE}/company/{symbol}", timeout=25)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PSX page %s failed: %s", symbol, e)
        return None
    soup = BeautifulSoup(r.content, "lxml")
    out: dict = {"symbol": symbol, "filings": [], "annual_eps": {}, "quarterly_eps": {}}
    ann_tables = []
    for t in soup.find_all("table"):
        head = [clean_ws(th.get_text(" ")) for th in t.find_all("tr")[0].find_all(["th", "td"])] if t.find("tr") else []
        if head[:3] == ["Date", "Title", "Document"]:
            ann_tables.append(t)
        elif len(head) >= 2 and head[0] == "" and t.find(string=re.compile(r"^\s*EPS\s*$")):
            eps_row = next((tr for tr in t.find_all("tr") if clean_ws(tr.find(["td", "th"]).get_text()) == "EPS"), None)
            if eps_row:
                vals = [_num(td.get_text()) for td in eps_row.find_all("td")[1:]]
                cols = head[1:]
                target = "quarterly_eps" if any(c.startswith("Q") for c in cols) else "annual_eps"
                out[target] = {c: v for c, v in zip(cols, vals) if v is not None}
    kinds = ["results", "board", "other"]
    for kind, t in zip(kinds, ann_tables):
        for tr in t.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            try:
                date = datetime.strptime(clean_ws(tds[0].get_text()), "%b %d, %Y")
            except ValueError:
                continue
            out["filings"].append({"kind": kind, "date": date.strftime("%Y-%m-%d"),
                                   "title": clean_ws(tds[1].get_text(" ")),
                                   "url": _doc_url(tr.find("a", attrs={"data-images": True}) or tr.find("a", href=True))})
    return out
