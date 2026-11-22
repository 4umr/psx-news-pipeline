"""Suggested changes for an existing portfolio: concrete trades, each with its reason and the evidence.

Order of priority (as a portfolio manager would review a book):
  1. limits: single stock above the yardstick (10%, or the stock's KSE-100 weight up to 15%), risk share above 20%
  2. quality: defaulters, loss-makers, negative book value -> review / exit
  3. tested score (select.TESTED, PSX 2010-2026): holdings in the BOTTOM 30% of the liquid universe lagged the
     market by 3.5% a year (better in only 4 of 17 years, both halves negative); the TOP 30% beat it by 5.9% a year
     (13 of 17 years) -> replace bottom-30% holdings with the best-scored stock of the same sector, if one is in the
     top 30% and not held
  4. diversification: fewer than 15 stocks -> add the best-scored stocks from sectors not held
  5. loss limit (when the client's plan is known): 1-in-20 bad 12 months beyond the stated limit -> move part to
     T-bills / gold
  6. liquidity: positions needing more than 5 sessions to sell -> reduce over time
"""
from __future__ import annotations

EVIDENCE_BOTTOM = "bottom 30% of the tested score lagged the market by 3.5% a year (better in 4 of 17 years, 2010-2026)"
EVIDENCE_TOP = "top 30% beat the market by 5.9% a year (13 of 17 years)"


def _limit(s: str, kse_w: dict) -> float:
    return min(0.15, max(0.10, kse_w.get(s, 0.0)))


def exposures(R: dict, style: str = "Core growth") -> dict:
    """Each holding's percentile (0-100) on the tested inputs within today's liquid universe, and the weighted
    portfolio average (50 = like the average liquid stock)."""
    from . import select
    try:
        u = select.universe()
    except Exception:  # noqa: BLE001
        return {}
    rows, tot = [], 0.0
    agg = {"gro": 0.0, "mom": 0.0, "lowvol": 0.0, "dy": 0.0, "score": 0.0}
    for p in R["pos"]["rows"]:
        s = p["sym"]
        if s not in u.index:
            rows.append({"sym": s, "in": False})
            continue
        r = u.loc[s]
        e = {"sym": s, "in": True, "gro": u["gro"].rank(pct=True)[s], "mom": u["mom"].rank(pct=True)[s],
             "lowvol": 1 - u["vol"].rank(pct=True)[s] + 1 / len(u), "dy": u["dy"].rank(pct=True)[s],
             "score": r[style + " rank"], "w": p["weq"]}
        rows.append(e)
        for k in agg:
            if e[k] == e[k]:
                agg[k] += e[k] * p["weq"]
        tot += p["weq"]
    if tot:
        agg = {k: v / tot for k, v in agg.items()}
    return {"rows": rows, "avg": agg, "covered": tot, "style": style, "date": u.attrs.get("date")}


def suggestions(R: dict, prof: dict | None = None, style: str = "Core growth") -> list[dict]:
    from . import select
    out = []
    P = R["pos"]
    total = R["total"]
    held = {p["sym"] for p in P["rows"]}
    kse_w = R["kse_w"]
    done = set()
    # 1 limits
    for p in P["rows"]:
        L = _limit(p["sym"], kse_w)
        if p["w"] > L + 0.005:
            sell = (p["w"] - L) * total / p["price"]
            out.append({"action": "Trim", "sym": p["sym"], "detail": f"{p['w'] * 100:.0f}% -> {L * 100:.0f}% (sell about "
                        f"{sell:,.0f} shares, Rs {sell * p['price']:,.0f})", "why": "above the single-stock yardstick",
                        "evidence": "Pakistani equity funds: max 10% per company, or its index weight up to 15%"})
            done.add(p["sym"])
    for s, pct in (R.get("risk", {}).get("pct") or {}).items():
        if pct > 0.20 and s not in done:
            w = R["weights"][s]
            out.append({"action": "Trim", "sym": s, "detail": f"{w * 100:.0f}% -> about {w * 0.20 / pct * 100:.0f}%",
                        "why": f"{pct * 100:.0f}% of the portfolio's risk from one stock",
                        "evidence": "risk budgeting: no single holding above 20% of total risk"})
            done.add(s)
    # 2 quality
    for f in R.get("fund") or []:
        fl = [x for x in f.get("flags") or [] if any(k in x for k in ("loss-making", "negative book", "defaulters"))]
        if fl:
            out.append({"action": "Review", "sym": f["sym"], "detail": "exit or keep small", "why": "; ".join(fl),
                        "evidence": "prudence screen (loss-making companies had no reliable edge either way on PSX)"})
            done.add(f["sym"])
    # 3 tested score
    try:
        u = select.universe()
    except Exception:  # noqa: BLE001
        u = None
    if u is not None:
        col = style + " rank"
        pool = u[~u.index.isin(held) & ~u["name"].str.contains("defaulter", case=False) & ~(u["ttm"] < 0)]
        for p in P["rows"]:
            s = p["sym"]
            if s in done or s not in u.index or u.loc[s, col] >= 0.30:
                continue
            same = pool[(pool["sector"] == u.loc[s, "sector"]) & (pool[col] >= 0.70)].sort_values(col, ascending=False)
            alt = same.index[0] if len(same) else None
            out.append({"action": "Replace" if alt else "Review", "sym": s,
                        "detail": (f"-> {alt} (same sector, score in the top {100 - u.loc[alt, col] * 100:.0f}%)" if alt
                                   else f"score in the bottom {u.loc[s, col] * 100:.0f}%; no strong stock in its sector"),
                        "why": "weak on earnings growth, momentum and steadiness",
                        "evidence": EVIDENCE_BOTTOM + "; " + EVIDENCE_TOP})
        # 4 diversification
        n = len(P["rows"])
        if n < 15:
            have = {u.loc[s, "sector"] for s in held if s in u.index}
            new = pool[~pool["sector"].isin(have) & (pool[col] >= 0.70)].sort_values(col, ascending=False)
            picks, secs = [], set()
            for s, r in new.iterrows():
                if r["sector"] in secs:
                    continue
                picks.append(s)
                secs.add(r["sector"])
                if len(picks) >= min(6, 15 - n):
                    break
            if picks:
                out.append({"action": "Add", "sym": ", ".join(picks), "detail": f"about {100 / 15:.0f}% each, from new sectors",
                            "why": f"only {n} stocks", "evidence": "most single-company risk goes with 15-30 stocks; picks are "
                                                                     "in the top 30% of the tested score"})
    # 5 loss limit
    mc = (R.get("mc") or {}).get("normal")
    if prof and mc and mc["pct"][5] < -prof["max_loss"]:
        f = max(0.0, min(0.9, 1 - prof["max_loss"] / abs(mc["pct"][5])))
        out.append({"action": "Rebalance", "sym": "Portfolio", "detail": f"move about {f * 100:.0f}% into T-bills / money "
                    "market (and gold, if allowed)", "why": f"a 1-in-20 bad year ({mc['pct'][5] * 100:.0f}%) is beyond your "
                    f"{prof['max_loss'] * 100:.0f}% limit", "evidence": "loss-tolerance check (10,000 simulated years)"})
    # 6 liquidity
    for s, d in (R.get("liq", {}).get("rows") or {}).items():
        if d.get("days") and d["days"] > 5:
            out.append({"action": "Reduce slowly", "sym": s, "detail": f"{d['days']:.0f} sessions to sell at 20% of volume",
                        "why": "hard to sell in a hurry", "evidence": "SEC liquidity rule yardstick: max 15% illiquid"})
    return out
