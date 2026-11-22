"""The rule book: every check names the rule it applies and where the rule comes from.

Status: 'pass', 'watch' (close to the limit or a softer guideline missed), 'breach' (limit exceeded), 'info'.
Regulatory limits are those fund managers must keep; a private investor is free to do otherwise, so the report
calls them yardsticks, not obligations.
"""
from __future__ import annotations

SOURCES = {
    "secp": "Pakistani equity funds (SECP NBFC Regulations 2008, as set out in fund offering documents): one "
            "company max 10% of net assets or its index weight, up to 15%; one sector max 30% or its index "
            "weight, up to 35%",
    "ucits": "EU UCITS Directive, Art. 52: max 10% per issuer, and holdings above 5% together max 40% (5/10/40)",
    "count": "Evans and Archer (1968), Statman (1987): most single-company risk is diversified away with 15-30 stocks",
    "hhi": "Herfindahl-Hirschman index: effective number of holdings = 1 / sum of squared weights",
    "risk": "Risk budgeting with Euler risk contributions (Litterman 1996; Qian 2006)",
    "liq": "US SEC Rule 22e-4 (fund liquidity): illiquid holdings max 15%; selling assumed at 20% of a stock's "
           "usual daily value traded",
    "basel": "Basel Committee (1996) traffic light for VaR backtests: 0-4 breaches in 250 days green, 5-9 yellow, 10+ red",
    "stress": "Stress-testing practice: losses in past crises compared with the market's",
    "corr": "Correlation above 0.8: two holdings that behave almost as one",
    "quality": "Prudence: loss-making companies, negative equity and the PSX defaulters segment",
    "kmi": "PSX-KMI All Share Index (Shariah screening approved by SECP)",
    "vol": "Risk relative to the market (as on fund factsheets)",
}


def _st(value: float | None, limit: float, warn: float, higher_bad: bool = True) -> str:
    if value is None:
        return "info"
    if higher_bad:
        return "breach" if value > limit + 1e-9 else "watch" if value > warn else "pass"
    return "breach" if value < limit - 1e-9 else "watch" if value < warn else "pass"


def book(R: dict) -> list[dict]:
    out = []
    w, kse_w = R["weights"], R["kse_w"]
    tot = R["total"]
    # 1 single company (SECP-style)
    worst, wv, lim = None, 0.0, 0.10
    for s, x in w.items():
        L = min(0.15, max(0.10, kse_w.get(s, 0.0)))
        if x / L > (wv / lim if lim else 0):
            worst, wv, lim = s, x, L
    out.append({"rule": "Largest single holding", "limit": f"max {lim * 100:.0f}%" + (" (its KSE-100 weight)" if lim > 0.10 else ""),
                "value": f"{worst} {wv * 100:.1f}%" if worst else "-", "status": _st(wv, lim, 0.8 * lim), "src": "secp"})
    over = [s for s, x in w.items() if x > min(0.15, max(0.10, kse_w.get(s, 0.0))) + 1e-9]
    if len(over) > 1:
        out[-1]["value"] += f" ({len(over)} holdings above their limit)"
    # 2 sector
    sec = R["sector_w"]
    if sec:
        ks = R["kse_sector_w"]
        s_name, s_w = max(sec.items(), key=lambda kv: kv[1] / min(0.35, max(0.30, ks.get(kv[0], 0.0))))
        L = min(0.35, max(0.30, ks.get(s_name, 0.0)))
        out.append({"rule": "Largest sector", "limit": f"max {L * 100:.0f}%", "value": f"{s_name.title()} {s_w * 100:.1f}%",
                    "status": _st(s_w, L, 0.85 * L), "src": "secp"})
    # 3 UCITS 5/10/40
    big = sum(x for x in w.values() if x > 0.05)
    mx = max(w.values()) if w else 0
    st = "breach" if mx > 0.10 or big > 0.40 else ("watch" if big > 0.35 else "pass")
    out.append({"rule": "5/10/40 rule", "limit": "max 10% each; >5% holdings together max 40%",
                "value": f"largest {mx * 100:.1f}%, >5% holdings {big * 100:.0f}%", "status": st, "src": "ucits"})
    # 4 number of holdings, 5 effective number
    n = len(w)
    out.append({"rule": "Number of stocks", "limit": "at least 15", "value": str(n),
                "status": "pass" if n >= 15 else "watch" if n >= 8 else "breach", "src": "count"})
    en = R["conc"].get("eff_n")
    out.append({"rule": "Effective number of holdings", "limit": "at least 10",
                "value": f"{en:.1f}" if en else "-", "status": _st(en, 7.0, 10.0, higher_bad=False), "src": "hhi"})
    # 6 risk concentration
    rc = R.get("risk", {}).get("pct") or {}
    if rc:
        s, p = max(rc.items(), key=lambda kv: kv[1])
        out.append({"rule": "Largest share of total risk", "limit": "max 20% from one stock",
                    "value": f"{s} {p * 100:.0f}%", "status": _st(p, 0.20, 0.15), "src": "risk"})
    # 7 liquidity
    il = R["liq"].get("illiquid")
    if il is not None:
        out.append({"rule": "Hard-to-sell holdings", "limit": "max 15% needing 5+ sessions",
                    "value": f"{il * 100:.1f}%", "status": _st(il, 0.15, 0.10), "src": "liq"})
    # 8 market risk
    vr = R.get("vol_ratio")
    if vr:
        out.append({"rule": "Volatility vs KSE-100 (1 year)", "limit": "up to 1.25x",
                    "value": f"{vr:.2f}x", "status": "watch" if vr > 1.25 else "pass", "src": "vol"})
    # 9 VaR backtest
    k = (R.get("var") or {}).get("kupiec99") or {}
    if k.get("zone"):
        out.append({"rule": "Value at Risk model backtest (99%)", "limit": "green zone (0-4 breaches)",
                    "value": f"{k['breaches']} breaches in 250 days", "status": {"green": "pass", "yellow": "watch",
                                                                                   "red": "breach"}[k["zone"]], "src": "basel"})
    # 10 stress
    worst_ep = R.get("stress_worst")
    if worst_ep:
        ratio = worst_ep["ret"] / worst_ep["idx"] if worst_ep["idx"] < 0 else None
        out.append({"rule": "Loss in the worst past crisis vs market", "limit": "up to 1.2x the KSE-100's fall",
                    "value": f"{worst_ep['ret'] * 100:.0f}% vs {worst_ep['idx'] * 100:.0f}%",
                    "status": "watch" if ratio and ratio > 1.2 else "pass", "src": "stress"})
    # 11 correlation
    pairs = R.get("pairs") or []
    out.append({"rule": "Near-duplicate holdings", "limit": "no pair correlated above 0.8",
                "value": ", ".join(f"{a}/{b} {c:.2f}" for a, b, c in pairs[:3]) or "none",
                "status": "watch" if pairs else "pass", "src": "corr"})
    # 12 quality
    q = R.get("quality") or {}
    qv = q.get("weight", 0.0)
    out.append({"rule": "Weak-quality holdings", "limit": "max 10% (no defaulters)",
                "value": (f"{qv * 100:.1f}% ({', '.join(q.get('syms', [])[:4])})" if qv else "none"),
                "status": "breach" if q.get("defaulter") or qv > 0.10 else "watch" if qv > 0 else "pass", "src": "quality"})
    # 13 Shariah
    if R.get("kmi_share") is not None:
        out.append({"rule": "Shariah-compliant share", "limit": "information",
                    "value": f"{R['kmi_share'] * 100:.0f}% in KMI All Share stocks", "status": "info", "src": "kmi"})
    for r in out:
        r["source"] = SOURCES[r["src"]]
    _ = tot
    return out
