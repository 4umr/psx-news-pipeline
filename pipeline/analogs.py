"""Decision support: similar past cases, the market regime, and a position size (Research Lab + Stock of the Day).

SIMILAR PAST CASES  every liquid stock, once a week, 2012-2026, described by six things a reader can check:
  latest result (good / weak / neither, within 90 days), chart (stage 2 / stage 4 / other), relative strength
  (top 30% / middle / bottom half), price to book (<= 1.2 / 1.2-3 / > 3 / unknown), size (large / mid / small)
  and the market regime. What happened over the next 60 sessions versus same-size liquid stocks is stored per
  profile in pipeline/analog_stats.json (built by scratch analog_test.py). A stock today is matched on all six; if
  the profile has fewer than 150 past cases, 30 stocks or 5 years, the least important feature is dropped, one at a time (regime is
  kept longest after results, chart and RS). The card says which features matched and how many cases.
  Overlapping weeks of the same stock are not independent, so the "stocks" count is shown too.
MARKET REGIME  up = KSE-100 above its 200-day average and the average rising over a month; down = below and falling;
  otherwise mixed. Breadth = share of liquid stocks above their own 200-day average.
POSITION SIZE  a risk rule, not an edge: risk about 1% of the portfolio per idea, so size = 1% / (distance from the
  price to the fails-below line), between 2% and 10% of the portfolio, adjusted by the tested sizing result
  (SIZING below, from the 2013-2026 portfolio test).
"""
from __future__ import annotations

import json

import numpy as np

from .common import ROOT, log

STATS_PATH = ROOT / "pipeline" / "analog_stats.json"
MIN_CASES = 150
MIN_STOCKS = 30
MIN_YEARS = 5
RISK_PER_IDEA = 0.01
_STATS: dict | None = None
# 2013-2026 portfolio test (10 slots, the live results rules): equal size 18.2% a year, worst fall -35.7%, Sharpe 1.00;
# volatility-scaled 18.0% / -35.7% / 1.03; half size in a down market 16.8% / -35.7% / 1.03; both 17.4% / -35.7% / 1.10.
# Neither adjustment changed the worst fall (market crashes drive it), so the size is a plain risk rule.
SIZING: dict = {"down_factor": 1.0, "vol_scaled": False,
                "note": "shrinking positions in weak markets or by volatility did not reduce the worst fall in the 2013-2026 test"}
# Regime evidence (the tested results rule, 60 sessions vs similar stocks): market up-trend +5.1% (256 cases), mixed
# +1.1% (64), down-trend +2.7% (54); breadth 50%+ +5.1%, below 50% +1.8%. Read from analog_stats.json -> regime.


def _stats() -> dict:
    global _STATS
    if _STATS is None:
        try:
            _STATS = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("analog stats not available")
            _STATS = {"levels": []}
    return _STATS


def regime(idx: dict[str, float]) -> dict:
    """Market regime from the KSE-100 closes {date: close}."""
    ks = [idx[k] for k in sorted(idx)]
    if len(ks) < 221:
        return {"name": "mixed", "text": "not enough index history"}
    k = np.array(ks, dtype=float)
    sma = np.convolve(k, np.ones(200) / 200, mode="valid")
    now, avg, avg_then = k[-1], sma[-1], sma[-21]
    name = "up" if now > avg and avg > avg_then else ("down" if now < avg and avg < avg_then else "mixed")
    text = {"up": "up-trend: KSE-100 above its rising 200-day average",
            "down": "down-trend: KSE-100 below its falling 200-day average",
            "mixed": "mixed: KSE-100 and its 200-day average point different ways"}[name]
    return {"name": name, "text": text, "kse": float(now), "sma200": float(avg), "gap": float((now / avg - 1) * 100)}


def profile(result_cls: str | None, chart: dict, pb: float | None, tier: str | None, reg: str) -> dict:
    from .earnings import BAD, GOOD
    rs = (chart or {}).get("rs_rank")
    return {"res": "G" if result_cls in GOOD else ("B" if result_cls in BAD else "N"),
            "chart": "S2" if (chart or {}).get("stage2") else ("S4" if (chart or {}).get("stage4") else "O"),
            "rs": "H" if rs is not None and rs == rs and rs >= 0.7 else ("M" if rs is not None and rs == rs and rs >= 0.5 else "L"),
            "pb": "U" if not pb else ("V" if pb <= 1.2 else ("F" if pb <= 3 else "X")),
            "tier": {"Large": "L", "Mid": "M"}.get(tier or "", "S"), "regime": reg}


NAMES = {"res": {"G": "good latest result", "B": "weak latest result", "N": "no strong or weak result"},
         "chart": {"S2": "stage 2 uptrend", "S4": "stage 4 downtrend", "O": "no clear trend"},
         "rs": {"H": "relative strength top 30%", "M": "middle relative strength", "L": "relative strength bottom half"},
         "pb": {"V": "price at 1.2x book or less", "F": "price 1.2x to 3x book", "X": "price above 3x book", "U": "book value unknown"},
         "tier": {"L": "large cap", "M": "mid cap", "S": "small cap"},
         "regime": {"up": "market in an up-trend", "down": "market in a down-trend", "mixed": "mixed market"}}


def lookup(p: dict) -> dict | None:
    """Most specific stored profile with at least MIN_CASES past cases."""
    for level in _stats().get("levels", []):
        keys = level["keys"]
        cell = level["cells"].get("|".join(p[k] for k in keys))
        # solid evidence only: enough cases, from enough different stocks, over enough years (else widen the match)
        if cell and (len(keys) == 1 or (cell["n"] >= MIN_CASES and cell["stocks"] >= MIN_STOCKS and cell["yrs"] >= MIN_YEARS)):
            return {**cell, "matched": [NAMES[k][p[k]] for k in keys]}
    return None


def size(price: float | None, risk_line: float | None, reg: str, side: str = "buy") -> dict:
    """Suggested position as a share of the portfolio, risking about 1% if the setup fails."""
    if not price or not risk_line or side == "sell" or risk_line >= price:
        return {}
    dist = (price - risk_line) / price
    pct = min(0.10, max(0.02, RISK_PER_IDEA / max(dist, 0.005)))
    if reg == "down":
        pct *= SIZING.get("down_factor", 1.0)
    return {"pct": pct * 100, "risk_pct": min(pct * dist, 0.10) * 100, "dist": dist * 100}


def read(result_cls: str | None, chart: dict, pb: float | None, tier: str | None, reg: dict, setup: dict | None) -> dict:
    p = profile(result_cls, chart, pb, tier, reg.get("name", "mixed"))
    su = setup or {}
    return {"profile": p, "cases": lookup(p), "regime": reg,
            "size": size(su.get("price"), su.get("risk"), reg.get("name", "mixed"), su.get("side", "buy"))}


def lines(a: dict, breadth: float | None = None) -> list[str]:
    if not a:
        return []
    L = []
    c = a.get("cases")
    if c:
        L.append(f"Similar past cases ({c['n']:,} stock-weeks in {c['stocks']} stocks, 2012-2026; matched on: "
                 + ", ".join(c["matched"]) + "): over the next 3 months they beat similar stocks "
                 f"{c['beat']:.0f}% of the time; average {c['avg']:+.1f}%, median {c['median']:+.1f}%; worst tenth "
                 f"{c['p10']:+.0f}%, best tenth {c['p90']:+.0f}%; positive in {c['yrs_pos']} of {c['yrs']} years")
    rg = a.get("regime") or {}
    if rg.get("text"):
        ev = ((_stats().get("regime") or {}).get("results_rule") or {}).get(rg.get("name", ""))
        L.append("Market now: " + rg["text"] + (f" ({rg['gap']:+.1f}% from it)" if rg.get("gap") is not None else "")
                 + (f"; {breadth * 100:.0f}% of liquid stocks are above their own 200-day average" if breadth is not None else "")
                 + (f". In this kind of market the tested results signal averaged {ev['edge']:+.1f}% vs similar stocks "
                    f"over 3 months ({ev['n']} cases; up-trend +5.1%, mixed +1.1%)" if ev else ""))
    sz = a.get("size") or {}
    if sz:
        L.append(f"Position size (risk rule, not an edge): about {sz['pct']:.0f}% of the portfolio, so a close below the "
                 f"fails-below line ({sz['dist']:.1f}% away) costs about {sz['risk_pct']:.1f}% of the portfolio"
                 + (f"; {SIZING['note']}" if SIZING.get("note") else ""))
    return L
