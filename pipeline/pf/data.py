"""Market data for a portfolio review: prices since 2008, dividends separated from price moves, the KSE-100, the
risk-free rate, macro factors, company facts and fundamentals. Everything is downloaded fresh for the holdings
(about 5 requests per stock) so the review works for any PSX stock, also outside the swing price database.

Two price series per stock (both from PSX's own reference prices, prices.adjust):
  tr  total return: adjusted for cash dividends, bonus shares, rights and splits (what an investor earned)
  px  price return: adjusted for bonus shares, rights and splits only (comparable with the KSE-100, which is a
      price index without dividends)
A day's adjustment counts as a cash dividend when an announced payout (scstrade results history: ex-date and
dividend % of face value) matches it within 1%; unmatched small adjustments (factor 0.95 or above) are taken as
dividends, larger ones as capital changes.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..common import log, now_pkt

START = datetime(2008, 1, 1)

# SBP policy rate decisions (discount rate until 2015, then the target / policy rate), % a year, from the dates
# announced. Used as the risk-free rate in Sharpe, Sortino, alpha. The latest decision is also read from the news
# pipeline's state (snap sbp.policy_rate) so a new decision is used without a code change.
POLICY = [
    ("2007-08-01", 10.0), ("2008-01-31", 10.5), ("2008-05-22", 12.0), ("2008-07-29", 13.0), ("2008-11-12", 15.0),
    ("2009-04-20", 14.0), ("2009-08-15", 13.0), ("2009-11-25", 12.5), ("2010-07-31", 13.0), ("2010-09-29", 13.5),
    ("2010-11-29", 14.0), ("2011-07-30", 13.5), ("2011-10-08", 12.0), ("2012-08-10", 10.5), ("2012-10-05", 10.0),
    ("2012-12-14", 9.5), ("2013-06-21", 9.0), ("2013-09-13", 9.5), ("2013-11-15", 10.0), ("2014-11-15", 9.5),
    ("2015-01-24", 8.5), ("2015-03-21", 8.0), ("2015-05-16", 7.0), ("2015-09-12", 6.0), ("2016-05-21", 5.75),
    ("2018-01-26", 6.0), ("2018-05-25", 6.5), ("2018-07-13", 7.5), ("2018-09-29", 8.5), ("2018-11-30", 10.0),
    ("2019-01-31", 10.25), ("2019-03-29", 10.75), ("2019-05-20", 12.25), ("2019-07-16", 13.25),
    ("2020-03-17", 12.5), ("2020-03-24", 11.0), ("2020-04-16", 9.0), ("2020-05-15", 8.0), ("2020-06-25", 7.0),
    ("2021-09-20", 7.25), ("2021-11-19", 8.75), ("2021-12-14", 9.75), ("2022-04-07", 12.25), ("2022-05-23", 13.75),
    ("2022-07-07", 15.0), ("2022-11-25", 16.0), ("2023-01-23", 17.0), ("2023-03-02", 20.0), ("2023-04-04", 21.0),
    ("2023-06-26", 22.0), ("2024-06-10", 20.5), ("2024-07-29", 19.5), ("2024-09-12", 17.5), ("2024-11-04", 15.0),
    ("2024-12-16", 13.0), ("2025-01-27", 12.0), ("2025-05-05", 11.0), ("2025-12-15", 10.5), ("2026-04-27", 11.5),
    ("2026-10-26", 12.0),
]


def policy_schedule(snap_sbp: dict | None = None, mpc_at: dict | None = None) -> list[tuple[str, float]]:
    """POLICY plus the latest decision from the news pipeline's state when it is newer than the list."""
    sched = list(POLICY)
    cur = (snap_sbp or {}).get("policy_rate")
    if cur and abs(cur - sched[-1][1]) > 1e-9:
        # the date of the last MPC meeting the state recorded (policy_rate_at holds the rate BEFORE that meeting)
        when = max((mpc_at or {}), default=None) or now_pkt().strftime("%Y-%m-%d")
        if when > sched[-1][0]:
            sched.append((when, float(cur)))
    return sched


def rf_daily(dates: pd.DatetimeIndex, A: float, sched: list[tuple[str, float]]) -> pd.Series:
    """The policy rate in force each day as a daily rate ((1 + r)^(1/A) - 1)."""
    s = pd.Series({pd.Timestamp(d): v for d, v in sched}).sort_index()
    annual = s.reindex(s.index.union(dates)).ffill().reindex(dates) / 100
    return (1 + annual) ** (1 / A) - 1


def policy_cycles(sched: list[tuple[str, float]], min_move: float = 3.0) -> list[dict]:
    """Rate cycles from the schedule: consecutive moves in one direction totalling at least `min_move` points."""
    out, i = [], 1
    while i < len(sched):
        d = np.sign(sched[i][1] - sched[i - 1][1])
        j = i
        while j + 1 < len(sched) and np.sign(sched[j + 1][1] - sched[j][1]) == d:
            j += 1
        move = sched[j][1] - sched[i - 1][1]
        if d and abs(move) >= min_move:
            out.append({"start": sched[i][0], "end": sched[j][0], "from": sched[i - 1][1], "to": sched[j][1],
                        "kind": "hikes" if d > 0 else "cuts"})
        i = j + 1
    return out


# ---------------------------------------------------------------- downloads
def _prices(sym: str, now: datetime) -> pd.DataFrame:
    from .. import prices
    rows = []
    for a, b in ((START, datetime(2016, 12, 31)), (datetime(2017, 1, 1), now)):
        rows += prices._rows(sym, a, b)
    df = pd.DataFrame(rows, columns=prices.COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(["sym", "date"], keep="last")


def index_series(now: datetime, name: str = "KSE 100") -> pd.Series:
    from ..sources import scs
    out = {}
    for a, b in ((START - timedelta(days=10), datetime(2016, 12, 31)), (datetime(2017, 1, 1), now)):
        rows = scs._post("MarketStatistics/MS_HistoricalIndices.aspx/chart",
                         {"par": name, "rows": 5000, "date1": a.strftime("%m/%d/%Y"), "date2": b.strftime("%m/%d/%Y")})
        for r in rows:
            from ..common import date_from_ms
            d = date_from_ms(r.get("kse_index_date", ""))
            if d and r.get("kse_index_close"):
                out[pd.Timestamp(d.strftime("%Y-%m-%d"))] = float(r["kse_index_close"])
    return clean_index(pd.Series(out).sort_index())


def clean_index(s: pd.Series) -> pd.Series:
    """Drop weekend rows and one-day spikes that reverse the next day (7 Jan 2024, a Sunday: 78,824 between
    64,515 and 64,237)."""
    s = s[s.index.dayofweek < 5]
    v = s.values
    bad = [i for i in range(1, len(v) - 1) if abs(v[i] / v[i - 1] - 1) > 0.12 and abs(v[i + 1] / v[i - 1] - 1) < 0.03]
    return s.drop(s.index[bad]) if bad else s


def payouts(sym: str) -> list[dict]:
    """Announced payouts with their ex-dates: [{ex, div_pct, bonus_pct, right_pct}]."""
    from ..earnings import _pct
    from ..sources import scs
    out = []
    for r in scs.results_history(sym):
        try:
            ex = pd.Timestamp(datetime.strptime((r.get("bm_bc_exp") or "").strip(), "%d %b %Y"))
        except ValueError:
            continue
        out.append({"ex": ex, "div": _pct(r.get("bm_dividend")) or 0.0, "bonus": _pct(r.get("bm_bonus")) or 0.0,
                    "right": _pct(r.get("bm_right_per")) or 0.0})
    return out


def split_series(adj: pd.DataFrame, pays: list[dict], face: float) -> tuple[pd.Series, pd.Series, list[dict]]:
    """One stock's adjusted frame (prices.adjust) -> (total-return close, price-return close, events found)."""
    a = adj.sort_values("date").set_index("date")
    f = a["exf"].astype(float)
    prev = a["c"].shift(1)
    price_f = pd.Series(1.0, index=a.index)
    events = []
    for d in f.index[f < 1.0]:
        fac, p = float(f.loc[d]), float(prev.loc[d]) if pd.notna(prev.loc[d]) else None
        near = [x for x in pays if abs((x["ex"] - d).days) <= 4]
        kind, pf = None, fac
        for x in near:
            fd = 1 - x["div"] / 100 * face / p if (x["div"] and p) else 1.0
            if x["bonus"] or x["right"]:
                kind, pf = "capital", fac / fd
                break
            if x["div"] and abs(fac / fd - 1) < 0.01:
                kind, pf = "dividend", 1.0
                break
        if kind is None:
            kind, pf = ("dividend", 1.0) if fac >= 0.95 else ("capital", fac)
        price_f.loc[d] = min(pf, 1.0)
        events.append({"date": d, "factor": fac, "kind": kind})
    later_tr = f[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    later_px = price_f[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    return a["c"] * later_tr, a["c"] * later_px, events


def factors(now: datetime) -> pd.DataFrame | None:
    """Month-end USD/PKR and Brent crude (Yahoo Finance via yfinance), for the macro scenarios."""
    try:
        import yfinance as yf
        out = {}
        for key, tick in (("usdpkr", "PKR=X"), ("brent", "BZ=F")):
            h = yf.Ticker(tick).history(start="2014-01-01", end=(now + timedelta(days=1)).strftime("%Y-%m-%d"),
                                        auto_adjust=False)
            if h is None or h.empty:
                return None
            s = h["Close"].copy()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            out[key] = s[s > 0].resample("ME").last()
        return pd.DataFrame(out).dropna()
    except Exception as e:  # noqa: BLE001
        log.warning("portfolio: macro factors unavailable (%s)", type(e).__name__)
        return None


# ---------------------------------------------------------------- everything for one review
def load(syms: list[str], cfg: dict, state: dict | None = None) -> dict:
    """All data for the holdings. state = the news pipeline's saved state (read only) or None."""
    from .. import prices
    from ..sources import psx, scs, scs_deep
    now = now_pkt().replace(tzinfo=None)
    snap = (state or {}).get("snap") or {}
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    view = scs.kse100_view_cached()
    live, full = scs.live_activity(view, wide=True)
    live_px = {r["company_code"]: r["trading_close"] for r in live if r.get("trading_close")}

    with ThreadPoolExecutor(4) as ex:
        hist = dict(zip(syms, ex.map(lambda s: _prices(s, now), syms)))
        snaps = dict(zip(syms, ex.map(scs_deep.snapshot, syms)))
        pays = dict(zip(syms, ex.map(payouts, syms)))
    idx = index_series(now)
    idx = idx[~idx.index.duplicated()]

    raw = pd.concat([h for h in hist.values() if not h.empty], ignore_index=True) if any(not h.empty for h in hist.values()) \
        else pd.DataFrame(columns=prices.COLS)
    adj = prices.adjust(raw) if len(raw) else raw
    tr, px, adv, events, first = {}, {}, {}, {}, {}
    for s in syms:
        a = adj[adj["sym"] == s] if len(adj) else adj
        if a is None or len(a) < 5:
            continue
        face = ((snaps.get(s) or {}).get("values") or {}).get("facevalue") or 10.0
        t, p, ev = split_series(a, pays.get(s, []), face)
        tr[s], px[s], events[s] = t, p, ev
        first[s] = a["date"].min()
        v = a.sort_values("date").tail(60)
        adv[s] = float(np.median((v["c"] * v["v"]).values)) if len(v) else None

    members = view or []
    kse_w = {(r.get("company_code") or "").strip(): (r.get("IndexWeightCur") or 0) / 100 for r in members}
    sector = {c: (r.get("sector_name") or "").strip() for c, r in act.items()}
    try:
        kmi = set(psx.index_members("KMIALLSHR", set(act)))
    except Exception:  # noqa: BLE001
        kmi = set()
    try:
        from .. import ta
        tier = ta.universe().get("tier") or {}
    except Exception:  # noqa: BLE001
        tier = {}
    try:
        meta = json.loads(prices.META_PATH.read_text(encoding="utf-8")) if prices.META_PATH.exists() else {}
    except (OSError, ValueError):
        meta = {}

    sbp = snap.get("sbp") or {}
    sched = policy_schedule(sbp, snap.get("policy_rate_at"))
    return {"now": now, "act": act, "view": view, "live_px": live_px, "live_full": full, "tr": tr, "px": px,
            "adv": adv, "events": events, "first": first, "idx": idx, "snaps": snaps, "pays": pays,
            "kse_w": kse_w, "sector": sector, "kmi": kmi, "tier": tier, "meta": meta, "sbp": sbp,
            "sched": sched, "factors": factors(now), "raw": raw}


def sessions_per_year(idx: pd.Series) -> float:
    """Trading sessions per year, measured over the last five full calendar years of the KSE-100."""
    y = idx.index.year
    full = [n for yr, n in pd.Series(1, index=y).groupby(y).size().items() if yr < idx.index[-1].year][-5:]
    return float(np.mean(full)) if full else 248.0
