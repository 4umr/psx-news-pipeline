"""Everything scstrade.com shows about ONE company, for the Research Lab (pipeline/lab.py).

- snapshot(sym)          company snapshot page: price ranges, returns, beta, free float, EPS (latest, annual,
                         by quarter), P/E and expected P/E, book value, ROE/ROA, dividends and yield, sales,
                         EV/EBITDA, margins, liquidity, debt, insider transactions, recent announcements
- technicals()           technical report for all covered stocks: MACD daily/weekly, RSI, support and
                         resistance, 50/100-day averages, 52-week range
- signal(sym)            SCS's own current trading signal (buy/sell since a date, the level that flips it)
- signal_record(sym)     their signal history -> how their buy/sell calls on this stock have worked
- research(sym)          SCS company report titles (PDF links)
- opinions()             SCS analysts' opinion notes, by company

All values are returned as plain numbers where possible; anything missing is simply absent.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from ..common import clean_ws, date_from_ms, http, log
from . import scs

BASE = scs.BASE


def _val(s: str):
    """'Rs. 1,363.48 bn' -> 1363.48 (bn kept as given), '5.63 x' -> 5.63, '15.00 %' -> 15.0, 'N/A %' -> None."""
    s = (s or "").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


# snapshot field id -> (name, unit) ; the page labels each value with a period ("Upto 2026 4Q")
FIELDS = {
    "price": ("Price", "Rs"), "mktcap": ("Market cap", "bn"), "avgvol": ("Average daily volume, 12 months", "shares"),
    "1mlow": ("1-month low", "Rs"), "1mhigh": ("1-month high", "Rs"), "6mlow": ("6-month low", "Rs"),
    "6mhigh": ("6-month high", "Rs"), "52low": ("52-week low", "Rs"), "52high": ("52-week high", "Rs"),
    "1mperf": ("Return 1 month", "%"), "3mperf": ("Return 3 months", "%"), "6mperf": ("Return 6 months", "%"),
    "12mperf": ("Return 12 months", "%"), "nos": ("Shares outstanding", "mn"), "freefloat": ("Free float shares", "mn"),
    "beta": ("Beta", ""), "facevalue": ("Face value", "Rs"),
    "leps": ("EPS year to date", "Rs"), "epslq": ("EPS last quarter", "Rs"), "laeps": ("EPS last full year", "Rs"),
    "pe": ("P/E (last full year)", "x"), "epe": ("Expected P/E (SCS)", "x"), "eeg": ("Expected earnings growth (SCS)", "%"),
    "bv": ("Book value per share", "Rs"), "ebv": ("Expected book value per share (SCS)", "Rs"),
    "pbv": ("Price to book", "x"), "epbv": ("Expected price to book (SCS)", "x"),
    "roe": ("Return on equity", "%"), "eroe": ("Expected return on equity (SCS)", "%"), "roa": ("Return on assets", "%"),
    "roce": ("Return on capital employed", "%"),
    "id": ("Dividend year to date", "Rs"), "d": ("Dividend last full year", "Rs"), "dy": ("Dividend yield", "%"),
    "ed": ("Expected dividend (SCS)", "Rs"), "edy": ("Expected dividend yield (SCS)", "%"),
    "dc": ("Dividend cover", "x"), "pr": ("Payout ratio", "%"),
    "sps": ("Sales per share", "Rs"), "esps": ("Expected sales per share (SCS)", "Rs"), "ps": ("Price to sales", "x"),
    "spsg": ("Sales per share growth", "%"), "espsg": ("Expected sales growth (SCS)", "%"),
    "ev": ("Enterprise value", "bn"), "eve": ("EV to EBITDA", "x"), "evps": ("EV per share", "Rs"),
    "cfps": ("Cash flow per share", "Rs"), "cps": ("Cash per share", "Rs"),
    "npm": ("Net profit margin", "%"), "gpm": ("Gross margin", "%"), "ebitdam": ("EBITDA margin", "%"),
    "cr": ("Current ratio", "x"), "etr": ("Effective tax rate", "%"),
    "tde": ("Total debt to equity", "%"), "tda": ("Total debt to assets", "%"),
    "ear": ("Equity to assets", "%"), "lde": ("Long-term debt to equity", "%"), "qr": ("Quick ratio", "x"),
}
DESC = {"leps": "lepsdesc", "epslq": "epslqdesc", "laeps": "laepsdesc", "pe": "pedesc", "epe": "epedesc",
        "eeg": "eegdesc", "bv": "bvdesc", "pbv": "pbvdesc", "roe": "roedesc", "d": "ddesc", "dy": "dydesc",
        "ed": "eddesc", "edy": "edydesc", "ear": "eardesc", "pr": "prdesc", "cr": "crdesc", "qr": "qrdesc",
        "cps": "cpsdesc", "tda": "tdadesc"}


def snapshot(sym: str) -> dict | None:
    try:
        r = http().get(f"{BASE}stockscreening/SS_CompanySnapShot.aspx?symbol={sym}", timeout=40)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("SCS snapshot %s failed: %s", sym, e)
        return None
    s = BeautifulSoup(r.text, "lxml")
    raw = {sp["id"][24:]: clean_ws(sp.get_text(" ")) for sp in s.find_all("span", id=re.compile(r"^ContentPlaceHolder1_lbl_"))}
    if not raw.get("price"):
        return None
    out = {"symbol": sym, "name": raw.get("companyname", sym).split(" - ", 1)[-1], "sector": raw.get("sector", ""),
           "values": {}, "period": {}}
    for k, (name, unit) in FIELDS.items():
        v = _val(raw.get(k, ""))
        if v is not None:
            out["values"][k] = v
            if k in DESC and raw.get(DESC[k]):
                out["period"][k] = raw[DESC[k]].replace("Upto", "to").strip()
    m = re.search(r"\(\s*(-?[\d.]+)\s*%\)", raw.get("change", ""))
    out["values"]["chg_pct"] = float(m.group(1)) if m else None
    # EPS by year and by quarter from the chart data
    h = r.text
    cats = re.findall(r"categories\s*:\s*\[([^\]]*)\]", h)
    years = next((re.findall(r"'(\d{4})'", c) for c in cats if c.count("'") >= 14), [])
    m = re.search(r"name\s*:\s*'EPS'\s*,\s*data\s*:\s*\[([^\]]*)\]", h)
    if m and years:
        vals = [_val(x) for x in m.group(1).split(",")]
        out["eps_annual"] = {y: v for y, v in zip(years, vals) if v is not None}
    qcats = next((re.findall(r"'(\d{4})'", c) for c in cats if 8 <= c.count("'") <= 12), [])
    q = {}
    for name in ("Q1", "Q2", "Q3", "Q4"):
        m = re.search(rf"name\s*:\s*'{name}'\s*,\s*data\s*:\s*\[([^\]]*)\]", h)
        if m and qcats:
            for y, v in zip(qcats, [_val(x) for x in m.group(1).split(",")]):
                if v:  # 0.00 = not reported yet
                    q[f"{y} {name}"] = v
    out["eps_quarters"] = q
    # insider transactions and recent announcements (tables)
    out["insiders"], out["announcements"] = [], []
    for t in s.find_all("table"):
        head = [clean_ws(th.get_text(" ")) for th in t.find_all("th")]
        rows = [[clean_ws(td.get_text(" ")) for td in tr.find_all("td")] for tr in t.find_all("tr")[1:]]
        if head[:4] == ["Date", "Name", "Designation", "Action"]:
            out["insiders"] = [{"date": r[0], "who": r[2], "action": r[3], "qty": _val(r[4]), "rate": _val(r[5])}
                               for r in rows if len(r) >= 6]
        elif head[:3] == ["Ann Type", "Ann Date", "Description"]:
            # SCS appends "at <today>" to board notices: strip it so no one reads it as a meeting date
            out["announcements"] = [{"type": r[0], "date": r[1], "title": re.sub(r"\s+at \d{1,2}-[A-Za-z]{3}-\d{4}$", "", r[2])}
                                    for r in rows if len(r) >= 3]
    return out


def technicals() -> dict[str, dict]:
    out = {}
    for r in scs._post("TechnicalAnalysis/TA_TechnicalReport.aspx/resdata"):
        code = (r.get("company_code") or "").strip()
        if code:
            out[code] = {k: (_val(str(v)) if k not in ("macddaily", "macdweekly") else v)
                         for k, v in r.items() if k not in ("company_code", "company_name", "sector_name")}
    return out


def signal(sym: str) -> dict | None:
    for idx in ("KSE100", ""):
        for r in scs._post("TechnicalAnalysis/TA_TradingSignals.aspx/chart", {"index": idx}):
            if (r.get("company_code") or "").strip() == sym:
                d = date_from_ms(r.get("currentsd", ""))
                return {"signal": r.get("currents"), "since": f"{d:%Y-%m-%d}" if d else "",
                        "at": _val(str(r.get("currentsr"))), "flip": r.get("nextsignal", ""),
                        "pl": _val(str(r.get("pl"))) if r.get("pl") is not None else None}
    return None


def signal_record(sym: str, years: int = 3) -> dict | None:
    """SCS buy/sell signals on this stock over `years` years: each buy held until the next sell."""
    end = datetime.now()
    rows = scs._post("TechnicalAnalysis/TA_TradingSignalsHistory.aspx/grid",
                     {"code": sym, "date1": f"{end - timedelta(days=365 * years):%m/%d/%Y}", "date2": f"{end:%m/%d/%Y}",
                      "lq": "", "sq": "", "cm": "", "cfs": "", "lpl": "", "spl": ""})
    sig = []
    for r in rows:
        try:
            sig.append((datetime.strptime(r["Date"], "%m/%d/%Y"), r["Signal"], float(r["Close"])))
        except (KeyError, ValueError):
            continue
    sig.sort()
    trades = []
    for (d1, s1, p1), (d2, s2, p2) in zip(sig, sig[1:]):
        if s1 == "Buy" and s2 == "Sell":
            trades.append((p2 / p1 - 1) * 100)
    if not trades:
        return None
    return {"trades": len(trades), "win": round(sum(t > 0 for t in trades) / len(trades) * 100),
            "avg": round(sum(trades) / len(trades), 2), "signals": len(sig), "years": years}


def research(sym: str, days: int = 180) -> list[dict]:
    end = datetime.now()
    rows = scs._post("research/RE_Reports.aspx/chart", {"par": sym, "date1": f"{end - timedelta(days=days):%m/%d/%Y}",
                                                         "date2": f"{end:%m/%d/%Y}"})
    out = []
    for r in rows:
        d = date_from_ms(r.get("file_date", ""))
        out.append({"date": f"{d:%Y-%m-%d}" if d else "", "title": clean_ws(r.get("FileTitle") or "").rstrip(". ")})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def opinions() -> dict[str, list[dict]]:
    """SCS analysts' notes on the Analyst Opinions page, grouped by company symbol."""
    try:
        r = http().get(f"{BASE}AnalystOpinionMain.aspx", timeout=40)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("SCS analyst opinions failed: %s", e)
        return {}
    out: dict[str, list[dict]] = {}
    # each note is a small table: "SYM - Company", title, "Author  m/d/yyyy h:mm:ss PM", text
    for t in BeautifulSoup(r.text, "lxml").find_all("table", class_="sTable"):
        cells = [clean_ws(td.get_text(" ")) for td in t.find_all("td")]
        if len(cells) < 4 or " - " not in cells[0]:
            continue
        sym = cells[0].split(" - ", 1)[0].strip()
        m = re.match(r"(.*?)\s*(\d{1,2}/\d{1,2}/\d{4})", cells[2])
        try:
            d = datetime.strptime(m.group(2), "%m/%d/%Y").strftime("%Y-%m-%d") if m else ""
        except ValueError:
            d = ""
        out.setdefault(sym, []).append({"date": d, "title": cells[1], "author": m.group(1).strip() if m else "",
                                        "text": cells[3][:1500]})
    return out


def book_values(sym: str) -> dict[str, float]:
    """Book value per share by fiscal year label (latest first on the page, up to 5 years), from the snapshot
    chart. The newest label can be a part year (e.g. '2027' = up to 2027 1Q)."""
    try:
        h = http().get(f"{BASE}stockscreening/SS_CompanySnapShot.aspx?symbol={sym}", timeout=40).text
    except Exception as e:  # noqa: BLE001
        log.warning("SCS book values %s failed: %s", sym, e)
        return {}
    i = h.find("name: 'Book Value'")
    if i < 0:
        return {}
    cats = re.findall(r"'(\d{4})'", h[h.rfind("categories", 0, i): i])
    m = re.search(r"data\s*:\s*\[([^\]]*)\]", h[i: i + 400])
    if not m or not cats:
        return {}
    vals = [_val(x) for x in m.group(1).split(",")]
    return {y: v for y, v in zip(cats, vals) if v is not None and v != 0}
