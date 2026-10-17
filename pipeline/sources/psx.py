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
    # Equity profile: "Market Cap (000's) 31,173,170.66 Shares 923,649,501 Free Float 508,007,226 Free Float 55.00%"
    prof = clean_ws(soup.get_text(" "))
    if m := re.search(r"Shares\s+([\d,]+)\s+Free Float\s+([\d,]+)\s+Free Float\s+([\d.]+)%", prof):
        out["shares"] = int(m.group(1).replace(",", ""))
        out["free_float"] = int(m.group(2).replace(",", ""))
        out["free_float_pct"] = float(m.group(3))
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


def _table_rows(t) -> list[list[str]]:
    return [[clean_ws(c.get_text(" ")) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]


def profile(symbol: str) -> dict | None:
    """Company profile + financial tables from the PSX company page (one request).

    Returns name, description, people [(name, role)], address, website, registrar, auditor, year_end,
    annual / quarterly {period: {Sales, Profit after Taxation, EPS}}, ratios {year: {...}}, shares, free float.
    """
    try:
        r = http().get(f"{BASE}/company/{symbol}", timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PSX profile %s failed: %s", symbol, e)
        return None
    soup = BeautifulSoup(r.content, "lxml")
    out: dict = {"symbol": symbol, "people": [], "annual": {}, "quarterly": {}, "ratios": {}}
    name = soup.select_one(".quote__name")
    # PSX adds temporary entitlement tags to the name ("... Limited XD"): not part of the company's name
    out["name"] = re.sub(r"\s+(XD|XB|XR|XBR|XDB|XDR|NC)$", "", clean_ws(name.get_text(" ")) if name else "")
    sector = soup.select_one(".quote__sector")
    out["sector"] = clean_ws(sector.get_text(" ")) if sector else ""
    for head in soup.select(".item__head"):
        label = clean_ws(head.get_text(" ")).lower()
        parts = []
        for sib in head.next_siblings:
            if getattr(sib, "get", None) and "item__head" in (sib.get("class") or []):
                break
            parts.append(sib.get_text(" ") if hasattr(sib, "get_text") else str(sib))
        val = clean_ws(" ".join(parts))
        key = {"business description": "description", "address": "address", "website": "website",
               "registrar": "registrar", "auditor": "auditor", "fiscal year end": "year_end"}.get(label)
        if key:
            out[key] = val
        if label == "key people":
            box = head.find_parent(class_="profile__item")
            for row in _table_rows(box.find("table")) if box and box.find("table") else []:
                if len(row) >= 2 and row[0]:
                    out["people"].append((row[0], row[1]))
    for t in soup.find_all("table"):
        rows = _table_rows(t)
        if len(rows) < 2 or rows[0][:1] != [""]:
            continue
        cols = rows[0][1:]
        labels = [rw[0] for rw in rows[1:]]
        data = {c: {} for c in cols}
        for rw in rows[1:]:
            for c, v in zip(cols, rw[1:]):
                n = _num(v)
                if n is not None:
                    data[c][rw[0]] = n
        if "Sales" in labels or "Profit after Taxation" in labels:
            out["quarterly" if any(c.startswith("Q") for c in cols) else "annual"] = data
        elif any("Margin" in lb for lb in labels):
            out["ratios"] = data
    prof = clean_ws(soup.get_text(" "))
    if m := re.search(r"Market Cap \(000'\s*s\s*\)\s*([\d,.]+)", prof):
        out["mcap_000"] = _num(m.group(1))
    if m := re.search(r"Shares\s+([\d,]+)\s+Free Float\s+([\d,]+)\s+Free Float\s+([\d.]+)%", prof):
        out["shares"], out["free_float_pct"] = int(m.group(1).replace(",", "")), float(m.group(3))
    return out


_SUFFIX = re.compile(r"(XD|XB|XR|XBR|XDB|XDR|NC)$")


def index_members(index: str, known: set[str] | None = None) -> list[str]:
    """Constituents of a PSX index (KMI30, KSE100, ...). Temporary ex-entitlement suffixes are removed
    (ATRLXD -> ATRL); with `known` symbols, a ticker is only shortened when the short form exists."""
    try:
        r = http().get(f"{BASE}/indices/{index}", timeout=30)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PSX index %s failed: %s", index, e)
        return []
    soup = BeautifulSoup(r.content, "lxml")
    out = []
    for tr in soup.select("table tbody tr"):
        td = tr.find("td")
        if not td:
            continue
        sym = clean_ws(td.get_text()).upper()
        short = _SUFFIX.sub("", sym)
        if short != sym and (known is None or short in known):
            sym = short
        if sym and sym not in out:
            out.append(sym)
    return out
