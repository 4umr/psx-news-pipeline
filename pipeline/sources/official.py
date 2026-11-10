"""Official monthly and fortnightly data, read straight from the publishers (hours before the papers):

- fuel_prices()   petrol / diesel / kerosene / LDO prices from Pakistan State Oil's price page
- remittances()   workers' remittances by month and country (SBP spreadsheet)
- bop()           balance of payments summary: current account, trade, remittances, FDI (SBP PDF)
- auto_link() + auto_sales()   PAMA monthly production and sales of vehicles (PDF table)

Every reader returns None when the page cannot be fetched or does not look as expected, and applies
its own sanity checks, so a changed layout can never put a wrong number on the channel.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from ..common import clean_ws, http, log

FUEL_URL = "https://psopk.com/en/fuels/fuel-prices"
REMIT_URL = "https://www.sbp.org.pk/assets/document/Homeremit_Arch.xlsx"
BOP_URL = "https://www.sbp.org.pk/assets/document/Balancepayment_BPM6.pdf"
SBP_DATA_PAGE = "https://www.sbp.org.pk/ecodata/index2.asp"
PAMA_PAGE = "https://pama.org.pk/monthly-production-sales-of-vehicles/"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]


def _get(url: str, timeout: int = 45):
    try:
        r = http().get(url, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:  # noqa: BLE001
        log.warning("official data: %s failed: %s", url.split("/")[2], e)
        return None


def _modified(r) -> datetime | None:
    try:
        return parsedate_to_datetime(r.headers["Last-Modified"])
    except Exception:  # noqa: BLE001
        return None


def _num(s) -> float | None:
    s = str(s).replace(",", "").replace("−", "-").replace("–", "-").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _mon(s: str) -> int | None:
    return MONTHS.get(str(s).strip().lower()[:3])


# ---------------------------------------------------------------- fuel prices (PSO)
FUEL_PRODUCTS = {"petrol": r"PREMIER EURO 5", "diesel": r"HI-?CETANE DIESEL EURO 5", "kerosene": r"SKO",
                 "ldo": r"LDO"}


def parse_fuel(text: str) -> dict | None:
    i = text.find("Effective From")
    if i < 0:
        return None
    j = text.find("Archives", i)
    block = text[i: j if j > 0 else i + 700]
    m = re.search(r"Effective From:?\s*([A-Za-z]+)\.? (\d{1,2}),? (\d{4})", block)
    if not m or not _mon(m.group(1)):
        return None
    prices = {}
    for key, pat in FUEL_PRODUCTS.items():
        p = re.search(pat + r"\s*Rs\.?\s*([\d,]*\d(?:\.\d+)?)\s*/\s*Ltr", block, re.I)
        if p and (v := _num(p.group(1))) and 50 <= v <= 2000:
            prices[key] = v
    if "petrol" not in prices or "diesel" not in prices:
        return None
    return {"effective": f"{int(m.group(3)):04d}-{_mon(m.group(1)):02d}-{int(m.group(2)):02d}", "prices": prices}


def fuel_prices() -> dict | None:
    r = _get(FUEL_URL)
    if r is None:
        return None
    return parse_fuel(clean_ws(BeautifulSoup(r.text, "lxml").get_text(" ")))


# ---------------------------------------------------------------- workers' remittances (SBP)
def _col_month(v) -> tuple[int, int] | None:
    """Column label -> (year, month): real dates, or text such as 'Oct-26', 'Sep-26R', 'June-26'."""
    if hasattr(v, "year") and hasattr(v, "month"):
        return v.year, v.month
    m = re.match(r"^\s*([A-Za-z]{3,9})[-' ]?(\d{2})\s*[RP]?\s*$", str(v))
    if m and _mon(m.group(1)):
        return 2000 + int(m.group(2)), _mon(m.group(1))
    return None


def parse_remittances(data: bytes) -> dict | None:
    import pandas as pd
    xl = pd.ExcelFile(io.BytesIO(data))
    sheet = next((s for s in xl.sheet_names if "onward" in s.lower()), xl.sheet_names[-1])
    df = xl.parse(sheet, header=None)
    head = next((i for i in range(min(12, len(df))) if any(str(x).strip() == "Country" for x in df.iloc[i, :4])), None)
    if head is None:
        return None
    cols = {c: ym for c in range(df.shape[1]) if (ym := _col_month(df.iat[head, c]))}
    if len(cols) < 24:
        return None

    def series(row: int) -> dict:
        out = {}
        for c, ym in cols.items():
            v = _num(df.iat[row, c])
            if v is not None and v == v:
                out[ym] = v
        return out

    total = series(head + 1)  # the first row under the header is the total ("Cash")
    if len(total) < 24:
        return None
    y, m = max(total)
    val = total[(y, m)]
    if not 500 <= val <= 15000:
        return None
    prev = total.get((y, m - 1) if m > 1 else (y - 1, 12))
    fy_start = (y, 7) if m >= 7 else (y - 1, 7)
    months = [(yy, mm) for (yy, mm) in total if fy_start <= (yy, mm) <= (y, m)]
    fytd = sum(total[k] for k in months)
    fytd_ly = sum(total.get((yy - 1, mm), float("nan")) for yy, mm in months)
    countries = []
    for row in range(head + 2, min(head + 45, len(df))):
        name = next((str(df.iat[row, c]).strip() for c in (2, 1) if isinstance(df.iat[row, c], str)
                     and df.iat[row, c].strip()), "")
        if not name or name.lower().startswith(("dubai", "abu dhabi", "sharjah", "other", "of which", "total")):
            continue
        s = series(row)
        if (y, m) in s and s[(y, m)] > 0:
            countries.append({"name": name, "value": s[(y, m)], "ly": s.get((y - 1, m))})
    countries.sort(key=lambda c: -c["value"])
    return {"year": y, "month": m, "label": f"{MONTH_NAMES[m]} {y}", "value": val, "prev": prev,
            "ly": total.get((y - 1, m)), "fytd": fytd, "fytd_ly": None if fytd_ly != fytd_ly else fytd_ly,
            "fy": f"FY{(fy_start[0] + 1) % 100:02d}", "n_months": len(months), "countries": countries[:6]}


def remittances() -> dict | None:
    r = _get(REMIT_URL)
    if r is None:
        return None
    try:
        d = parse_remittances(r.content)
    except Exception as e:  # noqa: BLE001
        log.warning("remittances file could not be read: %s", e)
        return None
    if d:
        d["modified"] = _modified(r)
    return d


# ---------------------------------------------------------------- balance of payments (SBP)
BOP_ROWS = {"ca": r"Current Account Balance", "exports": r"Exports of Goods FOB", "imports": r"Imports of Goods FOB",
            "trade": r"Balance on Trade in Goods", "remit": r"Workers' Remittances",
            "fdi": r"Direct Investment in Pakistan"}
_N = r"[-−–]?\s?[\d,]+(?:\.\d+)?"


def parse_bop(text: str) -> dict | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    t = next((ln for ln in lines if "Balance of Payments" in ln), "")
    m = re.search(r"([A-Za-z]+)\s+(\d{4})\s*$", t)
    if not m or not _mon(m.group(1)):
        return None
    mon, year = _mon(m.group(1)), int(m.group(2))
    h = next((i for i, ln in enumerate(lines) if ln.startswith("Items")), None)
    if h is None:
        return None
    periods = lines[h].split()[1:]               # e.g. Jul-Jun, Sep, Jul-Jun, Aug, Sep, Jul-Sep
    fy = [int(x) for x in re.findall(r"FY(\d{2})", lines[h + 1])] if h + 1 < len(lines) else []
    n = len(fy)                                  # one fiscal-year label per column
    if len(periods) == n - 1:                    # the year-to-date heading spans the last two columns
        periods.append(periods[-1])
    if len(periods) != n:
        return None
    is_m = [bool(_mon(p)) and "-" not in p for p in periods]
    same = [i for i, p in enumerate(periods) if is_m[i] and _mon(p) == mon]
    ytd = [i for i, p in enumerate(periods) if "-" in p and not p.lower().startswith("jul-jun")]
    if not same or n < 4:
        return None
    latest, ly = same[-1], (same[0] if len(same) > 1 else None)
    if ly is not None and fy[ly] != fy[latest] - 1:  # the two month columns must be a year apart
        return None
    prev_m = mon - 1 if mon > 1 else 12
    prev = next((i for i, p in enumerate(periods) if is_m[i] and _mon(p) == prev_m and i != latest), None)
    if mon == 7:  # in July the month itself is the year to date
        fytd, fytd_ly = latest, ly
    else:
        fytd = ytd[-1] if ytd else None
        fytd_ly = ytd[-2] if len(ytd) > 1 else None
    out = {"year": year, "month": mon, "label": f"{MONTH_NAMES[mon]} {year}", "rows": {}}
    for key, lab in BOP_ROWS.items():
        ln = next((x for x in lines if re.match(lab + r"\s+" + _N, x)), None)
        if not ln:
            continue
        nums = [_num(x.replace(" ", "")) for x in re.findall(_N, ln[len(re.match(lab, ln).group(0)):])]
        if len(nums) != n:
            continue
        pick = lambda i: None if i is None else nums[i]  # noqa: E731
        out["rows"][key] = {"now": nums[latest], "ly": pick(ly), "prev": pick(prev), "fytd": pick(fytd),
                            "fytd_ly": pick(fytd_ly)}
    ca = out["rows"].get("ca")
    if not ca or ca["now"] is None or abs(ca["now"]) > 8000:
        return None
    ex, im, tr = (out["rows"].get(k) for k in ("exports", "imports", "trade"))
    if ex and im and tr and abs((ex["now"] - im["now"]) - tr["now"]) > 5:  # the table must add up
        return None
    return out


def bop() -> dict | None:
    r = _get(BOP_URL)
    if r is None:
        return None
    try:
        import pypdf
        text = pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text(extraction_mode="layout")
        d = parse_bop(text)
    except Exception as e:  # noqa: BLE001
        log.warning("balance of payments PDF could not be read: %s", e)
        return None
    if d:
        d["modified"] = _modified(r)
    return d


# ---------------------------------------------------------------- vehicle sales (PAMA)
def auto_link() -> str | None:
    """The newest 'Production-Sales-<Month>-<Year>.pdf' on PAMA's page."""
    r = _get(PAMA_PAGE)
    if r is None:
        return None
    best = None
    for a in BeautifulSoup(r.text, "lxml").find_all("a", href=True):
        href = a["href"].replace("%20", "").strip()
        m = re.search(r"/uploads/(\d{4})/(\d{2})/Production-?Sales-?([A-Za-z]+)-?(\d{4})\.pdf$", href, re.I)
        if m and _mon(m.group(3)):
            key = (int(m.group(4)), _mon(m.group(3)))
            if best is None or key > best[0]:
                best = (key, href)
    return best[1] if best else None


_CELL = re.compile(r"^(-|Awaited|[\d,]+)$", re.I)
GROUPS = [  # (name shown, PSX symbol or "", pages, keyword in the row label)
    ("Toyota (Indus Motor)", "INDU", (0, 1), "toyota"),
    ("Honda Atlas Cars", "HCAR", (0, 1), "honda"),
    ("Pak Suzuki", "", (0, 1), "suzuki"),
    ("Hyundai Nishat", "", (0, 1), "hyundai"),
    ("Sazgar (Haval, BAIC)", "SAZEW", (0, 1), "sazgar"),
    ("Millat Tractors", "MTL", (2,), "millat"),
    ("Al-Ghazi Tractors", "AGTL", (2,), "al-ghazi"),
    ("Atlas Honda (bikes)", "ATLH", (3,), "honda"),
]
TOTALS = {"cars": r"TOTAL.*\bCARS\b", "suv": r"TOTAL.*JEEPS", "trucks_buses": r"TOTAL TRUCKS.*BUSES",
          "tractors": r"TOTAL.*TRACTORS", "bikes": r"TOTAL.*Wheelers"}


def parse_autos(pages: list[str]) -> dict | None:
    """pages = layout-mode text of each PDF page. Every company has a 'Prod' row and a 'Sale' row with the
    months of last fiscal year so far + cumulative, then the same for this year."""
    k = None
    rows = []  # (page, kind, label, values)
    for pi, text in enumerate(pages):
        pend_label, pend_kind = [], None
        for ln in text.splitlines():
            toks = [t for t in re.split(r"\s{2,}", ln.strip()) if t]
            if not toks:
                continue
            if "Cumulative" in toks:
                if k is None:
                    k = toks.index("Cumulative") - (0 if _mon(toks[0][:3]) else 1) + 1
                pend_label, pend_kind = [], None
                continue
            if "Page" in ln and "Association" in ln or "PRODUCTION & SALE" in ln:
                pend_label, pend_kind = [], None
                continue
            cells = [t for t in toks if _CELL.match(t)]
            words = [t for t in toks if not _CELL.match(t) and t not in ("Prod", "Sale")]
            kind = "Prod" if "Prod" in toks else ("Sale" if "Sale" in toks else None)
            if len(cells) >= 4:
                kind = kind or pend_kind
                label = " ".join(pend_label + words)
                vals = [None if c.lower() == "awaited" else (0.0 if c == "-" else _num(c)) for c in cells]
                rows.append((pi, kind, label, vals))
                pend_label, pend_kind = [], None
            else:
                pend_label += words
                pend_kind = kind or pend_kind
    if not k or k < 2:
        return None
    ents = []
    i = 0
    while i < len(rows) - 1:
        a, b = rows[i], rows[i + 1]
        if a[1] == "Prod" and b[1] == "Sale" and len(a[3]) == len(b[3]) == 2 * k and a[0] == b[0]:
            ents.append({"page": a[0], "label": clean_ws(f"{a[2]} {b[2]}"), "prod": a[3], "sale": b[3]})
            i += 2
        else:
            i += 1

    def view(sale: list) -> dict:
        return {"now": sale[2 * k - 2], "ly": sale[k - 2], "prev": sale[2 * k - 3] if k >= 3 else None,
                "fytd": sale[2 * k - 1], "fytd_ly": sale[k - 1]}

    totals = {}
    for key, pat in TOTALS.items():
        e = next((e for e in ents if re.search(pat, e["label"], re.I)), None)
        if e and None not in (e["sale"][2 * k - 2], e["sale"][k - 2]):
            totals[key] = view(e["sale"])
    if "cars" not in totals:
        return None
    # the company rows of the passenger car page must add up to PAMA's own total
    parts = [e for e in ents if e["page"] == 0 and not re.search(r"total", e["label"], re.I)]
    s = sum(e["sale"][2 * k - 2] or 0 for e in parts)
    if abs(s - totals["cars"]["now"]) > max(2, 0.01 * totals["cars"]["now"]):
        log.warning("PAMA table does not add up: company rows %s vs total %s", s, totals["cars"]["now"])
        return None
    groups = []
    for name, sym, pgs, kw in GROUPS:
        hit = [e for e in ents if e["page"] in pgs and kw in e["label"].lower()
               and not re.search(r"total", e["label"], re.I)]
        if not hit:
            continue
        add = lambda j: sum(e["sale"][j] or 0 for e in hit)  # noqa: E731
        groups.append({"name": name, "sym": sym, "now": add(2 * k - 2), "ly": add(k - 2),
                       "fytd": add(2 * k - 1), "fytd_ly": add(k - 1)})
    return {"totals": totals, "groups": groups, "months": k - 1}


def auto_sales(url: str) -> dict | None:
    r = _get(url, timeout=60)
    if r is None:
        return None
    m = re.search(r"Production-?Sales-?([A-Za-z]+)-?(\d{4})\.pdf", url, re.I)
    try:
        import pypdf
        pages = [p.extract_text(extraction_mode="layout") for p in pypdf.PdfReader(io.BytesIO(r.content)).pages]
        d = parse_autos(pages)
    except Exception as e:  # noqa: BLE001
        log.warning("PAMA PDF could not be read: %s", e)
        return None
    if d and m and _mon(m.group(1)):
        d.update(year=int(m.group(2)), month=_mon(m.group(1)), label=f"{MONTH_NAMES[_mon(m.group(1))]} {m.group(2)}",
                 url=url, modified=_modified(r))
        return d
    return None
