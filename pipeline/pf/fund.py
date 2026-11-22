"""Fundamentals of each holding and of the portfolio as a whole, from the same checked figures the Research Lab
uses (scstrade snapshot + checks.trailing + fairvalue.compute through underdog.verify).

Portfolio averages follow index-provider practice: P/E and P/B are HARMONIC weighted averages (the weighted
earnings or book yield, inverted: 1 / sum(w x E/P)), so one very expensive stock cannot swamp the figure and
loss-makers count with their negative earnings; dividend yield and ROE are plain weighted averages.
"""
from __future__ import annotations

from datetime import timedelta

from ..common import log

WHT_DIVIDEND = 0.15      # final tax on dividends for persons on the Active Taxpayers List (30% otherwise)
CGT_LISTED = 0.15        # shares bought from 1 Jul 2024: 15% for filers (higher for non-filers until Jul 2025)


def per_stock(sym: str, snap: dict | None, price: float, cost_e: float) -> dict:
    """P/E on checked trailing EPS, P/B on the updated book value, yield, ROE, debt, both fair values, flags."""
    from .. import earnings, underdog
    out = {"sym": sym, "price": price}
    if not snap:
        out["missing"] = True
        return out
    v = snap["values"]
    out.update(name=snap.get("name", sym), sector=snap.get("sector", ""), roe=v.get("roe"), de=v.get("tde"),
               dps=v.get("d"), mktcap=v.get("mktcap"), ff_mn=v.get("freefloat"), face=v.get("facevalue"))
    try:
        ver = underdog.verify(sym, snap, price, cost_e)
    except Exception as e:  # noqa: BLE001
        log.warning("portfolio fundamentals: %s", type(e).__name__)
        ver = {}
    ttm, bv = ver.get("ttm"), ver.get("bv_now", v.get("bv"))
    fv = ver.get("fv") or {}
    out.update(ttm=ttm, bv=bv, problems=ver.get("problems") or [], fair=fv.get("fair"), fade=fv.get("fade"),
               no_growth=fv.get("epv"))
    out["ey"] = ttm / price if ttm is not None and price else None          # earnings yield
    out["by"] = bv / price if bv and price else None                         # book yield
    out["pe"] = price / ttm if ttm and ttm > 0 else None
    out["pb"] = price / bv if bv and bv > 0 else None
    out["dy"] = out["dps"] / price if out.get("dps") and price else None
    out["up_stay"] = fv["fair"] / price - 1 if fv.get("fair") and price and not out["problems"] else None
    out["up_fade"] = fv["fade"] / price - 1 if fv.get("fade") and price and not out["problems"] else None
    try:
        a = earnings.assess(sym)
    except Exception:  # noqa: BLE001
        a = None
    if a:
        out["result"] = {"label": a.get("label"), "cls": a.get("cls"), "date": a.get("date"), "yoy": a.get("yoy"),
                         "words": earnings.change_word(a.get("eps"), a.get("eps_ly"), a.get("yoy"))}
    flags = []
    if ttm is not None and ttm < 0:
        flags.append("loss-making over the last 4 quarters")
    if bv is not None and bv <= 0:
        flags.append("negative book value")
    if "defaulter" in (snap.get("name") or "").lower():
        flags.append("in PSX's defaulters segment")
    fin = any(k in (snap.get("sector") or "").upper() for k in ("BANK", "INSURANCE", "MODARABA", "LEASING", "INV."))
    if out.get("de") and out["de"] > 100 and not fin:
        flags.append(f"debt {out['de']:.0f}% of equity")
    out["flags"], out["financial"] = flags, fin
    return out


def aggregate(rows: list[dict], w: dict) -> dict:
    """Weighted portfolio figures over the holdings that have each number (coverage reported)."""
    def wsum(key, harmonic=False):
        got = [(w[r["sym"]], r[key]) for r in rows if r.get(key) is not None and r["sym"] in w]
        cw = sum(x for x, _ in got)
        if not got or cw <= 0:
            return None, 0.0
        avg = sum(x * y for x, y in got) / cw
        if harmonic:
            return (1 / avg if avg > 0 else None), cw
        return avg, cw

    pe, c_pe = wsum("ey", True)
    pb, c_pb = wsum("by", True)
    dy, c_dy = wsum("dy")
    roe, c_roe = wsum("roe")
    up_s, c_us = wsum("up_stay")
    up_f, c_uf = wsum("up_fade")
    tot = sum(w.values()) or 1
    return {"pe": pe, "pb": pb, "dy": dy, "roe": roe, "up_stay": up_s, "up_fade": up_f,
            "cov": {"pe": c_pe / tot, "pb": c_pb / tot, "dy": c_dy / tot, "roe": c_roe / tot, "fv": c_us / tot}}


def events(syms: set[str], now, days: int = 30) -> list[dict]:
    """Board meetings and ex-dates of the holdings in the next `days` days."""
    from ..sources import scs
    out = []
    end = now + timedelta(days=days)
    try:
        for b in scs.board_meetings():
            c = (b.get("company_code") or "").strip()
            d = b.get("date")
            if c in syms and d and now.date() <= d.date() <= end.date():
                out.append({"sym": c, "date": d.replace(tzinfo=None), "what": "board meeting (results expected)"})
    except Exception:  # noqa: BLE001
        pass
    try:
        for b in scs.book_closures():
            c = (b.get("company_code") or "").strip()
            d = b.get("date")
            if c in syms and d and now.date() <= d.date() <= end.date():
                pay = " ".join(x for x in ((b.get("bm_dividend") or "").strip(), (b.get("bm_bonus") or "").strip()) if x)
                out.append({"sym": c, "date": d, "what": "ex-date" + (f" ({pay})" if pay else "")})
    except Exception:  # noqa: BLE001
        pass
    return sorted(out, key=lambda e: e["date"])
