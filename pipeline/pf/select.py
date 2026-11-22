"""Equity model portfolio from the stock-picking styles that worked on PSX (tested 2010-2026, see
docs/portfolio-planning.md):

  Core growth     = earnings growth + 12-1 month momentum + low volatility   (20 stocks, every 3 months:
                    +5.7% a year over the liquid market, 14 of 17 years, both halves, volatility 21% vs 26%,
                    worst fall -32% vs -57%)
  Core defensive  = earnings growth + low volatility + dividend yield         (+2.4% a year, volatility 19%,
                    worst fall -27%: for conservative profiles and income goals)

Universe exactly as tested: the 120 stocks with the highest 60-day median value traded, price >= Rs 5, a year of
history (ETFs and funds excluded). Scores = average of within-universe rank z-scores. Picks: best score first,
at most 30% of the names in one sector, equal weights (as tested), whole shares (PSX one-share lots since 2024).
Prudence screens that were NOT part of the test (said on the report): no defaulters, no loss-makers over the
last 4 quarters, no negative book value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..common import log

TESTED = {
    "Core growth": {"cols": ["z_gro", "z_mom", "z_lowvol"], "excess": 0.057, "t": 1.81, "years": "14/17",
                    "vol": 0.213, "mdd": -0.32, "uvol": 0.264, "umdd": -0.57, "sharpe": 0.85, "usharpe": 0.47},
    "Core defensive": {"cols": ["z_gro", "z_lowvol", "z_dy"], "excess": 0.024, "t": 0.75, "years": "10/17",
                       "vol": 0.189, "mdd": -0.27, "uvol": 0.264, "umdd": -0.57, "sharpe": 0.79, "usharpe": 0.47},
}
_U: dict = {}


def style_for(prof: dict) -> str:
    return "Core defensive" if prof["level"] <= 2 or prof.get("goal") in ("income", "preserve") else "Core growth"


def _z(s: pd.Series) -> pd.Series:
    r = s.rank(pct=True)
    return (r - r.mean()) / r.std()


def _results(syms: list[str]) -> pd.DataFrame:
    """Last result vs a year earlier for each symbol (the saved results history if fresh enough, else download)."""
    import os
    import time

    from .. import earnings
    if earnings.HIST_PATH.exists() and time.time() - os.path.getmtime(earnings.HIST_PATH) < 5 * 86400:
        res = earnings.classify(pd.read_csv(earnings.HIST_PATH))
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as ex:
            parts = [c for c in ex.map(earnings.company, syms) if c is not None and not c.empty]
        res = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if res.empty:
        return res
    res["date"] = pd.to_datetime(res["date"], errors="coerce")
    res["last_yoy"] = res["yoy_cum"].where(res["kind"].isin(["FY", "CY"]), res["yoy_q"])
    return res.dropna(subset=["date"]).sort_values("date").groupby("sym").last()


def universe(n: int = 120) -> pd.DataFrame:
    """Scores for today's liquid universe (cached for the run)."""
    if "u" in _U:
        return _U["u"]
    from .. import prices
    from ..sources import scs
    path = prices.LIVE_PATH if prices.LIVE_PATH.exists() else prices.HIST_PATH
    raw = prices.load(path)
    raw = raw[raw["date"] >= raw["date"].max() - pd.Timedelta(days=560)]
    df = prices.adjust(raw)
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    AC = df.pivot(index="date", columns="sym", values="ac")
    VAL = df.pivot(index="date", columns="sym", values="val")
    EXF = df.pivot(index="date", columns="sym", values="exf").fillna(1.0)
    C = df.pivot(index="date", columns="sym", values="c")
    R = AC.pct_change(fill_method=None)
    t = AC.index[-1]
    adv = VAL.iloc[-60:].median()
    hist = AC.notna().sum()
    px = C.ffill().iloc[-1]
    ok = [s for s in adv.index if hist.get(s, 0) >= 260 and (px.get(s) or 0) >= 5 and
          prices.is_equity(s, (act.get(s) or {}).get("sector_name") or "")]
    elig = adv[ok].dropna().nlargest(n).index
    loc = len(AC) - 1
    mom = AC.iloc[loc - 21][elig] / AC.iloc[loc - 252][elig] - 1
    vol = R.iloc[-252:][elig].std()
    divf = EXF.iloc[-252:][elig].where((EXF.iloc[-252:][elig] >= 0.93) & (EXF.iloc[-252:][elig] < 0.997), 1.0)
    dy = 1 - np.exp(np.log(divf).sum())
    res = _results(list(elig))
    gro = pd.Series({s: (res.loc[s, "last_yoy"] if s in res.index and (t - res.loc[s, "date"]).days <= 200 else np.nan)
                     for s in elig})
    ttm = pd.Series({s: (res.loc[s, "ttm"] if s in res.index else np.nan) for s in elig})
    u = pd.DataFrame({"sym": elig, "price": px[elig].values, "adv": adv[elig].values, "mom": mom.values,
                      "vol": vol.values * np.sqrt(250), "dy": dy.values, "gro": gro.reindex(elig).values,
                      "ttm": ttm.reindex(elig).values}).set_index("sym")
    u["sector"] = [((act.get(s) or {}).get("sector_name") or "").strip().title() for s in u.index]
    u["name"] = [((act.get(s) or {}).get("company_name") or s).strip() for s in u.index]
    u["z_mom"], u["z_lowvol"], u["z_dy"], u["z_gro"] = _z(u["mom"]), -_z(u["vol"]), _z(u["dy"]), _z(u["gro"])
    for name, spec in TESTED.items():
        u[name] = u[spec["cols"]].mean(axis=1, skipna=True)
        u[name + " rank"] = u[name].rank(pct=True)
    u.attrs["date"] = t
    _U["u"] = u
    log.info("plan: universe of %d liquid stocks scored (prices of %s)", len(u), f"{t:%d %b %Y}")
    return u


def build(amount: float, style: str, shariah: bool, kmi: set[str], exclude: set[str] | None = None,
          n: int | None = None, keep: list[str] | None = None) -> dict:
    """The model equity portfolio for `amount` rupees. `keep` = stocks already owned: those scoring in the better
    half (and passing the screens) are kept first, so a switch needs fewer trades (less cost and tax)."""
    u = universe().copy()
    notes = []
    if shariah:
        if kmi:
            u = u[u.index.isin(kmi)]
        else:
            notes.append("KMI All Share list unavailable: Shariah screen could not be applied")
    bad = u[(u["name"].str.contains("defaulter", case=False)) | (u["ttm"] < 0)].index
    u = u.drop(bad.union(pd.Index(list(exclude or []))), errors="ignore")
    if n is None:
        n = 20 if amount >= 1_000_000 else 15 if amount >= 400_000 else 10
    per_sector = max(1, int(0.30 * n))
    picks, count = [], {}
    kept = [s for s in (keep or []) if s in u.index and u.loc[s, style + " rank"] >= 0.5]
    order = list(u.loc[kept].sort_values(style, ascending=False).index) + \
        [s for s in u.sort_values(style, ascending=False).index if s not in kept]
    for s in order:
        row = u.loc[s]
        if count.get(row["sector"], 0) >= per_sector:
            continue
        # one day at 20% of the usual value traded must cover the position
        if row["adv"] and amount / n > 0.2 * row["adv"]:
            continue
        picks.append(s)
        count[row["sector"]] = count.get(row["sector"], 0) + 1
        if len(picks) == n:
            break
    rows, spent = [], 0.0
    for s in picks:
        r = u.loc[s]
        qty = int((amount / len(picks)) // r["price"]) if len(picks) else 0
        if qty <= 0:
            continue
        rows.append({"sym": s, "name": r["name"], "sector": r["sector"], "price": float(r["price"]), "qty": qty,
                     "value": qty * float(r["price"]), "score": float(r[style]), "rank": float(r[style + " rank"]),
                     "gro": r["gro"], "mom": r["mom"], "vol": r["vol"], "dy": r["dy"]})
        spent += qty * float(r["price"])
    return {"rows": rows, "style": style, "spent": spent, "left": amount - spent, "n": n, "notes": notes,
            "excluded": list(bad), "kept": [x for x in picks if x in kept], "universe": len(u), "date": _U["u"].attrs.get("date"), "tested": TESTED[style]}
