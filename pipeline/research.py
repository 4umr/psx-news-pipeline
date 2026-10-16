"""Round-2 rules: earnings momentum, valuation and sector flows.

These are candidates. The backtest judges them on two separate periods: they are designed
on 2018-2022 and must ALSO work on 2023-2026, which they were never tuned on. Only rules that
pass both are switched on (live=True) in the scanner.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import earnings, flows

EXTRA_SIGNALS: dict[str, dict] = {
    "earn_streak":       {"name": "Second strong quarter in a row", "kind": "long", "h": 60,
                          "rule": "EPS up 50%+ on last year (or back to profit) for the second quarter running, stock not down on results day"},
    "earn_accel":        {"name": "Profit growth speeding up", "kind": "long", "h": 60, "live": True,
                          "rule": "Quarterly EPS growth at least 30 points faster than last quarter, and at least +20%"},
    "div_raise":         {"name": "Dividend raised with higher profit", "kind": "long", "h": 60,
                          "rule": "Dividend 1.5x the same period last year, with EPS up on last year"},
    "value_cheap":       {"name": "Cheapest fifth by P/E", "kind": "long", "h": 60, "gap": 20,
                          "rule": "Price to trailing 12-month earnings in the cheapest 20% of liquid stocks (checked weekly)"},
    "cheap_improving":   {"name": "Cheap and improving", "kind": "long", "h": 60, "gap": 20,
                          "rule": "Cheapest 30% by P/E and the last result showed EPS up 20%+ on last year (checked weekly)"},
    "dear_worsening":    {"name": "Expensive and worsening", "kind": "risk", "h": 60, "gap": 20,
                          "rule": "Most expensive 20% by P/E and the last result showed EPS down 20%+ (checked weekly)"},
    "flow_foreign_top":  {"name": "Sector foreigners buy most", "kind": "long", "h": 20, "gap": 20,
                          "rule": "Stocks in the sector with the strongest foreign net buying over 4 weeks"},
    "flow_foreign_low":  {"name": "Sector foreigners sell most", "kind": "risk", "h": 20, "gap": 20,
                          "rule": "Stocks in the sector with the strongest foreign net selling over 4 weeks"},
    "flow_funds_top":    {"name": "Sector mutual funds buy most", "kind": "long", "h": 20, "gap": 20,
                          "rule": "Stocks in the sector with the strongest mutual fund net buying over 4 weeks"},
    "flow_funds_low":    {"name": "Sector mutual funds sell most", "kind": "risk", "h": 20, "gap": 20,
                          "rule": "Stocks in the sector with the strongest mutual fund net selling over 4 weeks"},
}
COLS = ["date", "sym", "si", "c", "chg", "vx", "adtv20", "tier", "ff_pct", "r10"]


def valuation(df: pd.DataFrame, res: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time trailing P/E and last result growth for every row (a result counts from the next day)."""
    r = res[["sym", "date", "ttm", "yoy_q", "yoy_cum", "kind"]].copy()
    r["last_yoy"] = r["yoy_cum"].where(r["kind"].isin(["FY", "CY"]), r["yoy_q"])
    r["date"] = pd.to_datetime(r["date"])
    r = r.dropna(subset=["date"]).sort_values("date")
    left = df[["date", "sym"]].reset_index().sort_values("date")
    m = pd.merge_asof(left, r[["date", "sym", "ttm", "last_yoy"]], on="date", by="sym",
                      allow_exact_matches=False).set_index("index").sort_index()
    out = df.copy()
    out["ttm"], out["last_yoy"] = m["ttm"], m["last_yoy"]
    out["pe"] = (out["c"] / out["ttm"]).where(out["ttm"] > 0)
    # EPS figures are per share as announced; within two years of a bonus issue or split, the trailing
    # 12-month EPS mixes old and new share counts (seen with SYS after its 2025 split), so no P/E then
    big_action = (out["exf"] < 0.92).astype(float).groupby(out["sym"]).transform(lambda s: s.rolling(520, min_periods=1).max())
    out.loc[big_action > 0, "pe"] = np.nan
    return out


def _weekly(df: pd.DataFrame) -> pd.Series:
    wk = df["date"].dt.to_period("W")
    return df["date"] == df.groupby(wk)["date"].transform("max")


def _debounce(df, mask, gap):
    from .signals import debounce
    return debounce(df, mask.fillna(False), gap)


def events(df: pd.DataFrame, res: pd.DataFrame, sectors: dict[str, str], min_adtv: float,
           fl: pd.DataFrame | None = None) -> pd.DataFrame:
    """All round-2 events (one row per signal, stock, day), shaped like signals.events()."""
    liquid = df["adtv20"] >= min_adtv
    out = []

    def add(name, mask, gap=None):
        k = _debounce(df, mask & liquid, gap or EXTRA_SIGNALS[name].get("gap", 10))
        e = df.loc[k, COLS].copy()
        e["signal"] = name
        out.append(e)

    # --- earnings momentum (signal day = results reaction day, as in earnings.event_rows)
    ev = earnings.event_rows(res, df, min_adtv, rules={
        "earn_streak": res["cls"].isin(earnings.GOOD) & res["prev_cls"].isin(earnings.GOOD),
        "earn_accel": (res["yoy_q"] >= 0.2) & (res["prev_yoy_q"] >= 0) & (res["yoy_q"] - res["prev_yoy_q"] >= 0.3),
        "div_raise": (res["div"] >= 1.5 * res["div_ly"]) & (res["div_ly"] > 0) & (res["yoy_cum"] > 0),
    }, react_min={"earn_streak": 0.0})
    out.append(ev)
    # --- valuation (weekly)
    v = valuation(df, res)
    wk = _weekly(v) & liquid & v["pe"].notna()
    pct = v["pe"].where(wk).groupby(v["date"]).rank(pct=True)
    add("value_cheap", wk & (pct <= 0.2))
    add("cheap_improving", wk & (pct <= 0.3) & (v["last_yoy"] >= 0.2))
    add("dear_worsening", wk & (pct >= 0.8) & (v["last_yoy"] <= -0.2))
    # --- sector flows (weekly): strongest buying / selling sector over 4 weeks
    if fl is not None and len(fl):
        sec = df["sym"].map(lambda s: flows.sector_key(sectors.get(s, "")))
        wkp = df["date"].dt.to_period("W")
        last = _weekly(df)
        for who, top, low in (("foreign", "flow_foreign_top", "flow_foreign_low"),
                              ("funds", "flow_funds_top", "flow_funds_low")):
            it = flows.intensity(fl, who)
            it.index = it.index.to_period("W")
            best = it.idxmax(axis=1).rename("best")
            worst = it.idxmin(axis=1).rename("worst")
            b = wkp.map(best)
            w = wkp.map(worst)
            add(top, last & (sec == b))
            add(low, last & (sec == w))
    return pd.concat(out) if out else pd.DataFrame()


# ---------------------------------------------------------------- sector valuations (public cards, website)
def _sector_pe_path():
    from .common import ROOT
    return ROOT / "state" / "sector_pe.csv"


def sector_pe(v: pd.DataFrame, sectors: dict[str, str], min_adtv: float, weekly: bool = True) -> pd.DataFrame:
    """Median trailing P/E of liquid stocks per sector (at least 3 stocks with a usable P/E), by week."""
    d = v[(v["adtv20"] >= min_adtv) & v["pe"].notna() & (v["pe"] < 200)].copy()
    if weekly:
        d = d[_weekly(d)]
    d["sector"] = d["sym"].map(lambda s: (sectors.get(s) or "").strip().title())
    g = d.groupby(["date", "sector"])["pe"].agg(["median", "count"]).reset_index()
    return g[(g["count"] >= 3) & (g["sector"] != "")]


def save_sector_history(v: pd.DataFrame, sectors: dict[str, str], min_adtv: float) -> None:
    sector_pe(v, sectors, min_adtv).to_csv(_sector_pe_path(), index=False)


def load_sector_history() -> pd.DataFrame | None:
    p = _sector_pe_path()
    if not p.exists():
        return None
    try:
        h = pd.read_csv(p, parse_dates=["date"])
        return h if len(h) else None
    except Exception:  # noqa: BLE001
        return None


def sector_table(now_pe: pd.DataFrame, hist: pd.DataFrame | None) -> pd.DataFrame:
    """Per sector: P/E now, its usual (median) P/E since 2018, and how often it was cheaper than now."""
    rows = []
    for _, r in now_pe.iterrows():
        h = hist[hist["sector"] == r["sector"]]["median"] if hist is not None else pd.Series(dtype=float)
        rows.append({"sector": r["sector"], "pe": r["median"], "n": int(r["count"]),
                     "usual": float(h.median()) if len(h) >= 52 else np.nan,
                     "cheaper_share": float((h < r["median"]).mean()) if len(h) >= 52 else np.nan,
                     "since": f"{hist[hist['sector'] == r['sector']]['date'].min():%Y}" if len(h) >= 52 else ""})
    return pd.DataFrame(rows).sort_values("pe")


def _stock_pe_path():
    from .common import ROOT
    return ROOT / "state" / "stock_pe.csv"


def save_stock_history(v: pd.DataFrame, years: int = 5) -> None:
    """Each company's usual (median) trailing P/E over the last `years` years, from weekly samples."""
    d = v[v["pe"].notna() & (v["pe"] < 200)]
    d = d[_weekly(d) & (d["date"] >= d["date"].max() - pd.Timedelta(days=365 * years))]
    g = d.groupby("sym")["pe"].agg(["median", "count"]).reset_index()
    g[g["count"] >= 52].rename(columns={"median": "usual_pe", "count": "weeks"}).to_csv(_stock_pe_path(), index=False)


def load_stock_history() -> dict[str, float]:
    p = _stock_pe_path()
    try:
        h = pd.read_csv(p)
        return dict(zip(h["sym"], h["usual_pe"]))
    except Exception:  # noqa: BLE001  missing or damaged: the website just leaves the comparison out
        return {}
