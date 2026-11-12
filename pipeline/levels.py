"""Buy and sell levels and the setup check on every Research Lab card (blue chips, daily-scan stocks, on request).

Honest split, from the 2010-2026 tests:
  DIRECTION comes from what was tested to work on PSX: results (good / weak quarter vs a year ago), the daily
  scan section, the fair value gap, plus the AI view (being tested). Price patterns (breakouts, RSI, moving
  averages) had NO edge after costs, so technicals do NOT vote on direction.
  LEVELS come from the price chart: where buying is closest to support (buy zone), where the setup is broken
  (close below the risk line), and the next resistance levels (targets). These are not tested; they make the
  risk of a position visible before it is taken.

Price history from scstrade is not adjusted for dividends and bonus issues; levels use the last 120 sessions only.
"""
from __future__ import annotations

import numpy as np

SECTION = {"breakout": "Results breakout", "breakout_backed": "Results breakout at 1.2x book or less",
           "backed": "Asset-backed and improving", "volume": "Volume with good results",
           "warning": "Warning: volume after weak results"}


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    a, out = 2 / (n + 1), np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _rsi(c: np.ndarray, n: int = 14) -> float | None:
    if len(c) <= n + 1:
        return None
    d = np.diff(c)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(d)):  # Wilder smoothing
        au, ad = (au * (n - 1) + up[i]) / n, (ad * (n - 1) + dn[i]) / n
    return 100.0 if ad == 0 else float(100 - 100 / (1 + au / ad))


def _macd(c: np.ndarray) -> str | None:
    if len(c) < 35:
        return None
    m = _ema(c, 12) - _ema(c, 26)
    return "Buy" if m[-1] > _ema(m, 9)[-1] else "Sell"


def weekly_closes(px: list[tuple]) -> list[float]:
    """Last close of each Monday-to-Friday week (ISO weeks; numpy's 'W' unit starts weeks on Thursday)."""
    from datetime import date
    wk = [date.fromisoformat(p[0][:10]).isocalendar()[:2] for p in px]
    return [px[i][2] for i in range(len(px)) if px[i][2] and (i == len(px) - 1 or wk[i + 1] != wk[i])]


def technicals(px: list[tuple]) -> dict:
    """SCS-style technicals computed from closing prices, for stocks SCS does not cover (it covers ~74).
    px rows: (date, open, close, volume[, high, low])."""
    c = np.array([p[2] for p in px if p[2]], dtype=float)
    if len(c) < 60:
        return {}
    weekly = np.array(weekly_closes(px), dtype=float)
    out = {"macddaily": _macd(c), "macdweekly": _macd(weekly), "rsidaily": _rsi(c),
           "sma50": float(c[-50:].mean()), "sma100": float(c[-100:].mean()) if len(c) >= 100 else None,
           "sma200": float(c[-200:].mean()) if len(c) >= 200 else None, "src": "computed from closing prices"}
    lv = swing_levels(px)
    out.update({"support1": lv.get("s1"), "support2": lv.get("s2"), "resistance1": lv.get("r1"),
                "resistance2": lv.get("r2")})
    return out


def swing_levels(px: list[tuple], price: float | None = None) -> dict:
    """Support = recent swing lows below the price, resistance = recent swing highs above it (5 to 120 sessions)."""
    px = [p for p in px if p[2]][-120:]
    if len(px) < 30:
        return {}
    hi = np.array([(p[4] if len(p) > 4 and p[4] else p[2]) for p in px], dtype=float)
    lo = np.array([(p[5] if len(p) > 5 and p[5] else p[2]) for p in px], dtype=float)
    p = price or px[-1][2]
    lows = sorted({round(float(lo[-n:].min()), 2) for n in (5, 10, 20, 60, 120) if len(lo) >= min(n, 30)}, reverse=True)
    highs = sorted({round(float(hi[-n:].max()), 2) for n in (5, 10, 20, 60, 120) if len(hi) >= min(n, 30)})
    sup = [x for x in lows if x < p * 0.995]
    res = [x for x in highs if x > p * 1.005]
    s1 = sup[0] if sup else None
    s2 = next((x for x in sup[1:] if s1 and x < s1 * 0.98), None)
    r1 = res[0] if res else None
    r2 = next((x for x in res[1:] if r1 and x > r1 * 1.02), None)
    return {"s1": s1, "s2": s2, "r1": r1, "r2": r2}


def setup(d: dict, call: dict | None, scan_item: dict | None, result: dict | None) -> dict:
    """The setup check: which tested signals point which way, plus the price levels."""
    from . import earnings
    v = d["snap"]["values"]
    price = v.get("price") or (d["px"][-1][2] if d.get("px") else None)
    # three independent families, one vote each, so the same evidence is never counted twice:
    # results (the scan section or the latest quarter), value (asset backing or fair value), and the AI view
    votes: list[tuple[int, str]] = []
    good = bool(result and result["cls"] in earnings.GOOD)
    bad = bool(result and result["cls"] in earnings.BAD)
    if scan_item and scan_item["kind"] == "warning":
        votes.append((-2, f"daily scan warning: heavy buying after weak results; {scan_item.get('tested', '')}"))
    elif scan_item:
        votes.append((1, f"daily scan: {SECTION.get(scan_item['kind'], scan_item['kind'])}; {scan_item.get('tested', 'tested')}"))
    elif good:
        votes.append((1, f"results improving: {result['label']}, {result['line']}"))
    elif bad:
        votes.append((-1, f"results weaker: {result['label']}, {result['line']}"))
    if scan_item and result and scan_item["kind"] != "warning":
        votes[-1] = (votes[-1][0], votes[-1][1] + f"; {result['label']}: {result['line']}")
    fv = d.get("fv") or {}
    pb, up = fv.get("pb"), fv.get("upside")
    if pb is not None and 0 < pb <= 1.2 and good:
        votes.append((1, f"value: {pb:.2f}x book value with results improving (tested)"
                         + (f"; fair value Rs {fv['fair']:,.2f}" if fv.get("fair") else "")))
    elif fv.get("fair") and up is not None and up >= 30:
        votes.append((1, f"value: price {up:.0f}% below its fair value Rs {fv['fair']:,.2f}"))
    elif fv.get("fair") and up is not None and up <= -30:
        votes.append((-1, f"value: price {-up:.0f}% above its fair value Rs {fv['fair']:,.2f}"))
    # chart: only the tested reads vote (confirms / cancels good results; stage 4 and springs warn)
    from .ta import verdicts
    good_now = good or bool(scan_item and scan_item["kind"] != "warning")
    votes += verdicts(d.get("chart") or {}, good_now)
    if call and call.get("view") in ("Outperform", "Underperform") and call.get("confidence", 0) >= 3:
        votes.append((1 if call["view"] == "Outperform" else -1,
                      f"AI view {call['view']} {call['confidence']}/5 (still being scored)"))
    score = sum(s for s, _ in votes)
    # BUY SIDE / SELL SIDE: results, value and the AI agree (or a tested warning plus one more against)
    label = ("BUY SIDE" if score >= 3 else "LEANING BUY" if score >= 1 else "SELL SIDE" if score <= -3
             else "LEANING SELL" if score <= -1 else "MIXED, WAIT")
    lv = swing_levels(d.get("px") or [], price)
    out = {"score": score, "label": label, "votes": votes, "price": price, **lv}
    if price and lv.get("s1"):
        s1 = lv["s1"]
        out["zone"] = (s1, round(s1 * 1.03, 2))
        # the next support if it is close (within 8%); otherwise 5% under the first support, so the risk stays small
        risk = lv["s2"] if lv.get("s2") and lv["s2"] >= s1 * 0.92 else round(s1 * 0.95, 2)
        out["risk"] = risk
        if lv.get("r1") and price > risk:
            out["rr"] = (lv["r1"] - price) / (price - risk)
    # what the levels say right now
    if not price:
        out["now"] = ""
    elif scan_item and scan_item["kind"].startswith("breakout") and score >= 1:
        out["now"] = "Tested entry for a results breakout: the next session's open (that is how the result was measured)."
    elif out.get("zone") and price <= out["zone"][1]:
        out["now"] = f"Price is inside the buy zone (Rs {out['zone'][0]:,.2f} to {out['zone'][1]:,.2f})."
    elif out.get("zone"):
        out["now"] = (f"Price is {(price / out['zone'][1] - 1) * 100:.0f}% above the buy zone: closer to support "
                      f"means less to lose if the setup fails.")
    else:
        out["now"] = "No support level below the price in the last 120 sessions (at a new high)."
    if out.get("risk") and price and price < out["risk"]:
        out["now"] = f"Price is below the risk line Rs {out['risk']:,.2f}: the chart setup is broken."
    # sell side: the same levels read the other way round (where holders can lighten, where support breaks)
    out["side"] = "sell" if score <= -1 else "buy"
    if out["side"] == "sell" and price:
        if lv.get("r1"):
            out["sell_zone"] = (round(lv["r1"] * 0.97, 2), lv["r1"])
        out["exit"] = lv.get("s1")
        out["now"] = ("Weak setup: " + (f"rallies toward Rs {out['sell_zone'][0]:,.2f} to {out['sell_zone'][1]:,.2f} are where "
                                         "holders can lighten" if out.get("sell_zone") else "the price is at a high")
                      + (f"; a close below Rs {lv['s1']:,.2f} breaks support." if lv.get("s1") else "."))
    if fv.get("nav") and fv["nav"] > 0:
        out["asset_zone"] = (round(0.8 * fv["nav"], 2), round(1.2 * fv["nav"], 2))
    return out


def lines(su: dict) -> list[str]:
    """Plain lines for the card and the text message."""
    L = [su["now"]] if su.get("now") else []
    L += [("For: " if s > 0 else "Against: ") + t for s, t in su["votes"]]
    if not su["votes"]:
        L.append("No tested signal points either way today.")
    return L
