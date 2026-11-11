"""Fair value and balance sheet for one company, from the scstrade snapshot (all per share).

FAIR VALUE (three standard ways; the first is the one we tested)
  COE  cost of equity       = SBP policy rate + equity risk premium (config fair_value.erp, default 5 points)
  EPV  earnings power value = last 4 quarters' EPS / COE (the current profit, if it simply continued)
                              Tested 2022-2026: big companies priced well ABOVE it lagged similar stocks by
                              4.8% over 60 sessions every year; small and mid caps 50%+ below it beat similar
                              stocks by 4.8% (all 5 years). Before 2023 cheap P/E did not work on PSX: treat it
                              as working in the current market, not as a law.
  DDM  dividend value       = expected dividend x (1 + g) / (COE - g), g = ROE x share of profit kept,
                              capped at 8% and at COE - 6 points; only for companies paying out 40%+ of profit
  NAV  asset value          = book value per share (what the shareholders own after all liabilities)
  FAIR = EPV (the tested measure; it assumes no growth, so fast growers look expensive on it)
  RANGE = lowest to highest of EPV, DDM and NAV

BALANCE SHEET (liabilities against assets)
  assets per share        = book value / (equity / assets)
  liabilities per share   = assets per share - book value
  liabilities % of assets = 100 - equity to assets
  plus debt to equity, current ratio (short-term assets / short-term liabilities), quick ratio, cash per share
  Banks, insurers and modarabas run on deposits and policy money, so their liability flags are not used.
"""
from __future__ import annotations

FINANCIAL = ("BANK", "INSURANCE", "MODARABA", "INV. BANKS", "LEASING")


def coe(cfg: dict, policy_rate: float | None) -> float:
    fv = cfg.get("fair_value") or {}
    rf = policy_rate if policy_rate else float(fv.get("risk_free", 12.0))
    return (rf + float(fv.get("erp", 5.0))) / 100


def compute(snap: dict, cost_of_equity: float) -> dict:
    v, sector = snap["values"], (snap.get("sector") or "").upper()
    p, bv = v.get("price"), v.get("bv")
    out: dict = {"price": p, "coe": cost_of_equity, "nav": bv, "financial": any(k in sector for k in FINANCIAL)}
    # trailing 12 months = the last 4 reported quarters (what the backtest used). SCS's "expected" EPS can
    # annualise one unusual quarter (refineries showed 6x "upside"), so it is not used for the fair value.
    q = snap.get("eps_quarters") or {}
    last4 = [q[k] for k in sorted(q, key=lambda k: (k[:4], k[-1]))[-4:]]
    eps_fwd = sum(last4) if len(last4) == 4 else v.get("laeps")
    out["eps_used"] = eps_fwd
    if eps_fwd and eps_fwd > 0:
        out["epv"] = eps_fwd / cost_of_equity
    roe = (v.get("roe") or 0) / 100
    keep = 1 - (v.get("pr") or 100) / 100 if v.get("pr") is not None else 0.0
    g = max(0.0, min(roe * keep, 0.08, cost_of_equity - 0.06))
    dps = v.get("ed") or v.get("d")
    if dps and dps > 0 and (v.get("pr") or 0) >= 40:
        out["ddm"] = dps * (1 + g) / (cost_of_equity - g)
        out["g"] = g
    if out.get("epv"):
        out["fair"] = out["epv"]
        rng = [x for x in (out.get("epv"), out.get("ddm"), bv if bv and bv > 0 else None) if x]
        out["low"], out["high"] = min(rng), max(rng)
        out["upside"] = (out["fair"] / p - 1) * 100 if p else None
    if p and bv and bv > 0:
        out["pb"] = p / bv
    # balance sheet
    ear = v.get("ear")
    if bv and bv > 0 and ear and ear > 0:
        assets = bv / (ear / 100)
        out.update(assets=assets, liabilities=assets - bv, liab_pct=100 - ear)
    out.update(tde=v.get("tde"), cr=v.get("cr"), qr=v.get("qr"), cash=v.get("cps"))
    flags = []
    if not out["financial"]:
        if out.get("liab_pct") is not None and out["liab_pct"] > 70:
            flags.append(f"liabilities are {out['liab_pct']:.0f}% of assets")
        if v.get("tde") and v["tde"] > 200:
            flags.append(f"debt {v['tde'] / 100:.1f}x equity")
        if v.get("cr") is not None and v["cr"] < 1:
            flags.append(f"current ratio {v['cr']:.2f} (short-term bills exceed short-term assets)")
    if eps_fwd is not None and eps_fwd <= 0:
        flags.append("loss-making: no earnings value")
    out["flags"] = flags
    return out


def lines(fv: dict) -> list[str]:
    """Plain sentences for cards and the AI dossier."""
    L = []
    if fv.get("fair"):
        L.append(f"Fair value (earnings power, no growth, {fv['coe'] * 100:.0f}% required return) Rs {fv['fair']:,.2f}, "
                 f"{fv['upside']:+.0f}% from Rs {fv['price']:,.2f}"
                 + (f"; dividend value Rs {fv['ddm']:,.2f}" if fv.get("ddm") else "")
                 + (f"; asset value (book) Rs {fv['nav']:,.2f}" if fv.get("nav") else ""))
    elif fv.get("nav"):
        L.append(f"No earnings-based fair value (loss-making); asset value (book) Rs {fv['nav']:,.2f}, price to book {fv.get('pb', 0):.2f}x")
    if fv.get("assets") and not fv["financial"]:
        L.append(f"Per share: assets Rs {fv['assets']:,.2f}, liabilities Rs {fv['liabilities']:,.2f} "
                 f"({fv['liab_pct']:.0f}% of assets), net assets (book) Rs {fv['nav']:,.2f}")
    return L
