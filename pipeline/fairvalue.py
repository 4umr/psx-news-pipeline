"""Fair value and balance sheet for one company (all per share). Inputs are cross-checked first (checks.py).

REQUIRED RETURN
  COE = SBP policy rate + equity risk premium (config fair_value.erp, default 5 points)      e.g. 12% + 5% = 17%

THREE STANDARD METHODS (the ones sell-side analysts use when only reported figures are available)
  1. Earnings power value     EPV = trailing 12-month EPS / COE
       what today's profit is worth if it simply continued (no growth); conservative
  2. Residual income          RI  = BV x (ROE - g) / (COE - g)          (the "justified price to book")
       ROE = trailing EPS / book value per share; g = ROE x share of profit kept, capped at 8% and at COE - 4
       points (never above long-run nominal growth). Standard for banks and asset-heavy companies.
  3. Dividend discount        DDM = DPS x (1 + g) / (COE - g)
       only when the company pays out 40%+ of profit (otherwise dividends say little about value)
  4. Normalised earnings      NORM = average EPS of the last 5 years / COE
       used only when this year is abnormal: losses now (then it is the only method), or trailing EPS more
       than 50% above or below the 5-year average (cyclicals at a peak or trough). If the 5-year average is
       itself a loss while the company earns now, the card warns that current profit may not last.
  FAIR VALUE = median of the methods available; RANGE = lowest to highest.
  The methods "agree" when the range is within 50% of the fair value; otherwise the card says they disagree.

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


def coe(cfg: dict, policy_rate: float | None) -> float:
    fv = cfg.get("fair_value") or {}
    rf = policy_rate if policy_rate else float(fv.get("risk_free", 12.0))
    return (rf + float(fv.get("erp", 5.0))) / 100


def _year_of(period: str | None) -> str | None:
    """'to 2026 4Q' -> '2026'."""
    m = re.search(r"(\d{4})", period or "")
    return m.group(1) if m else None


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
    if ttm is not None and ttm <= 0 and avg5 and avg5 > 0:
        out["methods"]["Normalised earnings"] = avg5 / cost
    if ttm is not None and ttm > 0 and bv and bv > 0:
        roe = ttm / bv
        pr = v.get("pr")
        keep = (1 - pr / 100) if pr is not None and 0 <= pr <= 100 else 0.0   # unknown payout: assume no growth
        g = max(0.0, min(roe * keep, G_CAP, cost - 0.04))
        out.update(roe=roe, g=g)
        out["methods"]["Earnings power"] = ttm / cost
        if roe > g and g > 0.001:  # with no growth residual income equals earnings power: count it once
            out["methods"]["Residual income"] = bv * (roe - g) / (cost - g)
        dps = v.get("d")
        if dps and dps > 0 and pr is not None and pr >= 40:
            out["methods"]["Dividend discount"] = dps * (1 + g) / (cost - g)
        if avg5 is not None and avg5 > 0 and not 0.5 <= ttm / avg5 <= 1.5:
            out["methods"]["Normalised earnings"] = avg5 / cost
    vals = list(out["methods"].values())
    if vals:
        out["fair"] = median(vals)
        out["low"], out["high"] = min(vals), max(vals)
        out["agree"] = (out["high"] - out["low"]) <= 0.5 * out["fair"]
        out["upside"] = (out["fair"] / p - 1) * 100 if p else None
        out["epv"] = out["methods"].get("Earnings power")
        out["epv_gap"] = (out["epv"] / p - 1) * 100 if p and out["epv"] else None
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
        L.append(f"Fair value Rs {fv['fair']:,.2f} ({fv['upside']:+.0f}% from Rs {fv['price']:,.2f}), median of {meth}; "
                 f"required return {fv['coe'] * 100:.0f}%, growth used {fv['g'] * 100:.1f}%"
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
