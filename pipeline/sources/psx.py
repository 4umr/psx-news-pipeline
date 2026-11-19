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


def equity(soup) -> dict:
    """The 'Equity Profile' box as label/value pairs: market cap (Rs 000), shares, face value, free float (shares and
    %). Read item by item: in Nov 2026 PSX added 'Face Value' between 'Shares' and 'Free Float', which broke the
    old one-line pattern (shares and free float stopped updating; Stock of the Day showed them blank)."""
    out: dict = {}
    box = soup.find(id="equity") or soup
    ff = []
    for it in box.select("div.stats_item"):
        lab, val = it.select_one(".stats_label"), it.select_one(".stats_value")
        if not lab or not val:
            continue
        k, v = clean_ws(lab.get_text(" ")).lower(), clean_ws(val.get_text(" "))
        n = _num(v.replace("%", ""))
        if n is None:
            continue
        if k.startswith("market cap"):
            out["mcap_000"] = n
        elif k == "shares":
            out["shares"] = int(n)
        elif k.startswith("face value"):
            out["face_value"] = n
        elif k.startswith("free float"):
            ff.append((v.endswith("%"), n))
    for pct, n in ff:
        if pct:
            out["free_float_pct"] = n
        else:
            out["free_float"] = int(n)
    return out


def company(symbol: str) -> dict | None:
    try:
        r = http().get(f"{BASE}/company/{symbol}", timeout=25)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PSX page %s failed: %s", symbol, e)
        return None
    soup = BeautifulSoup(r.content, "lxml")
    out: dict = {"symbol": symbol, "filings": [], "annual_eps": {}, "quarterly_eps": {}}
    out.update({k: v for k, v in equity(soup).items() if k != "mcap_000"})
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
    out.update(equity(soup))
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


_SCR: dict = {"at": 0.0, "prices": {}, "live": None}


def screener_live(view: list[dict], scr: dict[str, tuple[float, float]]) -> bool | None:
    """Does the PSX screener show today's live prices? Compared on KSE-100 stocks that moved today
    (13 Nov 2026 probe: 93 of 97 matched the live index view)."""
    n = live = 0
    for r in view or []:
        c, p, ld = r.get("company_code"), r.get("CurrentPrice"), r.get("LDCP")
        if not c or not p or not ld or abs(p - ld) < 0.006 or c not in scr:
            continue
        n += 1
        live += abs(scr[c][0] - p) <= max(0.011, p * 0.003)
    return None if n < 15 else live >= 0.6 * n


def live_screener(view: list[dict], max_age: float = 180.0) -> dict[str, tuple[float, float]]:
    """{sym: (price, change %)} for every listed stock, ONLY if the screener agrees with the live KSE-100 view
    right now; else {}. Downloaded at most every `max_age` seconds (one copy shared by the whole job)."""
    import time
    if time.time() - _SCR["at"] > max_age:
        scr = screener()
        _SCR.update(at=time.time(), prices=scr, live=screener_live(view, scr) if scr else None)
    return _SCR["prices"] if _SCR["live"] else {}


def screener() -> dict[str, tuple[float, float]]:
    """PSX screener page (dps.psx.com.pk/screener): every listed stock with price and change % (no volume).
    {sym: (price, change %)}. Whether it is live during the session is checked by runner._screener_probe."""
    try:
        t = http().get("https://dps.psx.com.pk/screener", timeout=40).text
    except Exception as e:  # noqa: BLE001
        log.warning("PSX screener failed: %s", e)
        return {}
    out = {}
    for row in re.findall(r"<tr><td data-order=\"([A-Z0-9-]+)\">(.*?)</tr>", t, flags=re.S):
        nums = re.findall(r'data-order="(-?[0-9.eE+-]+)"', row[1])
        # after the symbol: market cap, price, change %, 1-year %, P/E, dividend yield, free float, 30-day volume
        if len(nums) >= 3:
            try:
                out[row[0]] = (float(nums[1]), float(nums[2]))
            except ValueError:
                continue
    return out
