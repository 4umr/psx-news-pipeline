"""Live forward test (paper trading) of the two model portfolios that tested best on PSX 2010-2026
(docs/portfolio-planning.md, pf/select.py):

  Core growth     earnings growth + 12-1 month momentum + low volatility, 20 stocks
  Core defensive  earnings growth + low volatility + dividend yield, 20 stocks

Exactly as tested: the 120 most-traded stocks, equal weight, rebuilt every 63 sessions (about 3 months), 0.4% cost
a side on what is traded; plus the live screens (no defaulters, no loss-makers) and at most 30% of names per sector.
Every session the value is updated from the price database's adjusted closes (dividends included). Benchmarks: the
KSE-100 (price index) and the equal-weight liquid universe (the tested comparison). A back-test can flatter; this
record cannot be changed after the fact, so after a few quarters it shows whether the edge holds live.

State: state/model_pf.json (swing cache + encrypted swing-data backup). Runs at the end of swing.scan.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .common import ROOT, esc, log

PATH = ROOT / "state" / "model_pf.json"
STYLES = ("Core growth", "Core defensive")
N = 20
REBAL = 63
COST = 0.004


def load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save(st: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")


def value(units: dict, px: dict) -> float:
    return float(sum(u * px[s] for s, u in units.items() if px.get(s) == px.get(s) and px.get(s)))


def rebalance(nav: float, picks: list[str], px: dict, old_units: dict | None) -> tuple[dict, float, float]:
    """Equal weights on `picks` at prices px. Returns (units, nav after cost, turnover). Cost = COST x traded value
    (buys and sells), so a full switch costs 0.8%."""
    old_units = old_units or {}
    w_old = {s: u * px[s] / nav for s, u in old_units.items() if px.get(s)} if nav else {}
    w_new = {s: 1.0 / len(picks) for s in picks}
    traded = sum(abs(w_new.get(s, 0) - w_old.get(s, 0)) for s in set(w_new) | set(w_old))
    if not old_units:
        traded = 1.0  # first purchase: buying everything
    nav2 = nav * (1 - COST * traded)
    units = {s: nav2 * w_new[s] / px[s] for s in picks}
    return units, nav2, traded / 2


def _picks(style: str) -> tuple[list[str], list[str]]:
    from .pf import select
    b = select.build(10_000_000, style, False, set(), n=N)
    return [r["sym"] for r in b["rows"]], list(select.universe().index)


def update(df: pd.DataFrame, sess: pd.Timestamp) -> tuple[dict, list[str]]:
    """Bring both model portfolios up to `sess` (df = prices.adjust frame of the live database).
    Returns (state, events) where events are 'start' / 'rebuilt' notes for the message."""
    st = load()
    AC = df.pivot_table(index="date", columns="sym", values="ac").sort_index().ffill()
    days = list(AC.index)
    from . import prices
    try:
        kse = prices.index_history(days[0].to_pydatetime()).reindex(AC.index).ffill()
    except Exception:  # noqa: BLE001
        kse = pd.Series(np.nan, index=AC.index)
    events = []
    for style in STYLES:
        p = st.get(style)
        if p is None:
            picks, uni = _picks(style)
            if len(picks) < 10:
                continue
            px = AC.loc[sess].to_dict()
            units, nav, _ = rebalance(1.0, picks, px, None)
            p = {"start": f"{sess:%Y-%m-%d}", "last_rebal": f"{sess:%Y-%m-%d}", "units": units, "nav": nav,
                 "entry": {s: px[s] for s in picks}, "uni": uni, "uni_nav": 1.0, "kse0": float(kse.loc[sess]),
                 "hist": [[f"{sess:%Y-%m-%d}", nav, 1.0, float(kse.loc[sess])]], "changes": {"in": picks, "out": []},
                 "rebuilds": 0}
            st[style] = p
            events.append(f"{style}: started {sess:%d %b %Y} with {len(picks)} stocks")
            continue
        last = pd.Timestamp(p["hist"][-1][0])
        for d in [x for x in days if last < x <= sess]:
            px = AC.loc[d].to_dict()
            prev = AC.loc[days[days.index(d) - 1]].to_dict()
            nav = value(p["units"], px)
            rets = [px[s] / prev[s] - 1 for s in p["uni"] if px.get(s) and prev.get(s)]
            p["uni_nav"] *= 1 + (float(np.mean(rets)) if rets else 0.0)
            p["nav"] = nav
            p["hist"].append([f"{d:%Y-%m-%d}", nav, p["uni_nav"], float(kse.loc[d]) if kse.loc[d] == kse.loc[d] else None])
            n_sess = sum(1 for x in days if pd.Timestamp(p["last_rebal"]) < x <= d)
            if n_sess >= REBAL and d == sess:
                picks, uni = _picks(style)
                if len(picks) >= 10:
                    old = set(p["units"])
                    p["units"], p["nav"], turn = rebalance(nav, picks, px, p["units"])
                    p["entry"] = {s: (p["entry"].get(s) if s in old else px[s]) for s in picks}
                    p["changes"] = {"in": sorted(set(picks) - old), "out": sorted(old - set(picks)), "turnover": turn}
                    p["uni"], p["last_rebal"] = uni, f"{d:%Y-%m-%d}"
                    p["rebuilds"] += 1
                    p["hist"][-1][1] = p["nav"]
                    events.append(f"{style}: rebuilt on {d:%d %b} ({len(p['changes']['in'])} in, "
                                  f"{len(p['changes']['out'])} out, cost {COST * 2 * turn * 100:.2f}%)")
        p["hist"] = p["hist"][-800:]
    save(st)
    return st, events


def _ret(a: float | None, b: float | None) -> float | None:
    return (a / b - 1) if a and b else None


def text(st: dict, sess: pd.Timestamp, events: list[str], AC: pd.DataFrame | None = None) -> str:
    from .pf.select import TESTED
    f = lambda x: "n/a" if x is None else f"{x * 100:+.1f}%"  # noqa: E731
    lines = [f"<b>Model portfolios: live paper test</b> · {sess:%a %d %b %Y}"]
    for style in STYLES:
        p = st.get(style)
        if not p:
            continue
        h = p["hist"]
        first, lastr = h[0], h[-1]
        wk = next((r for r in reversed(h) if pd.Timestamp(r[0]) <= sess - pd.Timedelta(days=7)), first)
        k0 = p.get("kse0") or first[3]
        lines += ["", f"<b>{style}</b> since {pd.Timestamp(p['start']):%d %b %Y} ({len(h) - 1} sessions)",
                  f"Portfolio {f(_ret(lastr[1], first[1] / (1 - COST)))} (after costs) · liquid market {f(_ret(lastr[2], first[2]))}"
                  f" · KSE-100 {f(_ret(lastr[3], k0))}",
                  f"This week: portfolio {f(_ret(lastr[1], wk[1]))}, liquid market {f(_ret(lastr[2], wk[2]))}, "
                  f"KSE-100 {f(_ret(lastr[3], wk[3]))}"]
        if AC is not None and sess in AC.index:
            px = AC.loc[sess]
            perf = sorted(((s, px.get(s) / e - 1) for s, e in p["entry"].items() if px.get(s) == px.get(s) and e),
                          key=lambda t: -t[1])
            lines.append("Holdings: " + ", ".join(f"{esc(s)} {(0 if abs(r) < 0.005 else r) * 100:+.0f}%" for s, r in perf))
        nxt = REBAL - sum(1 for r in h if r[0] > p["last_rebal"])
        lines.append(f"Next rebuild in about {max(0, nxt)} sessions (every {REBAL}, as tested)")
        ch = p.get("changes") or {}
        if p.get("rebuilds") and (ch.get("in") or ch.get("out")):
            lines.append(f"Last rebuild: in {esc(', '.join(ch.get('in', [])) or '-')}; out {esc(', '.join(ch.get('out', [])) or '-')}")
        t = TESTED[style]
        lines.append(f"<i>Tested 2010-2026: {t['excess'] * 100:+.1f}% a year over the liquid market, better in {t['years']} "
                     f"years. This live record is the real check.</i>")
    if events:
        lines += ["", "<b>Today</b>"] + [esc(e) for e in events]
    lines += ["", "<i>Paper portfolios, equal weight, dividends included, 0.4% cost a side. Private research, not "
                  "investment advice.</i>"]
    return "\n".join(lines)


def run(df: pd.DataFrame, sess: pd.Timestamp, sender, force: bool = False) -> None:
    st, events = update(df, sess)
    if not st:
        return
    if events or sess.dayofweek == 4 or force:
        AC = df.pivot_table(index="date", columns="sym", values="ac").sort_index().ffill()
        sender.send_admin(text(st, sess, events, AC))
    log.info("model portfolios: %s", "; ".join(f"{k} nav {v['nav']:.4f}" for k, v in st.items()))
