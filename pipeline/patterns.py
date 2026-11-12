"""Classic chart patterns and advanced price mathematics, point in time (no look-ahead).

TURNING POINTS (zigzag): a swing high / low is confirmed only when the price has moved `thr` away from it
  (thr = 2.5 x the 20-session daily volatility x sqrt 5, kept between 5% and 15%); the pattern logic sees a pivot only from its
  confirmation day, exactly as a chartist would.
PATTERNS (event = the day the pattern completes by breaking its line, as chartists trade them):
  double bottom   two lows within 3%, 10+ sessions apart, a peak 8%+ above between; close above that peak
  double top      mirror; close below the trough between the two highs                                (warning)
  inverse H&S     three lows, the middle 5%+ below both shoulders, shoulders within 6%; close above the neckline
  head and        mirror; close below the neckline                                                    (warning)
  shoulders
  ascending       two or more highs within 2.5% (flat top) and rising lows; close above the top
  triangle
  descending      flat bottom and falling highs; close below the bottom                               (warning)
  triangle
  bull flag       a pole of 20%+ in 15 sessions or fewer, then 4-15 sessions retracing at most half of it on
                  lighter value; close above the flag high
  cup and handle  a 15-50% deep rounded decline and recovery to within 5% of the old high over 30-150
                  sessions, a 3-12% handle; close above the rim
  RSI divergence  bullish: a lower price low with a higher RSI(14) low, RSI under 40 at the second low;
                  bearish: a higher high with a lower RSI high, RSI over 60                          (warning)
  squeeze         20-day Bollinger width at its lowest in 120 sessions, then a close above the upper band
  Fibonacci       in a stage-2 uptrend, a pullback that holds the 50-61.8% retracement of the last up-swing and
  bounce          turns up (pivot low confirmed there)
LEVELS AND MATHS (shown as facts): volume-at-price (the price where most value traded in 120 sessions =
  point of control, plus the value area holding 70%), Fibonacci levels of the last swing, linear-regression
  trend channel (slope a year, R squared), Hurst exponent (above 0.55 = trending, below 0.45 = mean reverting),
  annualised volatility, beta and correlation to the KSE-100, maximum drawdown over a year.
Each pattern is tested on PSX 2012-2026 (scratch pat_test.py) before it may count anywhere: TESTED below.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PAT_SIGNALS: dict[str, dict] = {
    "pt_double_bottom": {"name": "Double bottom (breakout)", "kind": "long", "h": 60, "gap": 20},
    "pt_inv_hs": {"name": "Inverse head and shoulders (breakout)", "kind": "long", "h": 60, "gap": 20},
    "pt_asc_triangle": {"name": "Ascending triangle (breakout)", "kind": "long", "h": 60, "gap": 20},
    "pt_bull_flag": {"name": "Bull flag (breakout)", "kind": "long", "h": 20, "gap": 15},
    "pt_cup_handle": {"name": "Cup and handle (breakout)", "kind": "long", "h": 60, "gap": 30},
    "pt_bull_div": {"name": "Bullish RSI divergence", "kind": "long", "h": 20, "gap": 15},
    "pt_squeeze_up": {"name": "Volatility squeeze breakout", "kind": "long", "h": 20, "gap": 15},
    "pt_fib_bounce": {"name": "Fibonacci 50-61.8% bounce in an uptrend", "kind": "long", "h": 20, "gap": 15},
    "pt_double_top": {"name": "Double top (breakdown)", "kind": "risk", "h": 60, "gap": 20},
    "pt_hs_top": {"name": "Head and shoulders (breakdown)", "kind": "risk", "h": 60, "gap": 20},
    "pt_desc_triangle": {"name": "Descending triangle (breakdown)", "kind": "risk", "h": 60, "gap": 20},
    "pt_bear_div": {"name": "Bearish RSI divergence", "kind": "risk", "h": 20, "gap": 15},
}
# 2012-2026 test (scratch pat_test.py, 13 Nov 2026): liquid stocks Rs 2m+/day, next open, vs same-size stocks on the
# same days, after costs, both halves. Bullish patterns on their own: no edge; bearish ones and some "bullish" ones are
# reliable WARNINGS; bullish patterns only help inside a stage-2 uptrend.
TESTED: dict[str, str] = {
    "pt_double_bottom": "tested: no edge on PSX (about 0% vs similar stocks)",
    "pt_inv_hs": "tested: +0.9% vs similar stocks over 3 months, weak",
    "pt_asc_triangle": "tested: no edge (-1.4% over 3 months)",
    "pt_bull_flag": "tested: inside a stage 2 uptrend +0.6% over a month (weak); outside it -1.8% (3 of 15 years positive)",
    "pt_cup_handle": "tested: no edge",
    "pt_bull_div": "tested: followed by -1.4% vs similar stocks over a month (2 of 15 years positive): a warning, not a buy",
    "pt_squeeze_up": "tested: inside stage 2 +1.3% over a month (9 of 14 years, promising); outside it -1.0% (warning)",
    "pt_fib_bounce": "tested: -1.3% over a month (2 of 15 years positive): the bounce usually fails",
    "pt_double_top": "tested: -1.0% over a month, weak warning",
    "pt_hs_top": "tested: -3.8% vs similar stocks over 3 months (2 of 12 years positive): a reliable warning",
    "pt_desc_triangle": "tested: -1.6% over 3 months, weak warning",
    "pt_bear_div": "tested: no edge",
}


# ---------------------------------------------------------------- building blocks
def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        au, ad = (au * (n - 1) + up[i]) / n, (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def zigzag(c: np.ndarray, h: np.ndarray, l: np.ndarray) -> list[tuple[int, int, str, float]]:
    """Confirmed swing points: (index of the extreme, index where it got confirmed, 'H'/'L', price)."""
    n = len(c)
    if n < 30:
        return []
    r = np.diff(np.log(np.maximum(c, 1e-9)), prepend=np.log(max(c[0], 1e-9)))
    vol = pd.Series(r).rolling(20, min_periods=10).std().bfill().values
    thr = np.clip(2.5 * vol * np.sqrt(5), 0.05, 0.15)
    piv = []
    mode = None  # looking for the next 'H' or 'L'
    ext_i = 0
    hi_i = lo_i = 0
    for i in range(1, n):
        if h[i] >= h[hi_i]:
            hi_i = i
        if l[i] <= l[lo_i]:
            lo_i = i
        if mode in (None, "H") and h[hi_i] > 0 and c[i] <= h[hi_i] * (1 - thr[i]) and hi_i >= ext_i:
            piv.append((hi_i, i, "H", float(h[hi_i])))
            mode, ext_i = "L", hi_i
            lo_i = hi_i + int(np.argmin(l[hi_i:i + 1]))
        elif mode in (None, "L") and l[lo_i] > 0 and c[i] >= l[lo_i] * (1 + thr[i]) and lo_i >= ext_i:
            piv.append((lo_i, i, "L", float(l[lo_i])))
            mode, ext_i = "H", lo_i
            hi_i = lo_i + int(np.argmax(h[lo_i:i + 1]))
    return piv


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b))


# ---------------------------------------------------------------- pattern finder on confirmed pivots
def find(c, h, l, v, piv, k: int, rs: np.ndarray) -> list[dict]:
    """Patterns whose last pivot is piv[k] (known from its confirmation day). Each: name, line (break level),
    direction, expiry (sessions to break), formed index."""
    out = []
    P = piv[: k + 1]
    last = P[-1]
    conf = last[1]

    def add(name, line, direction, window=40):
        out.append({"name": name, "line": line, "dir": direction, "from": conf, "until": conf + window, "formed": last[0]})

    if len(P) >= 3:
        a, b, cc = P[-3], P[-2], P[-1]
        # double bottom: L H L ; double top: H L H
        if a[2] == "L" and b[2] == "H" and cc[2] == "L" and _near(a[3], cc[3], 0.03) and cc[0] - a[0] >= 10 \
                and b[3] >= 1.08 * max(a[3], cc[3]):
            add("pt_double_bottom", b[3], 1)
        if a[2] == "H" and b[2] == "L" and cc[2] == "H" and _near(a[3], cc[3], 0.03) and cc[0] - a[0] >= 10 \
                and b[3] <= 0.92 * min(a[3], cc[3]):
            add("pt_double_top", b[3], -1)
        # RSI divergence between the last two lows / highs
        if a[2] == cc[2] and not np.isnan(rs[cc[0]]) and not np.isnan(rs[a[0]]):
            if cc[2] == "L" and cc[3] < a[3] and rs[cc[0]] > rs[a[0]] + 2 and rs[cc[0]] < 40:
                out.append({"name": "pt_bull_div", "line": None, "dir": 1, "from": conf, "until": conf, "formed": cc[0]})
            if cc[2] == "H" and cc[3] > a[3] and rs[cc[0]] < rs[a[0]] - 2 and rs[cc[0]] > 60:
                out.append({"name": "pt_bear_div", "line": None, "dir": -1, "from": conf, "until": conf, "formed": cc[0]})
    if len(P) >= 5:
        p1, t1, p2, t2, p3 = P[-5:]
        if [x[2] for x in (p1, t1, p2, t2, p3)] == ["H", "L", "H", "L", "H"]:
            if p2[3] >= 1.05 * max(p1[3], p3[3]) and _near(p1[3], p3[3], 0.06):
                add("pt_hs_top", (t1[3] + t2[3]) / 2, -1)
        if [x[2] for x in (p1, t1, p2, t2, p3)] == ["L", "H", "L", "H", "L"]:
            if p2[3] <= 0.95 * min(p1[3], p3[3]) and _near(p1[3], p3[3], 0.06):
                add("pt_inv_hs", (t1[3] + t2[3]) / 2, 1)
    if len(P) >= 4:
        highs = [x for x in P[-6:] if x[2] == "H"]
        lows = [x for x in P[-6:] if x[2] == "L"]
        if len(highs) >= 2 and len(lows) >= 2:
            top = max(x[3] for x in highs[-2:])
            if _near(highs[-1][3], highs[-2][3], 0.025) and lows[-1][3] > lows[-2][3] * 1.02 and last[2] == "L":
                add("pt_asc_triangle", top, 1)
            bot = min(x[3] for x in lows[-2:])
            if _near(lows[-1][3], lows[-2][3], 0.025) and highs[-1][3] < highs[-2][3] * 0.98 and last[2] == "H":
                add("pt_desc_triangle", bot, -1)
    # Fibonacci bounce: last pivot is a low that holds 50-61.8% of the previous up-swing (L -> H)
    if len(P) >= 3 and P[-1][2] == "L" and P[-2][2] == "H" and P[-3][2] == "L":
        lo0, hi0, lo1 = P[-3][3], P[-2][3], P[-1][3]
        if hi0 > lo0 * 1.15:
            retr = (hi0 - lo1) / (hi0 - lo0)
            if 0.45 <= retr <= 0.66:
                out.append({"name": "pt_fib_bounce", "line": None, "dir": 1, "from": conf, "until": conf, "formed": P[-1][0]})
    # cup and handle: H (rim) ... L (cup bottom 15-50% down) ... H (back within 5% of the rim) ... L (handle 3-12%)
    if len(P) >= 4 and [x[2] for x in P[-4:]] == ["H", "L", "H", "L"]:
        rim, bottom, rim2, handle = P[-4:]
        depth = 1 - bottom[3] / rim[3]
        span = rim2[0] - rim[0]
        if 0.15 <= depth <= 0.5 and 30 <= span <= 150 and rim2[3] >= 0.95 * rim[3] and 0.03 <= 1 - handle[3] / rim2[3] <= 0.12:
            add("pt_cup_handle", max(rim[3], rim2[3]), 1, window=30)
    return out


def events(c, h, l, v) -> list[tuple[int, str]]:
    """Every pattern completion for one stock: (index of the completion day, pattern name). Point in time."""
    c, h, l, v = (np.asarray(x, dtype=float) for x in (c, h, l, v))
    n = len(c)
    if n < 150:
        return []
    piv = zigzag(c, h, l)
    rs = rsi(c)
    by_conf: dict[int, list[int]] = {}
    for k, p in enumerate(piv):
        by_conf.setdefault(p[1], []).append(k)
    pending: list[dict] = []
    ev = []
    # squeeze (Bollinger width at a 120-session low, then a close above the upper band)
    s = pd.Series(c)
    ma, sd = s.rolling(20).mean(), s.rolling(20).std()
    width = (4 * sd / ma)
    low_w = width <= width.rolling(120, min_periods=100).min() * 1.0001
    squeezed = low_w.rolling(10, min_periods=1).max().shift(1).fillna(0).astype(bool).values
    upper = (ma + 2 * sd).values
    # bull flag: pole of 20%+ in <= 15 sessions, then 4-15 sessions holding over half of it on lighter value
    val = c * v
    for i in range(1, n):
        for k in by_conf.get(i, []):
            for p in find(c, h, l, v, piv, k, rs):
                if p["line"] is None:
                    ev.append((i, p["name"]))
                else:
                    pending.append(p)
        keep = []
        for p in pending:
            if i > p["until"]:
                continue
            if i > p["from"] and ((p["dir"] > 0 and c[i] > p["line"] and c[i - 1] <= p["line"])
                                  or (p["dir"] < 0 and c[i] < p["line"] and c[i - 1] >= p["line"])):
                ev.append((i, p["name"]))
                continue
            keep.append(p)
        pending = keep
        if squeezed[i] and not np.isnan(upper[i]) and c[i] > upper[i] and c[i - 1] <= upper[i - 1]:
            ev.append((i, "pt_squeeze_up"))
        if i >= 30:
            for flag_len in (4, 6, 8, 10, 12, 15):
                j = i - flag_len  # pole ends at j
                if j < 16:
                    break
                pole_lo = c[j - 15:j].min()
                if c[j] < pole_lo * 1.20:
                    continue
                fl = c[j + 1:i]
                if len(fl) == 0:
                    continue
                if fl.min() < c[j] - 0.5 * (c[j] - pole_lo) or val[j + 1:i].mean() > val[j - 15:j + 1].mean():
                    continue
                if c[i] > max(h[j:i].max(), c[j]) and c[i - 1] <= max(h[j:i - 1].max(), c[j]):
                    ev.append((i, "pt_bull_flag"))
                    break
    return ev


# ---------------------------------------------------------------- maths shown on the cards
def hurst(c: np.ndarray) -> float | None:
    """Rescaled-range Hurst exponent of daily log returns (250 sessions): > 0.55 trending, < 0.45 mean reverting."""
    r = np.diff(np.log(np.maximum(c[-251:], 1e-9)))
    if len(r) < 100:
        return None
    sizes, rs = [], []
    for m in (10, 20, 40, 80, 125):
        k = len(r) // m
        if k < 2:
            continue
        vals = []
        for j in range(k):
            x = r[j * m:(j + 1) * m]
            z = np.cumsum(x - x.mean())
            s = x.std()
            if s > 0:
                vals.append((z.max() - z.min()) / s)
        if vals:
            sizes.append(m)
            rs.append(np.mean(vals))
    if len(sizes) < 3:
        return None
    return float(np.polyfit(np.log(sizes), np.log(rs), 1)[0])


def volume_profile(c, v, n: int = 120, bins: int = 30) -> dict:
    c, v = np.asarray(c[-n:], dtype=float), np.asarray(v[-n:], dtype=float)
    if len(c) < 40 or c.max() <= c.min():
        return {}
    edges = np.linspace(c.min(), c.max(), bins + 1)
    idx = np.clip(np.digitize(c, edges) - 1, 0, bins - 1)
    val = np.bincount(idx, weights=c * v, minlength=bins)
    poc = int(val.argmax())
    order = np.argsort(val)[::-1]
    tot, acc, chosen = val.sum(), 0.0, []
    for b in order:
        chosen.append(b)
        acc += val[b]
        if acc >= 0.7 * tot:
            break
    mid = (edges[:-1] + edges[1:]) / 2
    return {"poc": float(mid[poc]), "va_low": float(edges[min(chosen)]), "va_high": float(edges[max(chosen) + 1]),
            "bins": mid.tolist(), "val": val.tolist()}


def regression_channel(c, n: int = 250) -> dict:
    y = np.log(np.asarray(c[-n:], dtype=float))
    if len(y) < 60:
        return {}
    x = np.arange(len(y))
    b, a = np.polyfit(x, y, 1)
    fit = a + b * x
    resid = y - fit
    r2 = 1 - resid.var() / y.var() if y.var() > 0 else 0.0
    sd = resid.std()
    return {"slope_year": float(np.exp(b * 250) - 1), "r2": float(r2), "mid": float(np.exp(fit[-1])),
            "upper": float(np.exp(fit[-1] + 2 * sd)), "lower": float(np.exp(fit[-1] - 2 * sd)),
            "z": float(resid[-1] / sd) if sd > 0 else 0.0}


def fib_levels(piv) -> dict:
    """Retracement levels of the last completed swing (L->H or H->L)."""
    if len(piv) < 2:
        return {}
    a, b = piv[-2], piv[-1]
    lo, hi = (a[3], b[3]) if a[2] == "L" else (b[3], a[3])
    up = a[2] == "L"
    lv = {f"{int(f * 1000) / 10:g}%": (hi - f * (hi - lo)) if up else (lo + f * (hi - lo)) for f in (0.236, 0.382, 0.5, 0.618, 0.786)}
    return {"from": a[3], "to": b[3], "up": up, "levels": lv}


def read(px: list[tuple], idx: dict[str, float] | None = None) -> dict:
    """Live read for one stock: patterns completed in the last 15 sessions, patterns still forming (with the
    line to watch), and the maths. px rows (date, open, close, volume, high, low)."""
    if len(px) < 150:
        return {}
    dates = [p[0] for p in px]
    c = np.array([p[2] for p in px], dtype=float)
    v = np.array([p[3] or 0 for p in px], dtype=float)
    h = np.array([(p[4] if len(p) > 4 and p[4] and p[4] > 0 else p[2]) for p in px], dtype=float)
    l = np.array([(p[5] if len(p) > 5 and p[5] and p[5] > 0 else p[2]) for p in px], dtype=float)
    n = len(c)
    ev = events(c, h, l, v)
    recent = [(dates[i], name) for i, name in ev if i >= n - 15]
    piv = zigzag(c, h, l)
    rs = rsi(c)
    forming = []
    if piv:
        for p in find(c, h, l, v, piv, len(piv) - 1, rs):
            if p["line"] is None:
                continue
            not_yet = (c[-1] <= p["line"]) if p["dir"] > 0 else (c[-1] >= p["line"])
            if n - 1 <= p["until"] and not_yet:
                forming.append({"name": p["name"], "line": p["line"], "dir": p["dir"]})
    out = {"recent": recent, "forming": forming, "pivots": [(dates[p[0]], p[2], p[3]) for p in piv[-6:]],
           "hurst": hurst(c), "profile": volume_profile(c, v), "channel": regression_channel(c),
           "fib": fib_levels(piv), "rsi": float(rs[-1]) if not np.isnan(rs[-1]) else None,
           "vol_year": float(np.std(np.diff(np.log(c[-251:]))) * np.sqrt(250) * 100) if n > 60 else None,
           "price": float(c[-1])}
    peak = np.maximum.accumulate(c[-250:])
    out["max_dd"] = float((c[-250:] / peak - 1).min() * 100)
    if idx:
        k = np.array([idx.get(d) for d in dates], dtype=float)
        ok = ~np.isnan(k)
        if ok.sum() > 120:
            rr = np.diff(np.log(c[ok]))[-250:]
            rk = np.diff(np.log(k[ok]))[-250:]
            if rk.std() > 0:
                out["beta"] = float(np.cov(rr, rk)[0, 1] / rk.var())
                out["corr"] = float(np.corrcoef(rr, rk)[0, 1])
    return out


NAMES = {k: v["name"] for k, v in PAT_SIGNALS.items()}


def lines(r: dict) -> list[str]:
    if not r:
        return []
    L = []
    f = lambda x: f"{x:,.2f}"  # noqa: E731
    for d, name in r.get("recent", [])[-3:]:
        try:
            from datetime import datetime as _dt
            day = f"{_dt.strptime(d[:10], '%Y-%m-%d'):%d %b}"
        except ValueError:
            day = d
        L.append(f"Completed {day}: {NAMES.get(name, name)}" + (f" ({TESTED[name]})" if name in TESTED else ""))
    for p in r.get("forming", [])[:2]:
        L.append(f"Forming: {NAMES.get(p['name'], p['name']).split(' (')[0]}; it completes on a close "
                 f"{'above' if p['dir'] > 0 else 'below'} Rs {f(p['line'])}")
    pr, ch = r.get("profile") or {}, r.get("channel") or {}
    if pr:
        L.append(f"Volume at price (120 sessions): most value traded at Rs {f(pr['poc'])}; 70% between Rs {f(pr['va_low'])} "
                 f"and {f(pr['va_high'])}" + (" (price above it: buyers there are in profit)" if r["price"] > pr["va_high"] else
                                              " (price below it: holders there are under water, possible supply)" if r["price"] < pr["va_low"] else ""))
    if ch:
        L.append(f"1-year trend channel: {ch['slope_year'] * 100:+.0f}% a year, fit R2 {ch['r2']:.2f}; price is "
                 f"{abs(ch['z']):.1f} standard deviations {'above' if ch['z'] > 0 else 'below'} the line "
                 f"(channel Rs {f(ch['lower'])} to {f(ch['upper'])})")
    fb = r.get("fib") or {}
    if fb:
        lv = fb["levels"]
        L.append(f"Fibonacci of the last {'up' if fb['up'] else 'down'}-swing ({f(fb['from'])} to {f(fb['to'])}): "
                 f"38.2% {f(lv['38.2%'])}, 50% {f(lv['50%'])}, 61.8% {f(lv['61.8%'])}")
    m = []
    if r.get("hurst") is not None:
        hh = r["hurst"]
        m.append(f"Hurst {hh:.2f} ({'trending' if hh > 0.55 else 'mean reverting' if hh < 0.45 else 'random walk'})")
    if r.get("beta") is not None:
        m.append(f"beta {r['beta']:.2f}, correlation {r['corr']:.2f} to the KSE-100")
    if r.get("vol_year") is not None:
        m.append(f"volatility {r['vol_year']:.0f}% a year, worst fall in a year {r['max_dd']:.0f}%")
    if r.get("rsi") is not None:
        m.append(f"RSI {r['rsi']:.0f}")
    if m:
        L.append("Maths: " + "; ".join(m))
    return L


def verdicts(r: dict, stage2: bool) -> list[tuple[int, str]]:
    """At most one vote, and only from the tested reliable warnings, for patterns completed in the last 15 sessions."""
    names = {n for _, n in (r or {}).get("recent", [])}
    bad = []
    if "pt_hs_top" in names:
        bad.append("head and shoulders top completed")
    if "pt_bull_div" in names:
        bad.append("bullish RSI divergence (on PSX it has been followed by underperformance)")
    if "pt_bull_flag" in names and not stage2:
        bad.append("bull flag outside a stage 2 uptrend")
    if "pt_squeeze_up" in names and not stage2:
        bad.append("volatility squeeze breakout outside a stage 2 uptrend")
    if not bad:
        return []
    return [(-1, "pattern warning: " + "; ".join(bad) + " (tested 2012-2026: each was followed by underperformance)")]
