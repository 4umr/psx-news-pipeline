"""Fair value and balance sheet for one company (all per share). Inputs are cross-checked first (checks.py).

REQUIRED RETURN (cost of equity)
  COE = 10-year PIB yield (the long-term risk-free rate in rupees; SBP policy rate if not known)
        + equity risk premium (config fair_value.erp, default 5 points)            e.g. 12.85% + 5% = 17.85%
  A share is a long-lived asset, so it is discounted at a long-term rate, not the overnight policy rate.

GROWTH (one assumption, used the same way by every method)
  ROE      = trailing 12-month EPS / book value per share
  g        = ROE x share of profit kept (1 - payout), capped at 8% (long-run nominal growth: inflation target
             5-7% plus real growth) and at COE - 4 points. Unknown payout: g = 0. The formula itself makes growth
             add value only when ROE is above COE (below it, growth lowers the value), so there is no jump.
  NEXT YEAR: E1 = trailing EPS x (1 + g). The growth formulas value NEXT year's earnings and dividends
             (Gordon); using last year's figures understates every growing company.

METHODS
  1. Residual income (justified P/B), the main method:  RI = BV x (ROE1 - g) / (COE - g),  ROE1 = E1 / BV
       standard for banks and asset-heavy companies; with g = 0 it equals the no-growth value.
  2. Dividend discount:  DDM = E1 x payout / (COE - g)
       only when the company pays out 40%+ AND its natural growth (ROE x retention) is not capped: if it keeps
       more profit than 8% growth needs, its dividends understate what the extra retained profit is worth.
  3. Normalised earnings (cycles):  NORM = normal EPS / COE (no growth), normal EPS = 5-year average ROE x
       today's book (the 5-year average EPS when book history is missing). Used only when this year is
       abnormal: losses now, or ROE outside 0.5x-1.5x its 5-year average. ROE, not EPS, so a company that has
       simply grown (UBL, MEBL) is not marked down to its old, smaller profits. If the 5-year average ROE is
       a loss while the company earns now (refiners in a boom year), book value joins as the normal anchor.
  NO-GROWTH VALUE (earnings power, EPV = trailing EPS / COE) is shown for reference (and is what the screens
  rank on, as tested). It is not averaged in: that mixed "no growth" with
  "growth" in one number (FFC came out at Rs 444 that way, against Rs 538 consistently valued).
  FAIR VALUE = median of the methods used; RANGE = lowest to highest; "agree" = range within 50% of fair.

  Tested measure (screens, backtest): the EPV gap. 2022-2026: big companies priced well above it lagged similar
  stocks by 4.8% over 60 sessions (every year); small and mid caps 50%+ below it beat them by 4.8% (every year).
  Cheap P/E did not work on PSX before 2023, so this is a current-market result, not a law.

BALANCE SHEET (at the last year end, so every figure is from the same date)
  assets per share       = book value at year end / (equity / assets)
  liabilities per share  = assets per share - book value at year end
  liabilities % assets   = 100 - equity / assets
  plus debt to equity, current ratio, cash per share (latest SCS figures, labelled with their period)
  Banks, insurers, modarabas and leasing companies run on deposits and policy money: no liability flags.
  Liquidation value (current assets minus all liabilities) needs balance-sheet lines that this data does not
  include, so it is deliberately not estimated.
"""
from __future__ import annotations

import re
from statistics import median

FINANCIAL = ("BANK", "INSURANCE", "MODARABA", "INV. BANKS", "LEASING")
G_CAP = 0.08


def risk_free(sbp: dict | None) -> float | None:
    """Long-term rupee risk-free rate in %: the 10-year PIB yield, else the policy rate."""
    sbp = sbp or {}
    y10 = ((sbp.get("pib") or {}).get("yields") or {}).get("10-Y")
    if y10 and 3 < y10 < 40:
        return float(y10)
    pr = sbp.get("policy_rate")
    return float(pr) if pr and 3 < pr < 40 else None


def coe(cfg: dict, rf: float | None) -> float:
    fv = cfg.get("fair_value") or {}
    rf = rf if rf else float(fv.get("risk_free", 12.0))
    return (rf + float(fv.get("erp", 5.0))) / 100


def _year_of(period: str | None) -> str | None:
    """'to 2026 4Q' -> '2026'."""
    m = re.search(r"(\d{4})", period or "")
    return m.group(1) if m else None


def _norm_roe(ann: dict, bv_hist: dict | None) -> float | None:
    """Average ROE of the last 5 complete years that have both EPS and year-end book value (3+ needed)."""
    yrs = [y for y in sorted(ann) if (bv_hist or {}).get(y) and bv_hist[y] > 0][-5:]
    return sum(ann[y] / bv_hist[y] for y in yrs) / len(yrs) if len(yrs) >= 3 else None


def compute(snap: dict, cost: float, ttm: float | None = None, bv_hist: dict | None = None) -> dict:
    """ttm = checked trailing EPS (checks.trailing); without it no earnings-based value is given."""
    v, sector = snap["values"], (snap.get("sector") or "").upper()
    p, bv = v.get("price"), v.get("bv")
    out: dict = {"price": p, "coe": cost, "nav": bv, "nav_period": snap.get("period", {}).get("bv"),
                 "financial": any(k in sector for k in FINANCIAL), "eps_used": ttm, "methods": {}}
    if p and bv and bv > 0:
        out["pb"] = p / bv
    ann = snap.get("eps_annual") or {}
    last5 = [ann[k] for k in sorted(ann)[-5:]]
    avg5 = sum(last5) / len(last5) if len(last5) >= 3 else None
    out["avg5"] = avg5
    nroe = _norm_roe(ann, bv_hist)
    norm_eps = nroe * bv if nroe is not None and bv and bv > 0 else avg5
    out["norm_eps"], out["norm_roe"] = norm_eps, nroe
    if ttm is not None and ttm <= 0 and norm_eps and norm_eps > 0:
        out["methods"]["Normalised earnings"] = norm_eps / cost
    if ttm is not None and ttm > 0 and bv and bv > 0:
        roe = ttm / bv
        pr = v.get("pr")
        keep = (1 - pr / 100) if pr is not None and 0 <= pr <= 100 else None
        natural = roe * keep if keep is not None else None
        g = max(0.0, min(natural, G_CAP, cost - 0.04)) if natural is not None else 0.0
        e1 = ttm * (1 + g)
        out.update(roe=roe, g=g, e1=e1, roe1=e1 / bv, g_natural=natural)
        out["epv"] = ttm / cost
        out["methods"]["Residual income"] = bv * (e1 / bv - g) / (cost - g)  # equals the no-growth value when g = 0
        if pr is not None and pr >= 40 and natural is not None and natural <= G_CAP and g > 0:
            out["methods"]["Dividend discount"] = e1 * pr / 100 / (cost - g)
        if nroe is not None and nroe <= 0 and roe > cost:
            # no normal profit over 5 years but earning above its cost now (a cyclical in a boom year):
            # book value is the normal-profit anchor (below its cost, value is under book anyway)
            out["methods"]["Book value (5-year ROE is a loss)"] = bv
        elif norm_eps and norm_eps > 0:
            ratio = (roe / nroe) if nroe and nroe > 0 else (ttm / avg5 if avg5 and avg5 > 0 else None)
            if ratio is not None and not 0.5 <= ratio <= 1.5:
                out["methods"]["Normalised earnings"] = norm_eps / cost
    vals = list(out["methods"].values())
    if vals:
        out["fair"] = median(vals)
        out["low"], out["high"] = min(vals), max(vals)
        out["agree"] = (out["high"] - out["low"]) <= 0.5 * out["fair"]
        out["upside"] = (out["fair"] / p - 1) * 100 if p else None
    out["epv_gap"] = (out["epv"] / p - 1) * 100 if p and out.get("epv") else None
    # balance sheet at the last year end
    ear = v.get("ear")
    yr = _year_of(snap.get("period", {}).get("ear"))
    bv_y = (bv_hist or {}).get(yr) if yr else None
    if bv_y and bv_y > 0 and ear and 0 < ear <= 100:
        assets = bv_y / (ear / 100)
        out.update(assets=assets, liabilities=assets - bv_y, liab_pct=100 - ear, bs_year=yr, bv_year=bv_y)
    out.update(tde=v.get("tde"), cr=v.get("cr"), qr=v.get("qr"), cash=v.get("cps"))
    flags = []
    if not out["financial"]:
        if out.get("liab_pct") is not None and out["liab_pct"] > 70:
            flags.append(f"liabilities are {out['liab_pct']:.0f}% of assets")
        if v.get("tde") and v["tde"] > 200:
            flags.append(f"debt {v['tde'] / 100:.1f}x equity")
        if v.get("cr") is not None and v["cr"] < 1:
            flags.append(f"current ratio {v['cr']:.2f} (short-term bills exceed short-term assets)")
    if ttm is not None and ttm <= 0:
        flags.append("loss-making over the last 4 quarters" + (
            f"; 5-year average EPS {avg5:.2f} is also a loss: no earnings value at all" if avg5 is not None and avg5 <= 0 else ""))
    elif ttm and avg5 is not None and avg5 <= 0:
        flags.append(f"5-year average EPS is a loss ({avg5:.2f}): today's profit may not last")
        out["agree"] = False
    out["flags"] = flags
    return out


def lines(fv: dict) -> list[str]:
    """Plain sentences for cards and the AI dossier."""
    L = []
    if fv.get("fair"):
        meth = "; ".join(f"{k} Rs {x:,.2f}" for k, x in fv["methods"].items())
        L.append(f"Fair value Rs {fv['fair']:,.2f} ({fv['upside']:+.0f}% from Rs {fv['price']:,.2f}), "
                 + ("median of " if len(fv["methods"]) > 1 else "from ") + f"{meth}; "
                 + f"required return {fv['coe'] * 100:.1f}%"
                 + (f", growth used {fv['g'] * 100:.1f}%" if fv.get("g") is not None else "")
                 + (f"; no-growth value (floor) Rs {fv['epv']:,.2f}" if fv.get("epv") and fv["epv"] < fv["fair"] * 0.98 else "")
                 + ("" if fv["agree"] else "; the methods disagree, so treat the range, not the point"))
    elif fv.get("nav"):
        why = ("losses over the last 4 quarters" + (f" and over 5 years on average (EPS {fv['avg5']:.2f})"
                                                    if fv.get("avg5") is not None and fv["avg5"] <= 0 else "")
               if fv.get("eps_used") is not None and fv["eps_used"] <= 0 else "earnings could not be verified")
        L.append(f"No fair value from earnings or dividends ({why}). The only anchor is book value Rs {fv['nav']:,.2f} "
                 f"a share; the price is {fv.get('pb', 0):.2f}x that, and losses keep lowering it")
    if fv.get("assets") and not fv["financial"]:
        L.append(f"At the {fv['bs_year']} year end, per share: assets Rs {fv['assets']:,.2f}, liabilities Rs "
                 f"{fv['liabilities']:,.2f} ({fv['liab_pct']:.0f}% of assets), book value Rs {fv['bv_year']:,.2f}")
    return L
