"""The same stocks with other weights: the four standard risk-based portfolios used by institutional managers.
None of them needs a return forecast (forecasts are the weakest input in portfolio construction), so they show
what diversification alone could change. Covariance = Ledoit-Wolf shrinkage (metrics.shrink_cov).

  Equal weight                  1/N each
  Minimum variance              lowest predicted volatility (long only, cap per stock)
  Equal risk contribution       every stock adds the same share of risk (Maillard, Roncalli, Teiletche 2010),
                                solved with the convex form of Spinu (2013)
  Maximum diversification       highest diversification ratio (Choueifaty and Coignard 2008), long only, cap
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from . import metrics as mt


def cap_for(n: int) -> float:
    """Per-stock cap: 15% (the Pakistani equity-fund style limit), loosened when there are too few stocks."""
    return min(1.0, max(0.15, 2.0 / n))


def _solve(obj, n: int, cap: float, x0=None) -> np.ndarray:
    x0 = np.full(n, 1.0 / n) if x0 is None else x0
    res = minimize(obj, x0, method="SLSQP", bounds=[(0.0, cap)] * n,
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
                   options={"maxiter": 500, "ftol": 1e-12})
    w = np.clip(res.x if res.success else x0, 0, None)
    return w / w.sum()


def min_variance(cov: np.ndarray, cap: float) -> np.ndarray:
    scale = 1.0 / np.mean(np.diag(cov))
    return _solve(lambda w: w @ cov @ w * scale, len(cov), cap)


def max_div(cov: np.ndarray, cap: float) -> np.ndarray:
    sd = np.sqrt(np.diag(cov))
    return _solve(lambda w: -(w @ sd) / np.sqrt(w @ cov @ w), len(cov), cap)


def erc(cov: np.ndarray) -> np.ndarray:
    """Equal risk contribution: minimise 0.5 y'Cy - (1/n) sum ln y (y > 0), then w = y / sum(y)."""
    n = len(cov)
    C = cov / np.mean(np.diag(cov))
    res = minimize(lambda y: 0.5 * y @ C @ y - np.sum(np.log(y)) / n, np.full(n, 1.0 / np.sqrt(n)),
                   jac=lambda y: C @ y - 1.0 / (n * y), method="L-BFGS-B", bounds=[(1e-9, None)] * n)
    y = res.x
    return y / y.sum()


def alternatives(cov: np.ndarray) -> dict[str, np.ndarray]:
    n = len(cov)
    cap = cap_for(n)
    return {"Equal weight": np.full(n, 1.0 / n), "Minimum variance": min_variance(cov, cap),
            "Equal risk contribution": erc(cov), "Maximum diversification": max_div(cov, cap)}


def describe(w: np.ndarray, cov: np.ndarray, betas: np.ndarray) -> dict:
    return {"vol": float(np.sqrt(w @ cov @ w)), "beta": float(w @ betas), "dr": mt.div_ratio(w, cov),
            "enb": mt.enb(w, cov), "max_risk": float(mt.risk_contrib(w, cov)["pct"].max()), "top_w": float(w.max())}
