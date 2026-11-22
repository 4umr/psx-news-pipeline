"""Portfolio statistics: the standard definitions used by risk systems and fund factsheets.

Every function takes plain numbers (daily simple returns as pandas Series / numpy arrays, weights as arrays) and
returns plain numbers, so each one can be checked by hand (selftest.portfolio_tests). Conventions:
  - returns are simple daily returns; A = sessions per year (measured from the KSE-100 history, about 245-250)
  - annualised return = geometric (CAGR); annualised volatility = daily standard deviation x sqrt(A)
  - risk-free rate = SBP policy rate in force each day (pf.data.POLICY), turned into a daily rate
  - VaR and Expected Shortfall are reported as positive LOSSES (0.021 = a 2.1% loss)

Sources of the definitions:
  Sharpe ratio (Sharpe 1966, ex-post form Sharpe 1994); Sortino ratio with downside deviation against a minimum
  acceptable return (Sortino and Price 1994); Calmar ratio (Young 1991); Ulcer index and Martin ratio (Martin and
  McCann 1989); Jensen's alpha (1968), Treynor ratio (1965); tracking error and information ratio (Grinold and
  Kahn); up/down capture and batting average as Morningstar computes them (monthly, geometric); Omega ratio
  (Keating and Shadwick 2002); Cornish-Fisher modified VaR (Favre and Galeano 2002); EWMA volatility with
  lambda 0.94 (J.P. Morgan RiskMetrics 1996); Kupiec proportion-of-failures test (1995) and the Basel traffic
  light for VaR backtests (Basel Committee 1996); Euler risk contributions (Litterman 1996, Qian 2006);
  Ledoit-Wolf shrinkage to constant correlation (Ledoit and Wolf 2003, 'Honey, I shrunk the sample covariance
  matrix'); diversification ratio (Choueifaty and Coignard 2008); effective number of bets (Meucci 2009);
  Herfindahl-Hirschman concentration; active share (Cremers and Petajisto 2009); Blume beta adjustment
  (0.67 x raw + 0.33, Blume 1971, as on Bloomberg).
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

N01 = NormalDist()


# ---------------------------------------------------------------- returns and growth
def cagr(r, A: float) -> float | None:
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return None
    g = float(np.prod(1 + r))
    return g ** (A / len(r)) - 1 if g > 0 else -1.0


def total(r) -> float | None:
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    return float(np.prod(1 + r) - 1) if len(r) else None


def vol(r, A: float) -> float | None:
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    return float(np.std(r, ddof=1) * math.sqrt(A)) if len(r) > 2 else None


def wealth(r: pd.Series) -> pd.Series:
    return (1 + r.fillna(0)).cumprod()


def drawdown(r: pd.Series) -> pd.Series:
    w = wealth(r)
    return w / w.cummax() - 1


def max_drawdown(r: pd.Series) -> dict:
    """Deepest fall from a previous high: depth, peak / trough / recovery dates, sessions to recover."""
    if r is None or len(r) < 2:
        return {}
    w = wealth(r)
    dd = w / w.cummax() - 1
    t = dd.idxmin()
    depth = float(dd.loc[t])
    if depth >= 0:
        return {"depth": 0.0}
    peak = w.loc[:t].idxmax()
    after = w.loc[t:]
    rec = after[after >= w.loc[peak]]
    rec_d = rec.index[0] if len(rec) else None
    out = {"depth": depth, "peak": peak, "trough": t, "recovered": rec_d,
           "fall_sessions": int(w.index.get_loc(t) - w.index.get_loc(peak))}
    if rec_d is not None:
        out["recover_sessions"] = int(w.index.get_loc(rec_d) - w.index.get_loc(t))
    return out


def ulcer(r: pd.Series) -> float | None:
    """Ulcer index: root mean square of the percentage drawdowns (depth AND duration of falls)."""
    if r is None or len(r) < 2:
        return None
    return float(np.sqrt(np.mean(drawdown(r).values ** 2)))


# ---------------------------------------------------------------- risk-adjusted return
def sharpe(r, rf, A: float) -> float | None:
    """Annualised mean excess return / annualised standard deviation of excess return."""
    x = np.asarray(r, dtype=float) - np.asarray(rf, dtype=float)
    x = x[~np.isnan(x)]
    sd = np.std(x, ddof=1) if len(x) > 2 else 0
    return float(np.mean(x) / sd * math.sqrt(A)) if sd > 0 else None


def downside_dev(r, mar, A: float) -> float | None:
    x = np.asarray(r, dtype=float) - np.asarray(mar, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 3:
        return None
    return float(np.sqrt(np.mean(np.minimum(x, 0) ** 2)) * math.sqrt(A))


def sortino(r, mar, A: float) -> float | None:
    """Annualised mean return above the minimum acceptable return (here the risk-free rate) / downside deviation."""
    x = np.asarray(r, dtype=float) - np.asarray(mar, dtype=float)
    x = x[~np.isnan(x)]
    dd = downside_dev(r, mar, A)
    return float(np.mean(x) * A / dd) if dd else None


def calmar(r: pd.Series, A: float) -> float | None:
    c, m = cagr(r, A), max_drawdown(r).get("depth")
    return c / abs(m) if c is not None and m else None


def martin(r: pd.Series, rf_annual: float, A: float) -> float | None:
    c, u = cagr(r, A), ulcer(r)
    return (c - rf_annual) / u if c is not None and u else None


def omega(r, threshold) -> float | None:
    x = np.asarray(r, dtype=float) - np.asarray(threshold, dtype=float)
    x = x[~np.isnan(x)]
    lo = -x[x < 0].sum()
    return float(x[x > 0].sum() / lo) if lo > 0 else None


# ---------------------------------------------------------------- against the benchmark
def regress(rp, rm, rf=0.0) -> dict:
    """CAPM regression of excess returns: rp - rf = alpha + beta (rm - rf) + e. Daily alpha."""
    y = np.asarray(rp, dtype=float) - np.asarray(rf, dtype=float)
    x = np.asarray(rm, dtype=float) - np.asarray(rf, dtype=float)
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 20 or np.var(x) == 0:
        return {}
    b = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
    a = float(np.mean(y) - b * np.mean(x))
    e = y - a - b * x
    r2 = 1 - np.var(e) / np.var(y) if np.var(y) > 0 else None
    return {"beta": b, "alpha_d": a, "r2": float(r2) if r2 is not None else None, "resid_sd_d": float(np.std(e, ddof=2)),
            "corr": float(np.corrcoef(x, y)[0, 1]), "n": int(len(x))}


def blume(beta: float | None) -> float | None:
    return None if beta is None else 0.67 * beta + 0.33


def relative(rp: pd.Series, rm: pd.Series, rf: pd.Series, A: float) -> dict:
    """Beta, Jensen's alpha, Treynor, tracking error, information ratio, correlation, R squared."""
    df = pd.concat([rp, rm, rf], axis=1, keys=["p", "m", "f"]).dropna()
    if len(df) < 60:
        return {}
    reg = regress(df["p"], df["m"], df["f"])
    if not reg:
        return {}
    act = df["p"] - df["m"]
    te = float(act.std(ddof=1) * math.sqrt(A))
    ex = float((df["p"] - df["f"]).mean() * A)
    return {"beta": reg["beta"], "alpha": reg["alpha_d"] * A, "r2": reg["r2"], "corr": reg["corr"],
            "treynor": ex / reg["beta"] if reg["beta"] else None, "te": te,
            "ir": float(act.mean() * A / te) if te > 0 else None, "n": reg["n"]}


def monthly(r: pd.Series) -> pd.Series:
    """Compounded calendar-month returns (only months with at least 10 sessions)."""
    g = (1 + r.dropna()).groupby(r.dropna().index.to_period("M"))
    m = g.prod() - 1
    return m[g.size() >= 10]


def capture(rp: pd.Series, rm: pd.Series) -> dict:
    """Morningstar method on monthly returns: up capture = portfolio's geometric average return in the months the
    benchmark rose / the benchmark's geometric average in those months (x100); down capture the same for falling
    months; batting average = share of months the portfolio beat the benchmark."""
    mp, mm = monthly(rp), monthly(rm)
    df = pd.concat([mp, mm], axis=1, keys=["p", "m"]).dropna()
    if len(df) < 12:
        return {}

    def gavg(s):
        return float(np.prod(1 + s) ** (1 / len(s)) - 1) if len(s) else None

    up, dn = df[df["m"] > 0], df[df["m"] < 0]
    out = {"months": len(df), "batting": float((df["p"] > df["m"]).mean())}
    if len(up) and gavg(up["m"]):
        out["up"] = gavg(up["p"]) / gavg(up["m"]) * 100
    if len(dn) and gavg(dn["m"]):
        out["down"] = gavg(dn["p"]) / gavg(dn["m"]) * 100
    return out


def shape(r) -> dict:
    s = pd.Series(np.asarray(r, dtype=float)).dropna()
    if len(s) < 30:
        return {}
    return {"skew": float(s.skew()), "kurt": float(s.kurt())}   # sample skewness, EXCESS kurtosis


# ---------------------------------------------------------------- value at risk
def var_hist(r, c: float = 0.95) -> float | None:
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    return float(-np.quantile(r, 1 - c)) if len(r) >= 50 else None


def es_hist(r, c: float = 0.95) -> float | None:
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    v = var_hist(r, c)
    if v is None:
        return None
    tail = r[r <= -v]
    return float(-tail.mean()) if len(tail) else v


def var_normal(sd: float, c: float = 0.95) -> float:
    """Zero-mean normal VaR (the RiskMetrics convention for short horizons)."""
    return -N01.inv_cdf(1 - c) * sd


def es_normal(sd: float, c: float = 0.95) -> float:
    z = N01.inv_cdf(1 - c)
    return sd * math.exp(-z * z / 2) / math.sqrt(2 * math.pi) / (1 - c)


def var_cf(sd: float, skew: float, kurt: float, c: float = 0.95) -> float:
    """Cornish-Fisher (modified) VaR: the normal quantile corrected for skewness and excess kurtosis."""
    z = N01.inv_cdf(1 - c)
    zc = z + (z ** 2 - 1) * skew / 6 + (z ** 3 - 3 * z) * kurt / 24 - (2 * z ** 3 - 5 * z) * skew ** 2 / 36
    return -zc * sd


def ewma_sd(r, lam: float = 0.94) -> float | None:
    """RiskMetrics exponentially weighted daily volatility at the end of the series."""
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 30:
        return None
    v = float(np.var(r[:30], ddof=1))
    for x in r[30:]:
        v = lam * v + (1 - lam) * x * x
    return math.sqrt(v)


def kupiec(r, c: float = 0.99, window: int = 250, lookback: int = 250) -> dict:
    """Backtest of a rolling 1-day historical VaR: each day's VaR from the previous `lookback` sessions, compared
    with the next day's loss over the last `window` days. Kupiec POF likelihood ratio (chi-square, 1 df, 5% critical
    value 3.84) and, at 99%, the Basel traffic light (green 0-4 breaches in 250 days, yellow 5-9, red 10+)."""
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < window + lookback:
        return {}
    x = 0
    for i in range(len(r) - window, len(r)):
        v = -np.quantile(r[i - lookback:i], 1 - c)
        x += r[i] < -v
    p = 1 - c
    n = window
    ph = x / n
    if x == 0:
        lr = -2 * n * math.log(1 - p)
    elif x == n:
        lr = -2 * n * math.log(p)
    else:
        lr = -2 * ((n - x) * math.log(1 - p) + x * math.log(p) - (n - x) * math.log(1 - ph) - x * math.log(ph))
    out = {"breaches": int(x), "expected": p * n, "lr": lr, "ok": lr < 3.84}
    if abs(c - 0.99) < 1e-9 and n == 250:
        out["zone"] = "green" if x <= 4 else "yellow" if x <= 9 else "red"
    return out


# ---------------------------------------------------------------- covariance and risk decomposition
def shrink_cov(X: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf (2003) shrinkage of the sample covariance towards the constant-correlation matrix.
    X = T x N returns (no NaN). Returns (covariance, shrinkage intensity 0-1). Same scale as the input."""
    t, n = X.shape
    x = X - X.mean(axis=0)
    sample = x.T @ x / t
    var = np.diag(sample).copy()
    sd = np.sqrt(var)
    if n == 1:
        return sample, 0.0
    rbar = (np.sum(sample / np.outer(sd, sd)) - n) / (n * (n - 1))
    prior = rbar * np.outer(sd, sd)
    np.fill_diagonal(prior, var)
    y = x ** 2
    phi_mat = y.T @ y / t - 2 * (x.T @ x) * sample / t + sample ** 2
    phi = phi_mat.sum()
    term1 = (x ** 3).T @ x / t
    help_ = x.T @ x / t
    term2 = np.diag(help_)[:, None] * sample
    term3 = help_ * var[:, None]
    term4 = var[:, None] * sample
    theta = term1 - term2 - term3 + term4
    np.fill_diagonal(theta, 0)
    rho = np.trace(phi_mat) + rbar * np.sum(np.outer(1 / sd, sd) * theta)
    gamma = np.linalg.norm(sample - prior, "fro") ** 2
    kappa = (phi - rho) / gamma if gamma > 0 else 0.0
    delta = float(max(0.0, min(1.0, kappa / t)))
    return delta * prior + (1 - delta) * sample, delta


def risk_contrib(w: np.ndarray, cov: np.ndarray) -> dict:
    """Euler decomposition: sigma_p = sum_i w_i (cov w)_i / sigma_p. Contributions add up to sigma_p exactly."""
    w = np.asarray(w, dtype=float)
    sp = float(np.sqrt(w @ cov @ w))
    mcr = cov @ w / sp
    cr = w * mcr
    return {"sigma": sp, "mcr": mcr, "cr": cr, "pct": cr / sp}


def div_ratio(w: np.ndarray, cov: np.ndarray) -> float:
    """Weighted average of the stocks' own volatilities / portfolio volatility (1 = no diversification)."""
    w = np.asarray(w, dtype=float)
    return float(w @ np.sqrt(np.diag(cov)) / np.sqrt(w @ cov @ w))


def enb(w: np.ndarray, cov: np.ndarray) -> float:
    """Effective number of independent bets: exp(entropy) of the risk shares of the principal components."""
    lam, E = np.linalg.eigh(cov)
    v = E.T @ np.asarray(w, dtype=float)
    p = v ** 2 * np.maximum(lam, 0)
    p = p / p.sum()
    p = p[p > 1e-12]
    return float(math.exp(-np.sum(p * np.log(p))))


def hhi(w) -> float:
    w = np.asarray(w, dtype=float)
    w = w / w.sum()
    return float(np.sum(w ** 2))


def active_share(wp: dict, wb: dict) -> float:
    keys = set(wp) | set(wb)
    return 0.5 * sum(abs(wp.get(k, 0.0) - wb.get(k, 0.0)) for k in keys)


def liquidity_days(value: float, adv: float | None, part: float = 0.20) -> float | None:
    """Sessions needed to sell a position trading `part` (20%) of the stock's usual daily value."""
    return value / (part * adv) if adv and adv > 0 else None
