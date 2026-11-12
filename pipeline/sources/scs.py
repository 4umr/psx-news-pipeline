"""SCS Trade (scstrade.com) data endpoints — PSX market statistics in one place.

These are the JSON endpoints that power the tables on scstrade.com:
results/announcements (EPS, dividend, bonus + PSX PDF link), board meetings,
book closures, KSE-100 index view (live point contributions), index OHLC,
and FIPI/LIPI (foreign/local investor flows).
"""
from __future__ import annotations

import json
from datetime import datetime

from ..common import date_from_ms, http, log

BASE = "https://www.scstrade.com/"


def _post(path: str, extra: dict | None = None) -> list[dict]:
    payload = {"_search": False, "nd": 1, "rows": 500, "page": 1, "sidx": "", "sord": "asc"}
    payload.update(extra or {})
    try:
        r = http().post(BASE + path, data=json.dumps(payload), timeout=30,
                        headers={"Content-Type": "application/json; charset=utf-8",
                                 "X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        return r.json().get("d") or []
    except Exception as e:  # noqa: BLE001
        log.warning("SCS %s failed: %s", path, e)
        return []


def results() -> list[dict]:
    """Latest financial results announcements (EPS / dividend / bonus)."""
    rows = _post("MarketStatistics/MS_Announcements.aspx/chartact", {"par": ""})
    for r in rows:
        r["date"] = date_from_ms(r.get("bm_date", ""))
        r["company_name"] = (r.get("company_name") or "").strip()
    return rows


def results_history(symbol: str) -> list[dict]:
    """Every results announcement of one company since ~2010 (date, time, period, EPS, dividend)."""
    rows = _post("MarketStatistics/MS_Announcements.aspx/chartact", {"par": symbol})
    for r in rows:
        r["date"] = date_from_ms(r.get("bm_date", ""))
    return [r for r in rows if r.get("date") and (r.get("company_code") or "").strip() == symbol]


def board_meetings() -> list[dict]:
    rows = _post("MarketStatistics/MS_BoardMeetings.aspx/chartact", {"par": ""})
    for r in rows:
        r["date"] = date_from_ms(r.get("bm_date", ""))
        r["company_name"] = (r.get("company_name") or "").strip()
    return rows


def book_closures() -> list[dict]:
    rows = _post("MarketStatistics/MS_xDates.aspx/chartact", {"par": ""})
    for r in rows:
        r["company_name"] = (r.get("company_name") or "").strip()
        try:
            r["date"] = datetime.strptime(r.get("bm_bc_exp", "").strip(), "%d %b %Y")
        except ValueError:
            r["date"] = None
    return rows


def kse100_view() -> list[dict]:
    """KSE-100 constituents with current price, index level and point contribution."""
    return _post("MarketStatistics/MS_IndexView.aspx/chartact")


_ACT: dict = {"at": 0.0, "rows": []}


def daily_activity(max_age: float = 30.0) -> list[dict]:
    """Every listed stock: open/high/low/close, volume, change (market breadth & volume leaders).
    Several watchers need it in the same pass, so one download is shared for `max_age` seconds."""
    import time
    if _ACT["rows"] and time.time() - _ACT["at"] < max_age:
        return _ACT["rows"]
    rows = _post("MarketStatistics/MS_DailyActivity.aspx/chartact", {"rows": 3000})
    if rows:
        _ACT.update(at=time.time(), rows=rows)
    return rows


_VIEW: dict = {"at": 0.0, "rows": []}


def kse100_view_cached(max_age: float = 30.0) -> list[dict]:
    import time
    if _VIEW["rows"] and time.time() - _VIEW["at"] < max_age:
        return _VIEW["rows"]
    rows = kse100_view()
    if rows:
        _VIEW.update(at=time.time(), rows=rows)
    return rows


def activity_is_today(act: list[dict], view: list[dict]) -> bool | None:
    """The all-stock daily-activity table is updated ONCE a day, about 16:10-16:30 PKT (found 13 Nov 2026 from
    the intraday recorder: no change between 09:15 and 16:13). Until then it holds the PREVIOUS session.
    Test: for KSE-100 stocks that moved today (live index view: price != previous close), does the table
    show today's price (True) or yesterday's close (False)? None = cannot tell."""
    a = {(r.get("company_code") or "").strip(): r.get("trading_close") for r in act}
    n = cur = 0
    for r in view or []:
        c, p, ld = (r.get("company_code") or "").strip(), r.get("CurrentPrice"), r.get("LDCP")
        if not c or not p or not ld or abs(p - ld) < 0.006 or a.get(c) is None:
            continue
        n += 1
        cur += abs(a[c] - p) < 0.006
    return None if n < 15 else cur >= 0.6 * n


def live_activity(view: list[dict] | None = None) -> tuple[list[dict], bool]:
    """Rows shaped like daily_activity() that are about TODAY: the full table once it has been updated after
    the close; before that, the KSE-100 stocks from the live index view (price and change; no volume).
    Returns (rows, full) where full=True means every stock with today's volume."""
    view = view if view is not None else kse100_view_cached()
    act = daily_activity()
    if activity_is_today(act, view):
        return act, True
    sec = {(r.get("company_code") or "").strip(): r for r in act}
    rows = []
    for r in view or []:
        c, p, ld = (r.get("company_code") or "").strip(), r.get("CurrentPrice"), r.get("LDCP")
        if c and p and ld:
            o = sec.get(c) or {}
            rows.append({"company_code": c, "company_name": r.get("company_name") or o.get("company_name", ""),
                         "sector_name": o.get("sector_name", ""), "trading_close": p, "trading_change": round(p - ld, 4),
                         "trading_vol": None})
    return rows, False


def price_history(symbol: str, start: datetime, end: datetime) -> list[dict]:
    """Daily OHLC + volume for one stock (available back to 2018).

    `trading_change` is measured against PSX's reference price, which PSX adjusts on ex-dates
    (cash dividend, bonus, right, split). close - change != previous close marks such a date.
    """
    rows = _post("MarketStatistics/MS_HistoricalPrices.aspx/chart",
                 {"par": symbol, "rows": 5000, "date1": start.strftime("%m/%d/%Y"), "date2": end.strftime("%m/%d/%Y")})
    for r in rows:
        r["date"] = date_from_ms(r.get("trading_Date", ""))
    return [r for r in rows if r.get("date") and r.get("trading_close")]


def index_history(days: int = 45, name: str = "KSE 100") -> list[dict]:
    """Daily OHLC + volume for an index over the last `days` calendar days (oldest first)."""
    from datetime import timedelta

    from ..common import now_pkt
    end = now_pkt()
    rows = _post("MarketStatistics/MS_HistoricalIndices.aspx/chart",
                 {"par": name, "rows": 5000, "date1": (end - timedelta(days=days)).strftime("%m/%d/%Y"),
                  "date2": end.strftime("%m/%d/%Y")})
    for r in rows:
        r["date"] = date_from_ms(r.get("kse_index_date", ""))
    rows = [r for r in rows if r.get("date") and r.get("kse_index_close")]
    rows.sort(key=lambda r: r["date"])
    return rows


def indices() -> list[dict]:
    """OHLC + change for KSE-100, KSE-30, KMI-30, KSE All etc."""
    rows = _post("MarketStatistics/MS_DailyActivity.aspx/chartind")
    for r in rows:
        r["kse_index_type"] = (r.get("kse_index_type") or "").strip()
    return rows


def fipi(day: datetime) -> dict | None:
    """Foreign/local investor flows for one day. None if not published yet."""
    d = day.strftime("%m/%d/%Y")
    summary = _post("FIPILIPI.aspx/loadmainsum", {"date1": d, "date2": d})
    if not summary or all(s.get("FLNetValueUSD") is None for s in summary):
        return None
    out = {"date": day.strftime("%Y-%m-%d"), "summary": {}, "lipi": {}, "fipi": {}}
    for s in summary:
        out["summary"][s["FLType"]] = {"buy": s.get("FLBuyValue"), "sell": s.get("FLSellValue"),
                                       "net": s.get("FLNetValueUSD")}
    # Breakdown by investor type, all markets, in PKR (summary above is USD m)
    for kind, method in (("lipi", "loadlipi"), ("fipi", "loadfipi")):
        for row in _post(f"FIPILIPI.aspx/{method}", {"date1": d, "date2": d}):
            t = (row.get("FLType") or "").strip().title()
            out[kind][t] = out[kind].get(t, 0) + (row.get("NetValue") or 0)
    return out
