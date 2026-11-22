"""Strategic asset allocation for a Pakistani investor: PSX equities, gold, T-bills / money market.

Capital market assumptions (forward-looking building blocks, as asset managers publish them):
  T-bills / money market  today's SBP policy rate (yields follow it closely)
  PSX equities            10-year PIB yield + equity risk premium (config fair_value.erp, 5%): the same required
                          return as the fair value model
  Gold in rupees          midpoint of SBP's 5-7% medium-term inflation target + 1% long-run real return of gold
Volatility, correlation, fat tails and crisis behaviour come from the real 2008-2026 monthly history (KSE-100,
Comex gold x USD/PKR, policy rate), resampled with a stationary bootstrap and re-centred on the assumptions.

Allocation rules (docs/portfolio-planning.md): equity from the risk level, capped by the horizon (glide path, as
Betterment-style goal planners do), gold as the crash hedge when allowed, the rest in T-bills; then equity is cut
in 5-point steps until the 1-in-20 bad 12 months fits the client's stated loss limit.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..common import log

BASE_EQUITY = {1: 0.20, 2: 0.35, 3: 0.50, 4: 0.70, 5: 0.85}
GOLD = {1: 0.05, 2: 0.075, 3: 0.10, 4: 0.10, 5: 0.05}
INFLATION_TARGET = 0.06      # SBP medium-term target 5-7%
GOLD_REAL = 0.01


def horizon_cap(years: float) -> float:
    """Most equity a goal can hold given the years left (money needed sooner cannot wait out a crash)."""
    return 0.0 if years < 1 else 0.15 if years < 2 else 0.30 if years < 3 else 0.50 if years < 5 else 0.70 if years < 7 else 1.0


def history(now: datetime | None = None) -> pd.DataFrame:
    """Monthly returns since 2008: equity (KSE-100), gold in PKR, T-bills (policy rate in force)."""
    from .data import POLICY, clean_index, index_series
    now = now or datetime.now()
    kse = clean_index(index_series(now)).resample("ME").last()
    try:
        import yfinance as yf
        out = {}
        for k, t in (("usd", "PKR=X"), ("gold", "GC=F")):
            h = yf.Ticker(t).history(start="2007-12-01", end=(now + timedelta(days=1)).strftime("%Y-%m-%d"),
                                     auto_adjust=False)["Close"]
            h.index = pd.to_datetime(h.index).tz_localize(None)
            out[k] = h[h > 0].resample("ME").last()
        gold = (out["gold"] * out["usd"]).pct_change(fill_method=None)
    except Exception as e:  # noqa: BLE001
        log.warning("plan: gold prices unavailable (%s)", type(e).__name__)
        gold = pd.Series(dtype=float)
    pol = pd.Series({pd.Timestamp(d): v for d, v in POLICY}).sort_index()
    pol = pol.reindex(pol.index.union(kse.index)).ffill().reindex(kse.index)
    df = pd.DataFrame({"equity": kse.pct_change(fill_method=None), "gold": gold.reindex(kse.index),
                       "tbill": (1 + pol / 100) ** (1 / 12) - 1})
    return df[df.index >= "2008-01-31"].dropna(subset=["equity", "tbill"])


def assumptions(cfg: dict, sbp: dict | None, policy_now: float) -> dict:
    from .. import fairvalue
    rf10 = fairvalue.risk_free(sbp) or float((cfg.get("fair_value") or {}).get("risk_free", 12.0))
    erp = float((cfg.get("fair_value") or {}).get("erp", 5.0))
    return {"tbill": policy_now / 100, "equity": (rf10 + erp) / 100, "gold": INFLATION_TARGET + GOLD_REAL,
            "rf10": rf10, "erp": erp}


def _boot_months(X: np.ndarray, months: int, paths: int, block: float = 6.0, seed: int = 21) -> np.ndarray:
    """Stationary bootstrap of whole months (all assets together): paths x months x assets."""
    rng = np.random.default_rng(seed)
    T = len(X)
    starts = rng.integers(0, T, (paths, months))
    jump = rng.random((paths, months)) < 1.0 / block
    jump[:, 0] = True
    ix = np.empty((paths, months), dtype=np.int64)
    ix[:, 0] = starts[:, 0]
    for m in range(1, months):
        ix[:, m] = np.where(jump[:, m], starts[:, m], (ix[:, m - 1] + 1) % T)
    return X[ix]


def recentred(hist: pd.DataFrame, cma: dict) -> np.ndarray:
    """Monthly history with each asset's own average replaced by the assumption (shape kept, level forward-looking).
    Missing gold months (rare) are filled with the T-bill return."""
    h = hist.copy()
    h["gold"] = h["gold"].fillna(h["tbill"])
    X = np.column_stack([h[c].values for c in ("equity", "gold", "tbill")]).astype(float)
    target = np.array([(1 + cma["equity"]) ** (1 / 12) - 1, (1 + cma["gold"]) ** (1 / 12) - 1,
                       (1 + cma["tbill"]) ** (1 / 12) - 1])
    # geometric targets: add half the variance back so the compounded average matches the assumption
    X = X - X.mean(axis=0) + target + X.var(axis=0) / 2
    X[:, 2] = target[2]          # T-bills: no randomness in the yield over the plan (rates risk is small and short)
    return X


def one_year(w: np.ndarray, sims: np.ndarray) -> dict:
    """12-month outcome of weights w (equity, gold, tbill), rebalanced monthly."""
    r = (sims[:, :12, :] @ w)
    end = np.prod(1 + r, axis=1) - 1
    return {"p5": float(np.percentile(end, 5)), "p50": float(np.median(end)), "p95": float(np.percentile(end, 95)),
            "p_loss": float((end < 0).mean()), "mean": float(end.mean())}


def allocate(prof: dict, gold_ok: bool, sims: np.ndarray) -> dict:
    lvl, yrs = prof["level"], prof["years"]
    eq0 = min(BASE_EQUITY[lvl], horizon_cap(yrs))
    gold = GOLD[lvl] if gold_ok and yrs >= 1 else 0.0
    eq, steps = eq0, []
    while True:
        w = np.array([eq, gold, max(0.0, 1 - eq - gold)])
        o = one_year(w, sims)
        steps.append((eq, o["p5"]))
        if o["p5"] >= -prof["max_loss"] or eq <= 0:
            break
        eq = round(max(0.0, eq - 0.05), 4)
    if eq <= 0 and gold and one_year(np.array([0, gold, 1 - gold]), sims)["p5"] < -prof["max_loss"]:
        gold = 0.0
    w = {"equity": eq, "gold": gold, "tbill": max(0.0, 1 - eq - gold)}
    return {"w": w, "start_equity": eq0, "base": BASE_EQUITY[lvl], "cap": horizon_cap(yrs), "steps": steps,
            "cut_for_loss": eq < eq0 - 1e-9}


def project(w: dict, sims: np.ndarray, amount: float, monthly: float, years: float, tbill: float) -> dict:
    """Wealth paths over the horizon with monthly additions (made at each month end)."""
    M = max(1, int(round(years * 12)))
    X = sims[:, :M, :] if sims.shape[1] >= M else None
    if X is None:
        raise ValueError("simulation shorter than the horizon")
    r = X @ np.array([w["equity"], w["gold"], w["tbill"]])
    wealth = np.empty_like(r)
    v = np.full(r.shape[0], float(amount))
    for m in range(M):
        v = v * (1 + r[:, m]) + monthly
        wealth[:, m] = v
    paid = amount + monthly * M
    tb = amount * (1 + tbill) ** (M / 12) + sum(monthly * (1 + tbill) ** ((M - k - 1) / 12) for k in range(M))
    end = wealth[:, -1]
    return {"months": M, "paid": paid, "tbill_plan": tb, "pct": {p: float(np.percentile(end, p)) for p in (5, 25, 50, 75, 95)},
            "p_below_paid": float((end < paid).mean()), "p_beat_tbill": float((end > tb).mean()),
            "fan": {p: np.percentile(wealth, p, axis=0) for p in (5, 25, 50, 75, 95)}}


def compare(prof: dict, gold_ok: bool, sims: np.ndarray, cma: dict) -> list[dict]:
    """The five standard mixes at this horizon (what each would expect and risk), for the comparison table."""
    out = []
    for lvl in range(1, 6):
        eq = min(BASE_EQUITY[lvl], horizon_cap(prof["years"]))
        g = GOLD[lvl] if gold_ok and prof["years"] >= 1 else 0.0
        w = np.array([eq, g, 1 - eq - g])
        o = one_year(w, sims)
        exp = float(w @ np.array([cma["equity"], cma["gold"], cma["tbill"]]))
        out.append({"level": lvl, "equity": eq, "gold": g, "tbill": 1 - eq - g, "exp": exp, **o})
    return out


def crises(w: dict, hist: pd.DataFrame, eps: list[dict]) -> list[dict]:
    """The mix (bought at the top, held) in the KSE-100's falls, month by month, gold and T-bills included."""
    out = []
    h = hist.copy()
    h["gold"] = h["gold"].fillna(h["tbill"])
    for e in eps:
        a, b = e["peak"].to_period("M"), e["trough"].to_period("M")
        seg = h[(h.index.to_period("M") > a) & (h.index.to_period("M") <= b)]
        if seg.empty:
            continue
        g = {c: float((1 + seg[c]).prod() - 1) for c in ("equity", "gold", "tbill")}
        mix = w["equity"] * g["equity"] + w["gold"] * g["gold"] + w["tbill"] * g["tbill"]
        out.append({"name": e.get("name") or f"Fall of {e['peak']:%b %Y}", "peak": e["peak"], "trough": e["trough"],
                    "kse": g["equity"], "gold": g["gold"], "tbill": g["tbill"], "mix": mix})
    return out
