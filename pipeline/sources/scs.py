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


def daily_activity() -> list[dict]:
    """Every listed stock: open/high/low/close, volume, change (market breadth & volume leaders)."""
    return _post("MarketStatistics/MS_DailyActivity.aspx/chartact", {"rows": 3000})


def index_history(days: int = 45, name: str = "KSE 100") -> list[dict]:
    """Daily OHLC + volume for an index over the last `days` calendar days (oldest first)."""
    from datetime import timedelta

    from ..common import now_pkt
    end = now_pkt()
    rows = _post("MarketStatistics/MS_HistoricalIndices.aspx/chart",
                 {"par": name, "date1": (end - timedelta(days=days)).strftime("%m/%d/%Y"),
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
