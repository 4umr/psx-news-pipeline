"""Swing signal rules on the adjusted price database.

Everything here is point-in-time: a signal on day t uses only data up to the close of t,
so the earliest realistic entry is the next session's open. The same rules drive the
backtest and the daily scanner, so what is tested is exactly what is published.

Research result (2018-2026, see backtest.py): price patterns alone mostly do NOT beat
similar stocks after costs on PSX. Only the rules marked live=True are used by the scanner;
the rest stay in the backtest so the report shows what was tested and rejected.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# kind: long = possible opportunity, risk = warning (weak returns expected afterwards)
# h = holding period in sessions used to judge the rule; tiers = size groups it applies to
SIGNALS: dict[str, dict] = {
    "vol_breakout":  {"name": "Volume breakout", "kind": "long", "gap": 10, "h": 10,
                      "rule": "Close above the 3-month high on 3x normal trading value, near the day's high"},
    "high52":        {"name": "52-week high breakout", "kind": "long", "gap": 10, "h": 10,
                      "rule": "Close above the 52-week high on 2x normal trading value"},
    "quiet_accum":   {"name": "Quiet accumulation", "kind": "long", "gap": 20, "h": 10,
                      "rule": "Trading value doubles for 10 days while the price stays flat"},
    "upper_lock":    {"name": "Upper circuit close", "kind": "long", "gap": 5, "h": 10,
                      "rule": "Closed at the upper price limit"},
    "upper_lock2":   {"name": "Two upper circuits in a row", "kind": "long", "gap": 5, "h": 20,
                      "tiers": ["Mid", "Small", "Micro"],
                      "rule": "Closed at the upper price limit two sessions in a row (mid, small and micro caps)"},
    "gap_event":     {"name": "Gap up on heavy volume", "kind": "long", "gap": 10, "h": 10,
                      "rule": "Opened 3%+ higher and closed 5%+ higher on 4x normal trading value"},
    "pullback":      {"name": "Dip in an uptrend", "kind": "long", "gap": 10, "h": 10,
                      "rule": "In a long-term uptrend, down 6%+ in 3 sessions"},
    "rs_leader":     {"name": "Relative strength leader", "kind": "long", "gap": 20, "h": 20,
                      "rule": "Top 5% of liquid stocks by 3-month return, checked weekly"},
    "crash_bounce":  {"name": "Washout bounce", "kind": "long", "gap": 10, "h": 10,
                      "rule": "Down 20%+ in 5 sessions"},
    "pump_risk":     {"name": "Small cap up 50% in 2 weeks", "kind": "risk", "gap": 10, "h": 10,
                      "rule": "Small or micro cap up 50%+ in 10 sessions"},
    "lower_lock":    {"name": "Lower circuit close", "kind": "risk", "gap": 5, "h": 10, "live": True,
                      "rule": "Closed at the lower price limit"},
}

TIERS = [(30, "Large"), (100, "Mid"), (200, "Small"), (10**9, "Micro")]


def _tier(rank: float) -> str:
    if pd.isna(rank):
        return "n/a"
    for top, name in TIERS:
        if rank <= top:
            return name
    return "Micro"


def features(df: pd.DataFrame, meta: dict | None = None) -> pd.DataFrame:
    """df = prices.adjust(...) output. Adds indicators used by the rules."""
    df = df.sort_values(["sym", "date"]).reset_index(drop=True)
    g = df.groupby("sym", sort=False)

    def roll(col, n, fn="mean", shift=0, minp=None):
        s = g[col].shift(shift) if shift else df[col]
        r = s.groupby(df["sym"]).rolling(n, min_periods=minp or n)
        return getattr(r, fn)().reset_index(level=0, drop=True)

    df["adtv20"] = roll("val", 20, minp=15)
    df["vx"] = df["val"] / roll("val", 20, shift=1, minp=15)
    df["hh60"] = roll("ah", 60, "max", shift=1)
    df["hh250"] = roll("ah", 250, "max", shift=1, minp=230)
    df["sma50"] = roll("ac", 50)
    df["sma200"] = roll("ac", 200, minp=190)
    for n in (3, 5, 10, 60):
        df[f"r{n}"] = df["ac"] / g["ac"].shift(n) - 1
    df["acc"] = roll("val", 10) / roll("val", 50, shift=10, minp=40)
    rng = (df["h"] - df["l"]).replace(0, np.nan)
    df["pos"] = ((df["c"] - df["l"]) / rng).fillna(1.0)
    df["gap_open"] = df["o"] / (df["c"] / (1 + df["chg"])) - 1
    df["lock_up"] = (df["chg"] >= df["limit"] - 0.006) & (df["c"] >= df["h"])
    df["lock_dn"] = (df["chg"] <= -(df["limit"] - 0.006)) & (df["c"] <= df["l"])
    # Data guard: an adjusted one-day move beyond the price limit between consecutive sessions is a data error
    ar = g["ac"].pct_change()
    consec = g["si"].diff() == 1
    df["bad"] = consec & (ar.abs() > df["limit"] + 0.02)
    # size: market cap rank on each day (shares from PSX x adjusted price)
    if meta:
        shares = df["sym"].map({s: m.get("shares") for s, m in meta.items()})
        df["mcap"] = shares * df["ac"]
        df["rank"] = df.groupby("date")["mcap"].rank(ascending=False)
        df["tier"] = df["rank"].map(_tier)
        df["ff_pct"] = df["sym"].map({s: m.get("free_float_pct") for s, m in meta.items()})
    else:
        df["mcap"], df["rank"], df["tier"], df["ff_pct"] = np.nan, np.nan, "n/a", np.nan
    return df


def detect(df: pd.DataFrame, min_adtv: float) -> dict[str, pd.Series]:
    """Boolean masks per signal (before de-duplication)."""
    liquid = df["adtv20"] >= min_adtv
    up = df["chg"] > 0
    m = {
        "vol_breakout": (df["ac"] > df["hh60"]) & (df["vx"] >= 3) & (df["pos"] >= 0.7) & (df["chg"] >= 0.02),
        "high52": (df["ac"] > df["hh250"]) & (df["vx"] >= 2) & (df["pos"] >= 0.6) & up,
        "quiet_accum": (df["acc"] >= 2) & (df["r10"].abs() <= 0.04) & (df["ac"] > df["sma50"]),
        "upper_lock": df["lock_up"],
        "upper_lock2": df["lock_up"] & df.groupby("sym")["lock_up"].shift(1, fill_value=False),
        "gap_event": (df["gap_open"] >= 0.03) & (df["chg"] >= 0.05) & (df["vx"] >= 4),
        "pullback": (df["ac"] > df["sma200"]) & (df["sma50"] > df["sma200"]) & (df["r3"] <= -0.06),
        "crash_bounce": df["r5"] <= -0.20,
        "pump_risk": (df["r10"] >= 0.50) & df["tier"].isin(["Small", "Micro", "n/a"]),
        "lower_lock": df["lock_dn"],
    }
    # Relative strength: last session of each week, top 5% of liquid stocks by 60-session return
    wk = df["date"].dt.to_period("W")
    last_of_week = df["date"] == df.groupby(wk)["date"].transform("max")
    pct = df["r60"].where(liquid).groupby(df["date"]).rank(pct=True)
    m["rs_leader"] = last_of_week & (pct >= 0.95) & (df["ac"] > df["sma50"])
    out = {}
    for k, v in m.items():
        if tiers := SIGNALS[k].get("tiers"):
            v = v & df["tier"].isin(tiers)
        out[k] = v.fillna(False) & liquid
    return out


def debounce(df: pd.DataFrame, mask: pd.Series, gap: int) -> pd.Series:
    """Keep the first signal per stock; ignore repeats within `gap` sessions of the last kept one."""
    keep = pd.Series(False, index=df.index)
    last: dict[str, int] = {}
    for i in mask[mask].index:
        s, si = df.at[i, "sym"], df.at[i, "si"]
        if s not in last or si - last[s] >= gap:
            keep.at[i] = True
            last[s] = si
    return keep


def events(df: pd.DataFrame, min_adtv: float) -> pd.DataFrame:
    """All de-duplicated price-signal events: one row per (signal, stock, day), indexed like df."""
    out = []
    for name, mask in detect(df, min_adtv).items():
        k = debounce(df, mask, SIGNALS[name]["gap"])
        e = df.loc[k, ["date", "sym", "si", "c", "chg", "vx", "adtv20", "tier", "ff_pct", "r10"]].copy()
        e["signal"] = name
        out.append(e)
    return pd.concat(out) if out else pd.DataFrame()
