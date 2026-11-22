"""Stress tests: how the portfolio did in the KSE-100's past bear markets, worst days and SBP rate cycles, how it
would move in defined shocks to the market, the rupee and oil (a factor model with correlated shocks, the
approach of MSCI Barra / RiskMetrics stress testing), and a Monte Carlo of the next 12 months.

Historical tests hold TODAY's weights from the start of each episode (buy and hold). A stock not yet listed then
is represented by its Blume-adjusted beta x the KSE-100's move; the share of the portfolio that had real prices
is shown as 'coverage'. Price-return series are used against the KSE-100 (a price index); Monte Carlo uses total
returns (dividends included), re-centred on the CAPM expected return so the past's lucky or unlucky average does
not decide the future: E[r] = risk-free + beta x equity risk premium (the same premium as the fair value model).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# names for the episodes the data finds (matched on the peak date); anything else is shown by its dates only
NAMES = [
    ("2007-12-01", "2008-12-31", "Global financial crisis (incl. the 2008 PSX price floor)"),
    ("2017-04-01", "2018-12-31", "2017-2020 bear market (deficits, rate hikes, IMF programme, then COVID-19)"),
    ("2019-12-01", "2020-03-31", "COVID-19 crash"),
    ("2021-05-01", "2022-12-31", "2021-2023 slide (political crisis, record rates, default fears)"),
    ("2025-12-01", "2026-06-30", "Early 2026 sell-off (Middle East war)"),
]
# well-known crises that sit INSIDE a longer fall (the index had not regained its old high), so the peak-to-trough
# search alone would not list them: highest close in the window -> lowest close after it
NAMED = [
    ("COVID-19 crash", "2020-01-01", "2020-04-30"),
    ("2021-2023 slide (political crisis, record rates, default fears)", "2021-05-01", "2023-07-31"),
]


def episodes(idx: pd.Series, threshold: float = 0.15) -> list[dict]:
    """Peak-to-trough falls of the index of at least `threshold`, each ending when the old peak is regained."""
    out = []
    peak_d, peak_v = idx.index[0], float(idx.iloc[0])
    tr_d, tr_v = peak_d, peak_v
    for d, v in idx.items():
        v = float(v)
        if v >= peak_v:
            if tr_v / peak_v - 1 <= -threshold:
                out.append({"peak": peak_d, "trough": tr_d, "recovered": d, "depth": tr_v / peak_v - 1})
            peak_d, peak_v, tr_d, tr_v = d, v, d, v
        elif v < tr_v:
            tr_d, tr_v = d, v
    if tr_v / peak_v - 1 <= -threshold:
        out.append({"peak": peak_d, "trough": tr_d, "recovered": None, "depth": tr_v / peak_v - 1})
    for e in out:
        e["name"] = next((n for a, b, n in NAMES if pd.Timestamp(a) <= e["peak"] <= pd.Timestamp(b)), None)
    for name, a, b in NAMED:
        seg = idx.loc[a:b]
        if len(seg) < 20 or any(e.get("name") == name for e in out):
            continue
        pk = seg.idxmax()
        tr = seg.loc[pk:].idxmin()
        depth = float(seg.loc[tr] / seg.loc[pk] - 1)
        if depth <= -threshold:
            out.append({"peak": pk, "trough": tr, "recovered": None, "depth": depth, "name": name, "inside": True})
    return sorted(out, key=lambda e: e["peak"])


def _at(s: pd.Series, d) -> float | None:
    s = s.loc[:d].dropna()
    return float(s.iloc[-1]) if len(s) else None


def window(px: dict, w: dict, betas: dict, idx: pd.Series, a, b, cash_w: float = 0.0, rf_total: float = 0.0) -> dict:
    """Buy and hold today's weights from date a to date b. Returns the portfolio and index return, each stock's
    return (real or proxied), coverage, and the portfolio's worst point inside the window."""
    ia, ib = _at(idx, a), _at(idx, b)
    if not ia or not ib:
        return {}
    ir = ib / ia - 1
    path_idx = idx.loc[a:b] / ia
    stocks, cov, path = {}, 0.0, cash_w * pd.Series(1.0, index=path_idx.index)
    for s, wi in w.items():
        ser = px.get(s)
        first = ser.dropna().index.min() if ser is not None and len(ser.dropna()) else None
        if first is not None and first <= pd.Timestamp(a) and _at(ser, a):
            p0 = _at(ser, a)
            seg = ser.loc[a:b].reindex(path_idx.index).ffill().fillna(p0) / p0
            stocks[s] = {"ret": float(seg.iloc[-1] - 1), "real": True}
            cov += wi
        else:
            bta = betas.get(s) or 1.0
            seg = 1 + bta * (path_idx - 1)
            stocks[s] = {"ret": float(seg.iloc[-1] - 1), "real": False}
        path = path + wi * seg
    ret = float(path.iloc[-1] - 1) + cash_w * rf_total
    return {"ret": ret, "idx": ir, "worst": float(path.min() - 1), "stocks": stocks,
            "coverage": cov / max(1e-9, sum(w.values()))}


def worst_days(idx: pd.Series, px: dict, w: dict, betas: dict, n: int = 8) -> list[dict]:
    ir = idx.pct_change().dropna()
    out = []
    for d in ir.nsmallest(n).index:
        ret, cov = 0.0, 0.0
        for s, wi in w.items():
            ser = px.get(s)
            if ser is not None and d in ser.index and ser.index.get_loc(d) > 0:
                prev = ser.loc[:d].iloc[-2]
                ret += wi * (float(ser.loc[d]) / float(prev) - 1)
                cov += wi
            else:
                ret += wi * (betas.get(s) or 1.0) * float(ir.loc[d])
        out.append({"date": d, "idx": float(ir.loc[d]), "ret": ret, "coverage": cov / max(1e-9, sum(w.values()))})
    return out


# ---------------------------------------------------------------- factor scenarios
SCENARIOS = [
    ("Market correction", {"mkt": -0.15}, "KSE-100 falls 15%"),
    ("Bear market", {"mkt": -0.30}, "KSE-100 falls 30% (2017-19 and 2021-23 were 30-40%)"),
    ("Crash like 2008", {"mkt": -0.50}, "KSE-100 falls 50% (2008: about 69% from the top)"),
    ("Rupee crisis", {"usd": 0.25}, "rupee loses 20% against the dollar (USD/PKR +25%)"),
    ("Oil spike", {"oil": 0.50}, "Brent crude +50% (as in 2022)"),
    ("Oil collapse", {"oil": -0.40}, "Brent crude -40% (as in 2020)"),
    ("Balance-of-payments crisis", {"mkt": -0.25, "usd": 0.30, "oil": 0.20},
     "KSE-100 -25%, rupee -23% (USD/PKR +30%), oil +20% (like 2022-23)"),
    ("Strong rally", {"mkt": 0.25}, "KSE-100 rises 25% (the upside test)"),
]


def factor_model(px: dict, idx: pd.Series, fac: pd.DataFrame | None, months: int = 60) -> dict:
    """Monthly OLS per stock on KSE-100 return, USD/PKR change and Brent change over the last `months` months.
    Returns {'B': {sym: {mkt, usd, oil, resid}}, 'cov': factor covariance, 'keys', 'n'}."""
    m_idx = idx.resample("ME").last().pct_change(fill_method=None)
    F = pd.DataFrame({"mkt": m_idx})
    keys = ["mkt"]
    if fac is not None and len(fac) > 24:
        F["usd"] = fac["usdpkr"].pct_change(fill_method=None)
        F["oil"] = fac["brent"].pct_change(fill_method=None)
        keys = ["mkt", "usd", "oil"]
    F = F.dropna().iloc[-months:]
    B = {}
    for s, ser in px.items():
        r = ser.resample("ME").last().pct_change(fill_method=None)
        df = pd.concat([r.rename("r"), F], axis=1).dropna()
        if len(df) < 24:
            continue
        X = np.column_stack([np.ones(len(df))] + [df[k].values for k in keys])
        coef, *_ = np.linalg.lstsq(X, df["r"].values, rcond=None)
        e = df["r"].values - X @ coef
        B[s] = {**{k: float(c) for k, c in zip(keys, coef[1:])}, "resid": float(np.std(e, ddof=len(keys) + 1)),
                "n": len(df)}
    return {"B": B, "cov": F[keys].cov().values, "keys": keys, "n": len(F), "start": F.index.min() if len(F) else None}


def scenario(fm: dict, w: dict, shocks: dict, betas: dict) -> dict | None:
    keys = fm["keys"]
    if any(k not in keys for k in shocks):
        return None
    S = [keys.index(k) for k in shocks]
    U = [i for i in range(len(keys)) if i not in S]
    f = np.zeros(len(keys))
    f[S] = [shocks[keys[i]] for i in S]
    if U:  # the other factors move as they usually do with the shocked ones (conditional expectation)
        C = fm["cov"]
        f[U] = C[np.ix_(U, S)] @ np.linalg.solve(C[np.ix_(S, S)], f[S])
    impacts, var_e, cov_w = {}, 0.0, 0.0
    for s, wi in w.items():
        b = fm["B"].get(s)
        if b:
            impacts[s] = float(sum(b[k] * f[i] for i, k in enumerate(keys)))
            var_e += (wi * b["resid"]) ** 2
            cov_w += wi
        else:  # too little history for the regression: beta on the market only
            impacts[s] = float((betas.get(s) or 1.0) * f[0])
    ret = float(sum(w[s] * impacts[s] for s in w))
    band = 1.645 * float(np.sqrt(var_e))
    return {"ret": ret, "band": band, "stocks": impacts, "factors": dict(zip(keys, f.tolist())),
            "coverage": cov_w / max(1e-9, sum(w.values()))}


# ---------------------------------------------------------------- Monte Carlo
def bootstrap(r: np.ndarray, horizon: int, paths: int = 10000, block: float = 20.0, seed: int = 7) -> np.ndarray:
    """Stationary bootstrap (Politis and Romano 1994): blocks of random length (mean `block` sessions) drawn from
    the history keep fat tails and volatility clusters. Returns wealth paths (paths x horizon), starting at 1."""
    rng = np.random.default_rng(seed)
    T = len(r)
    starts = rng.integers(0, T, (paths, horizon))
    jump = rng.random((paths, horizon)) < 1.0 / block
    jump[:, 0] = True
    ix = np.empty((paths, horizon), dtype=np.int64)
    ix[:, 0] = starts[:, 0]
    for h in range(1, horizon):
        ix[:, h] = np.where(jump[:, h], starts[:, h], (ix[:, h - 1] + 1) % T)
    return np.cumprod(1 + r[ix], axis=1)


def summarise(paths: np.ndarray) -> dict:
    end = paths[:, -1] - 1
    run_max = np.maximum.accumulate(np.concatenate([np.ones((len(paths), 1)), paths], axis=1), axis=1)[:, 1:]
    mdd = (paths / run_max - 1).min(axis=1)
    pct = {p: float(np.percentile(end, p)) for p in (1, 5, 10, 25, 50, 75, 90, 95, 99)}
    return {"pct": pct, "mean": float(end.mean()),
            "p_loss": float((end < 0).mean()), "p_loss10": float((end < -0.10).mean()),
            "p_loss20": float((end < -0.20).mean()), "p_loss30": float((end < -0.30).mean()),
            "mdd_med": float(np.median(mdd)), "mdd_95": float(np.percentile(mdd, 5)),
            "fan": {p: np.percentile(paths, p, axis=0) for p in (5, 25, 50, 75, 95)}}


def monte_carlo(r_port: pd.Series, A: float, exp_annual: float, crisis_mask: pd.Series | None,
                horizon: int | None = None) -> dict:
    """Normal times: history re-centred on the CAPM expected return. Crisis times: blocks drawn only from the
    KSE-100's bear-market days (their own, negative, average kept)."""
    r = r_port.dropna()
    H = int(round(horizon or A))
    out = {}
    if len(r) >= 250:
        x = r.values - r.values.mean() + exp_annual / A
        out["normal"] = summarise(bootstrap(x, H))
        out["normal"]["n"] = len(r)
    if crisis_mask is not None:
        c = r[crisis_mask.reindex(r.index).fillna(False).astype(bool)]
        if len(c) >= 120:
            out["crisis"] = summarise(bootstrap(c.values, H, block=10.0, seed=11))
            out["crisis"]["n"] = len(c)
    out["horizon"] = H
    out["exp"] = exp_annual
    return out


def crisis_days(idx: pd.Series, eps: list[dict]) -> pd.Series:
    m = pd.Series(False, index=idx.index)
    for e in eps:
        m.loc[e["peak"]:e["trough"]] = True
    return m

