"""Backtest of the swing rules on PSX history since 2018.

Method (kept strict so results are not flattering):
- Signal at the close of day t, entry at the NEXT session's open (what a reader of an
  evening post could actually do). If that session is locked at the upper limit all day,
  the trade is skipped (no shares would have been available).
- Exit at the close after the rule's holding period. Prices adjusted for dividends and bonuses.
- Costs per round trip by size group (brokerage, taxes, fees and the bid-ask spread).
- Compared with a baseline: buying ALL liquid stocks of the same size group on the same day
  and holding them for the same period. "Edge" = return after costs minus that baseline,
  which takes the 2024-25 bull market out of the result.
- Significance: t-statistic on daily averages (signals on the same day count as one
  observation), so one market-wide rally cannot make a rule look good.
- Universe: stocks listed today (delisted companies are missing, which flatters results a bit).
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from . import earnings, flows, prices, research
from .common import ROOT, log
from .signals import SIGNALS, events, features

OUT = ROOT / "out" / "backtest"
STATS_PATH = ROOT / "pipeline" / "swing_stats.json"      # committed: the result of the research
LIVE_STATS_PATH = ROOT / "state" / "swing_stats.json"    # refreshed by the weekly re-test in GitHub Actions
# Verdicts that keep a rule switched on. If a re-test moves a rule out of these, the scanner stops using it.
ACTIVE = {"Works", "Promising", "Reliable warning", "Weak warning"}
HORIZONS = (4, 5, 10, 20, 40, 60)
YEARS = [str(y) for y in range(2018, 2027)]
DEFAULT_COST = {"Large": 0.005, "Mid": 0.006, "Small": 0.009, "Micro": 0.013, "n/a": 0.013}
SPECS = {**SIGNALS, **earnings.EVENT_SIGNALS, **research.EXTRA_SIGNALS}
SPLIT = "2023-01-01"   # rules are designed on data before this date and must also work after it


def forward(df: pd.DataFrame, kse: pd.Series, min_adtv: float, cost: dict) -> pd.DataFrame:
    g = df.groupby("sym", sort=False)
    entry = g["ao"].shift(-1)
    n_o, n_h, n_l = g["o"].shift(-1), g["h"].shift(-1), g["l"].shift(-1)
    n_ref, n_lim = g["c"].shift(-1) / (1 + g["chg"].shift(-1)), g["limit"].shift(-1)
    df["no_fill"] = (n_o >= n_ref * (1 + n_lim - 0.006)) & (n_h <= n_l)
    bad_cum = g["bad"].cumsum()
    df["cost"] = df["tier"].map(cost).fillna(max(cost.values()))
    base_pool = df["adtv20"] >= min_adtv
    kse_now = df["date"].map(kse).values
    for h in HORIZONS:
        df[f"ret{h}"] = g["ac"].shift(-h) / entry - 1
        df[f"bad{h}"] = (bad_cum.groupby(df["sym"]).shift(-h) - bad_cum) > 0
        df[f"kse{h}"] = g["date"].shift(-h).map(kse).values / kse_now - 1
        ok = base_pool & df[f"ret{h}"].notna() & ~df[f"bad{h}"]
        # Baseline: average return of all liquid stocks of the same size group, same entry day
        base = df[ok].groupby(["date", "tier"])[f"ret{h}"].mean().rename(f"base{h}")
        df = df.join(base, on=["date", "tier"])
    return df


def _tstat(daily: pd.Series) -> float:
    if len(daily) < 5 or daily.std() == 0:
        return float("nan")
    return float(daily.mean() / (daily.std() / np.sqrt(len(daily))))


def trades(ev: pd.DataFrame) -> pd.DataFrame:
    """One row per usable trade at the rule's own holding period, with net return and edge."""
    out = []
    for name, spec in SPECS.items():
        h = spec["h"]
        e = ev[ev["signal"] == name]
        e = e[e[f"ret{h}"].notna() & ~e[f"bad{h}"] & ~e["no_fill"] & e[f"base{h}"].notna()].copy()
        e["h"] = h
        e["ret"] = e[f"ret{h}"]
        # Warnings are about holding vs not holding, no trade is made: no costs for them
        e["net"] = e["ret"] - (e["cost"] if spec["kind"] == "long" else 0.0)
        e["base"] = e[f"base{h}"]
        e["kse"] = e[f"kse{h}"]
        e["edge"] = e["net"] - e["base"]
        out.append(e[["signal", "date", "sym", "tier", "c", "chg", "h", "ret", "net", "base", "kse", "edge"]])
    return pd.concat(out, ignore_index=True)


def summarize(tr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, spec in SPECS.items():
        e = tr[tr["signal"] == name]
        daily = e.groupby("date")["edge"].mean()
        row = {"signal": name, "name": spec["name"], "kind": spec["kind"], "rule": spec["rule"], "h": spec["h"],
               "live": bool(spec.get("live")), "n": len(e), "stocks": e["sym"].nunique(),
               "win": (e["net"] > 0).mean(), "beat": (e["edge"] > 0).mean(), "avg": e["net"].mean(),
               "edge": e["edge"].mean(), "med_edge": e["edge"].median(), "t": _tstat(daily),
               "p10": e["net"].quantile(0.10), "kse": e["kse"].mean()}
        for tag, part in (("d", e[e["date"] < SPLIT]), ("t", e[e["date"] >= SPLIT])):
            row[f"n_{tag}"] = len(part)
            row[f"edge_{tag}"] = part["edge"].mean() if len(part) >= 10 else np.nan
            row[f"t_{tag}"] = _tstat(part.groupby("date")["edge"].mean()) if len(part) >= 10 else np.nan
        yr = e["date"].dt.year.astype(str)
        for y in YEARS:
            p = e[yr == y]
            row[f"y{y}"] = p["edge"].mean() if len(p) >= 8 else np.nan
            row[f"n{y}"] = len(p)
        rows.append(row)
    s = pd.DataFrame(rows)
    s["verdict"] = s.apply(verdict, axis=1)
    return s


def verdict(r: pd.Series) -> str:
    n, edge, t = r["n"], r["edge"], r["t"]
    yrs = [r[f"y{y}"] for y in YEARS if pd.notna(r[f"y{y}"])]
    pos = sum(1 for x in yrs if x > 0) / max(1, len(yrs))
    if pd.isna(edge) or n < 40:
        return "Too few cases"
    # must hold in BOTH halves: the design period and the later test period
    ed, et = r.get("edge_d"), r.get("edge_t")
    if r["kind"] == "risk":
        both = pd.notna(ed) and pd.notna(et) and ed < 0 and et < 0
        if edge <= -0.01 and t <= -2.5 and pos <= 0.35 and both:
            return "Reliable warning"
        return "Weak warning" if edge < 0 and t <= -1.5 and both else "Not a warning"
    both = pd.notna(ed) and pd.notna(et) and ed > 0 and et > 0
    if n >= 100 and edge >= 0.02 and t >= 2 and pos >= 0.75 and both:
        return "Works"
    if edge >= 0.0075 and t >= 1.5 and pos >= 0.6 and both:
        return "Promising"
    return "No edge"


def run(cfg: dict, refresh: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    sw = cfg.get("swing", {})
    min_adtv = float(sw.get("min_adtv", 10e6))
    cost = {**DEFAULT_COST, **(sw.get("costs") or {})}
    start = sw.get("history_start", "2018-01-01")
    if refresh and prices.HIST_PATH.exists():
        prices.HIST_PATH.unlink()
    raw = prices.build_history(start)
    syms = sorted(raw["sym"].unique())
    meta = prices.meta(syms)
    log.info("adjusting prices and computing indicators (%d rows)", len(raw))
    df = features(prices.adjust(raw), meta)
    kse = prices.index_history(datetime.fromisoformat(start))
    df = forward(df, kse, min_adtv, cost)
    res = earnings.classify(earnings.load_all(syms, refresh=refresh))
    _, sectors = prices.universe()
    try:
        fl = flows.history()
    except Exception as e:  # noqa: BLE001  flow rules are skipped, the rest still runs
        log.warning("sector flows unavailable: %s", e)
        fl = None
    ev = pd.concat([events(df, min_adtv), earnings.event_rows(res, df, min_adtv),
                    research.events(df, res, sectors, min_adtv, fl)])
    try:  # sector P/E history since 2018 for the public valuation card and the website
        research.save_sector_history(research.valuation(df, res), sectors, min_adtv)
    except Exception:  # noqa: BLE001
        log.exception("sector P/E history failed")
    cols = ["no_fill", "cost"] + [f"{p}{h}" for h in HORIZONS for p in ("ret", "bad", "kse", "base")]
    ev = ev.join(df[cols])
    tr = trades(ev)
    summary = summarize(tr)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "summary.csv", index=False)
    tr.sort_values(["signal", "date"]).to_csv(OUT / "trades.csv", index=False, date_format="%Y-%m-%d")
    save_stats(summary, df, len(syms))
    log.info("backtest done: %d trades, results in %s", len(tr), OUT)
    return summary, tr


def save_stats(summary: pd.DataFrame, df: pd.DataFrame, n_syms: int) -> None:
    """Track record per rule, shown next to live signals (committed with the code)."""
    out = {"period": f"{df['date'].min():%b %Y} to {df['date'].max():%b %Y}", "stocks": n_syms, "rules": {}}
    for _, r in summary.iterrows():
        out["rules"][r["signal"]] = {
            "n": int(r["n"]), "h": int(r["h"]), "win": round(float(r["win"]), 3), "beat": round(float(r["beat"]), 3),
            "avg": round(float(r["avg"]), 4), "edge": round(float(r["edge"]), 4),
            "med_edge": round(float(r["med_edge"]), 4), "t": round(float(r["t"]), 2) if pd.notna(r["t"]) else None,
            "p10": round(float(r["p10"]), 4), "verdict": r["verdict"],
            "edge_d": None if pd.isna(r["edge_d"]) else round(float(r["edge_d"]), 4),
            "edge_t": None if pd.isna(r["edge_t"]) else round(float(r["edge_t"]), 4)}
    out["tested"] = datetime.now().strftime("%Y-%m-%d")
    for path in (STATS_PATH, LIVE_STATS_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1), encoding="utf-8")


def load_stats() -> dict:
    """Latest track record: the weekly re-test if there is one, else the committed research result."""
    for path in (LIVE_STATS_PATH, STATS_PATH):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("rules"):
                return d
        except (OSError, json.JSONDecodeError):
            continue
    return {"rules": {}}


def verdict_changes(old: dict, new: dict) -> list[str]:
    """Plain-language list of live rules whose verdict changed between two re-tests."""
    out = []
    for name, spec in SPECS.items():
        if not spec.get("live"):
            continue
        a = (old.get("rules", {}).get(name) or {}).get("verdict")
        b = (new.get("rules", {}).get(name) or {}).get("verdict")
        if a and b and a != b:
            state = "switched OFF" if b not in ACTIVE else ("switched ON" if a not in ACTIVE else "still on")
            out.append(f"{spec['name']}: {a} -> {b} ({state})")
    return out
