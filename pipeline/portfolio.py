"""Portfolio test: what following the live idea rules would have done with real, limited money.

Per-signal averages hide overlap, idle cash and losing streaks. This simulates a simple plan:
- capital split into N equal slots; each new idea (signal at a close) is bought at the next open
  if a slot is free, sized at 1/N of the portfolio's value at that moment
- the full round-trip cost of the stock's size group is paid at entry
- each position is held for its rule's period (or until an optional stop-loss at a close)
- idle cash earns nothing here (in reality it would earn T-bill interest, which only helps)
Benchmarks: KSE-100 (price index, dividends NOT included) and an equal-weight basket of all liquid
stocks (dividends included), both over the same days.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from . import backtest, earnings, prices, research
from .common import log
from .signals import features

PRIORITY = ["results_momentum", "earn_accel", "results_reaction"]   # if several ideas compete for one slot


def _ideas(cfg: dict, df: pd.DataFrame, min_adtv: float) -> pd.DataFrame:
    res = earnings.classify(earnings.load_all(sorted(df["sym"].unique())))
    _, sectors = prices.universe()
    ev = pd.concat([earnings.event_rows(res, df, min_adtv), research.events(df, res, sectors, min_adtv)])
    live = [k for k, v in backtest.SPECS.items() if v.get("live") and v["kind"] == "long"]
    ev = ev[ev["signal"].isin(live)].copy()
    ev["prio"] = ev["signal"].map({k: i for i, k in enumerate(PRIORITY)}).fillna(9)
    ev["h"] = ev["signal"].map(lambda k: backtest.SPECS[k]["h"])
    return ev.sort_values(["date", "prio"]).drop_duplicates(["date", "sym"])


def simulate(df: pd.DataFrame, ideas: pd.DataFrame, cost: dict, slots: int = 10, stop: float | None = None,
             start: str = "2018-01-01") -> dict:
    ao = df.pivot_table(index="date", columns="sym", values="ao")
    ac = df.pivot_table(index="date", columns="sym", values="ac")
    days = [d for d in ac.index if d >= pd.Timestamp(start)]
    by_day = {d: g for d, g in ideas.groupby("date")}
    prev_day = {d: p for p, d in zip(days, days[1:])}
    cash, pos, curve, log_ = 1.0, [], [], []
    for d in days:
        # 1. buy yesterday's ideas at today's open
        p = prev_day.get(d)
        if p is not None and p in by_day:
            equity = cash + sum(x["units"] * x["last"] for x in pos)
            held = {x["sym"] for x in pos}
            for _, r in by_day[p].iterrows():
                if len(pos) >= slots or r["sym"] in held:
                    continue
                o = ao.at[d, r["sym"]] if r["sym"] in ao.columns else np.nan
                before = ac.at[p, r["sym"]] if r["sym"] in ac.columns else np.nan
                size = min(equity / slots, cash)
                if not o or np.isnan(o) or size <= equity * 0.01:
                    continue
                if np.isnan(before) or not 0.6 <= o / before <= 1.6:  # impossible overnight jump: bad data, skip
                    continue
                c = cost.get(r["tier"], 0.013)
                pos.append({"sym": r["sym"], "rule": r["signal"], "units": size * (1 - c) / o, "entry": o,
                            "last": o, "held": 0, "h": int(r["h"]), "in": d, "paid": size})
                cash -= size
                held.add(r["sym"])
        # 2. mark at the close, then exits
        keep = []
        for x in pos:
            px = ac.at[d, x["sym"]]
            if not np.isnan(px):
                x["last"] = px
                x["held"] += 1
            out = x["held"] >= x["h"] or (stop is not None and x["last"] / x["entry"] - 1 <= -stop)
            if out:
                cash += x["units"] * x["last"]
                log_.append({"sym": x["sym"], "rule": x["rule"], "in": x["in"], "out": d,
                             "ret": x["units"] * x["last"] / x["paid"] - 1})
            else:
                keep.append(x)
        pos = keep
        curve.append((d, cash + sum(x["units"] * x["last"] for x in pos), len(pos)))
    eq = pd.DataFrame(curve, columns=["date", "equity", "n"]).set_index("date")
    return {"curve": eq, "trades": pd.DataFrame(log_), "slots": slots, "stop": stop}


def stats(curve: pd.Series) -> dict:
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    total = curve.iloc[-1] / curve.iloc[0] - 1
    dd = (curve / curve.cummax() - 1).min()
    return {"total": total, "cagr": (1 + total) ** (1 / yrs) - 1 if yrs > 0 else np.nan, "maxdd": dd}


def run(cfg: dict) -> dict:
    sw = cfg.get("swing", {})
    min_adtv = float(sw.get("min_adtv", 10e6))
    cost = {**backtest.DEFAULT_COST, **(sw.get("costs") or {})}
    raw = prices.build_history(sw.get("history_start", "2018-01-01"))
    df = features(prices.adjust(raw), prices.meta(sorted(raw["sym"].unique())))
    ideas = _ideas(cfg, df, min_adtv)
    log.info("portfolio test: %d ideas from the live rules", len(ideas))
    variants = {}
    for name, slots, stop in (("10 slots", 10, None), ("10 slots, stop -15%", 10, 0.15),
                              ("5 slots", 5, None), ("20 slots", 20, None)):
        variants[name] = simulate(df, ideas, cost, slots, stop)
    # benchmarks over the same days
    days = variants["10 slots"]["curve"].index
    kse = prices.index_history(datetime.fromisoformat(sw.get("history_start", "2018-01-01"))).reindex(days).ffill()
    rets = df.pivot_table(index="date", columns="sym", values="ac").pct_change(fill_method=None)
    liq = df.pivot_table(index="date", columns="sym", values="adtv20").shift(1) >= min_adtv
    ew = (1 + rets.where(liq).clip(-0.5, 0.5).mean(axis=1).reindex(days).fillna(0)).cumprod()
    out = {"variants": variants, "kse": kse / kse.iloc[0], "ew": ew / ew.iloc[0], "days": days}
    rows = []
    for name, v in variants.items():
        s = stats(v["curve"]["equity"])
        t = v["trades"]
        rows.append({"plan": name, **s, "trades": len(t), "win": (t["ret"] > 0).mean() if len(t) else np.nan,
                     "invested": (v["curve"]["n"] > 0).mean(), "avg_slots": v["curve"]["n"].mean() / v["slots"]})
    for name, c in (("KSE-100 (no dividends)", out["kse"]), ("All liquid stocks, equal weight", out["ew"])):
        rows.append({"plan": name, **stats(c.dropna())})
    out["table"] = pd.DataFrame(rows)
    yearly = {}
    for name, c in [("10 slots", variants["10 slots"]["curve"]["equity"]), ("KSE-100", out["kse"]), ("All liquid", out["ew"])]:
        c = c.dropna()
        yearly[name] = c.groupby(c.index.year).last() / c.groupby(c.index.year).first() - 1
    out["yearly"] = pd.DataFrame(yearly)
    return out
