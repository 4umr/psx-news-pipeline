"""Investment plan on request: client answers (/plan) -> risk profile -> asset allocation -> equity model portfolio
-> projection and crisis tests -> switch trades from current holdings -> full review of the proposed portfolio.
Design and evidence: docs/portfolio-planning.md. Private: sent to the owner's chat only.
"""
from __future__ import annotations

import json

from ..common import ROOT, log, now_pkt

ETF_MIN = 200_000      # below this equity amount, an index ETF is the practical way to hold PSX equities


def _state() -> dict:
    sp = ROOT / "state" / "state.json"
    try:
        return json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    except (OSError, ValueError):
        return {}


def _prices(syms: list[str]) -> dict[str, float]:
    from ..sources import scs
    live, _ = scs.live_activity(None, wide=True)
    px = {r["company_code"]: r["trading_close"] for r in live if r.get("trading_close")}
    for r in scs.daily_activity():
        c = (r.get("company_code") or "").strip()
        if c in syms and c not in px and r.get("trading_close"):
            px[c] = r["trading_close"]
    return px


def build(cfg: dict, text: str) -> dict:
    """Everything for the plan report (no sending)."""
    import numpy as np

    from . import cma, data, engine, holdings, profile, select, stress
    from ..sources import psx
    a, hold_text, _ = profile.parse(text)
    prof = profile.score(a)
    cur, cur_cash, cur_mode, cur_probs = holdings.parse(hold_text) if hold_text else ([], 0.0, "shares", [])
    if cur and cur_mode != "shares":
        cur, cur_probs = [], cur_probs + ["current holdings must be share counts (FFC 500 @ 450) for a switch plan"]
    px_cur = _prices([h["sym"] for h in cur]) if cur else {}
    cur_value = sum(h["qty"] * px_cur.get(h["sym"], 0) for h in cur) + (cur_cash or 0)
    # with holdings listed, 'amount' is NEW money on top of them; the plan covers both
    amount = cur_value + (a.get("amount") or 0) if cur else (a.get("amount") or 0)
    if not amount:
        return {"error": "no amount (and no holdings) given"}
    st = _state()
    snap = st.get("snap") or {}
    sbp = snap.get("sbp") or {}
    sched = data.policy_schedule(sbp, snap.get("policy_rate_at"))
    now = now_pkt().replace(tzinfo=None)
    hist = cma.history(now)
    ca = cma.assumptions(cfg, sbp, sched[-1][1])
    months = max(12, int(round(prof["years"] * 12)))
    sims = cma._boot_months(cma.recentred(hist, ca), months, 5000)
    need = min(prof["need"], amount)
    investable = amount - need
    alloc = cma.allocate(prof, a.get("gold", True), sims)
    w = alloc["w"]
    proj = cma.project(w, sims, investable, a.get("monthly", 0) or 0, prof["years"], ca["tbill"])
    compare = cma.compare(prof, a.get("gold", True), sims, ca)
    idx = data.index_series(now)
    eps = stress.episodes(idx, 0.15)
    crises = cma.crises(w, hist, eps)
    one = cma.one_year(np.array([w["equity"], w["gold"], w["tbill"]]), sims)
    exp = w["equity"] * ca["equity"] + w["gold"] * ca["gold"] + w["tbill"] * ca["tbill"]
    eq_amt = investable * w["equity"]
    hs = {}
    for k in ("equity", "gold", "tbill"):
        r = hist[k].dropna()
        if len(r) > 24:
            wv = (1 + r).cumprod()
            hs[k] = {"cagr": float(wv.iloc[-1] ** (12 / len(r)) - 1), "vol": float(r.std() * 12 ** 0.5),
                     "mdd": float((wv / wv.cummax() - 1).min())}
    out = {"hist_stats": hs, "answers": a, "prof": prof, "amount": amount, "need": need, "investable": investable, "alloc": alloc,
           "w": w, "cma": ca, "proj": proj, "compare": compare, "crises": crises, "one": one, "exp": exp,
           "eq_amt": eq_amt, "cur": cur, "cur_value": cur_value, "new_money": a.get("amount") or 0, "cur_probs": cur_probs, "now": now, "hist_from": hist.index[0],
           "monthly": a.get("monthly", 0) or 0, "shariah": bool(a.get("shariah")), "gold_ok": a.get("gold", True)}
    if eq_amt <= 0:
        return out
    kmi = set()
    if out["shariah"]:
        try:
            kmi = set(psx.index_members("KMIALLSHR"))
        except Exception:  # noqa: BLE001
            kmi = set()
    style = select.style_for(prof)
    keep = [h["sym"] for h in cur]
    model = select.build(max(eq_amt, ETF_MIN), style, out["shariah"], kmi, keep=keep) if eq_amt < ETF_MIN else \
        select.build(eq_amt, style, out["shariah"], kmi, keep=keep)
    out["model"], out["style"], out["etf"] = model, style, eq_amt < ETF_MIN
    # switch trades (current -> target)
    if cur:
        tgt = {r["sym"]: r for r in model["rows"]}
        trades = []
        for h in cur:
            s, p = h["sym"], px_cur.get(h["sym"])
            want = tgt.get(s, {}).get("qty", 0) if not out["etf"] else 0
            if p and want != h["qty"]:
                d = want - h["qty"]
                gain = (p - h["cost"]) * min(h["qty"], -d) if d < 0 and h.get("cost") else None
                trades.append({"sym": s, "side": "Sell" if d < 0 else "Buy", "qty": abs(d), "price": p, "value": abs(d) * p,
                               "why": "not in the model portfolio" if want == 0 else "resize to the model weight",
                               "cgt": max(0.0, gain) * 0.15 if gain is not None else None})
        held = {h["sym"] for h in cur}
        if not out["etf"]:
            for s, r in tgt.items():
                if s not in held:
                    trades.append({"sym": s, "side": "Buy", "qty": r["qty"], "price": r["price"], "value": r["value"],
                                   "why": f"top {max(1, 100 - r['rank'] * 100):.0f}% of the tested score", "cgt": None})
        trades = sorted(trades, key=lambda t: (t["side"] != "Sell", -t["value"]))
        # the rest of the plan: where the money from sales, new money and current cash goes
        tb = investable * w["tbill"] + (model["left"] if not out["etf"] else eq_amt) + need
        gd = investable * w["gold"]
        if gd > 0:
            trades.append({"sym": "Gold", "side": "Buy", "qty": 0, "price": None, "value": gd,
                           "why": "the plan's gold part (physical or PMEX)", "cgt": None})
        trades.append({"sym": "T-bills / MM fund", "side": "Buy", "qty": 0, "price": None, "value": tb,
                       "why": "T-bill part" + (" + money kept aside" if need else "") + " + rounding left-over", "cgt": None})
        out["trades"] = trades
        out["cash_in"] = {"sales": sum(t["value"] for t in trades if t["side"] == "Sell"),
                          "new": out["new_money"], "cash": cur_cash or 0}
    # full review of the proposed portfolio (stocks + T-bill / cash part; gold is outside the PSX engine)
    if not out["etf"] and model["rows"]:
        hold = [{"sym": r["sym"], "qty": r["qty"]} for r in model["rows"]]
        cash = investable * w["tbill"] + model["left"] + need
        D = data.load([h["sym"] for h in hold], cfg, st)
        R = engine.analyse(hold, cash, "shares", D, cfg, prof)
        if not R.get("error"):
            out["review"], out["D"] = R, D
    log.info("plan built: profile %s, equity %.0f%%, %d stocks", prof["name"], w["equity"] * 100,
             len(model["rows"]))
    return out


def run(cfg: dict, text: str, s, say, dry: bool) -> bool:
    from ..lab import chunks
    from . import planreport, report
    P = build(cfg, text)
    if P.get("error"):
        from .profile import HELP
        say(f"Investment plan: {P['error']}.\n\n" + HELP)
        return False
    now = now_pkt()
    meta = {"price_day": f"{now:%a %d %b %Y}", "date_long": f"{now:%A, %d %B %Y}"}
    plan_pngs, plan_pdf = planreport.build(P, meta)
    rev_pngs, rev_pdf = [], None
    if P.get("review"):
        D = P["D"]
        rmeta = {**meta, "tier": D["tier"], "sector": D["sector"], "band": "PROPOSED PORTFOLIO  |  RISK CHECK"}
        rev_pngs, rev_pdf = report.build(P["review"], rmeta)
    pdf = planreport.merge([plan_pdf] + ([rev_pdf] if rev_pdf else []))
    msg = planreport.summary_text(P)
    if dry or not s.admin:
        out = ROOT / "out" / "plan"
        out.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(plan_pngs + rev_pngs, 1):
            (out / f"page{i:02d}.png").write_bytes(b)
        (out / "investment-plan.pdf").write_bytes(pdf)
        (out / "summary.txt").write_text(msg, encoding="utf-8")
        log.info("plan (dry run): %d pages written to out/plan", len(plan_pngs) + len(rev_pngs))
        return True
    for part in chunks(msg):
        s._post(s.admin, part, False)
    chats, s.chats = s.chats, [s.admin]
    try:
        s.send_album((plan_pngs + rev_pngs[:1])[:10], "Investment plan", name="plan")
    finally:
        s.chats = chats
    s.send_document(pdf, f"Investment-plan-{now:%Y-%m-%d}.pdf",
                    f"Investment plan ({len(plan_pngs)} pages) + risk check of the proposed portfolio ({len(rev_pngs)} pages)",
                    admin_only=True)
    log.info("plan sent (%d + %d pages)", len(plan_pngs), len(rev_pngs))
    return True
