"""The investment plan as 5 A4 pages (house research style), followed in the PDF by the full risk review of the
proposed portfolio. Pages: 1 profile and policy, 2 asset allocation, 3 projection, 4 equity model portfolio,
5 switch plan, monitoring and disclosures.
"""
from __future__ import annotations

import io

import numpy as np

from .. import note
from ..note import BOTTOM, DOWN, GOLD, GREEN, INK, LIGHT, M, MUTED, RULE, UP, W, Page
from .report import COL, CW, _ax, _col, _legend, nm, pc, rs

PAGES = 5
FOOT = "PAKISTAN INVESTORS  |  Model plan for a risk profile, not personal investment advice. Disclosures on page 5."


def _title(p: Page, t: str) -> float:
    p.text(M, 150, t, 17, GREEN, bold=True)
    return 172


def _yrs(y: float) -> str:
    return f"{y:g} year{'s' if y != 1 else ''}"


def page1(P: dict, meta: dict) -> Page:
    p = Page(1, PAGES, meta)
    pr, a, w = P["prof"], P["answers"], P["w"]
    p.text(M, 160, "Investment plan", 24, GREEN, bold=True)
    p.text(M, 192, f"{pr['name']} profile  |  Rs {rs(P['amount'])}"
           + (f" (holdings Rs {rs(P['cur_value'])} + new Rs {rs(P['new_money'])})" if P.get("cur") else "")
           + (f" + Rs {rs(P['monthly'])} a month" if P["monthly"] else "") + f"  |  {_yrs(pr['years'])}  |  goal: {pr['goal']}",
           9.5, MUTED)
    y0 = 222
    x, sw = M, note.SIDE_W
    y = p.section(x, y0, sw, "The plan in brief")
    pj, one = P["proj"], P["one"]
    rows = [["Profile", pr["name"]], ["Risk level (SECP style)", pr["secp"]],
            ["PSX equities", f"{pc(w['equity'], 0)}  (Rs {rs(P['investable'] * w['equity'])})"],
            ["Gold", f"{pc(w['gold'], 1)}  (Rs {rs(P['investable'] * w['gold'])})"],
            ["T-bills / money market", f"{pc(w['tbill'], 0)}  (Rs {rs(P['investable'] * w['tbill'])})"]]
    if P["need"]:
        rows.append(["Kept aside (needed in a year)", f"Rs {rs(P['need'])}"])
    rows += [["Expected return a year", pc(P["exp"])], ["Typical year", pc(one["p50"], 0, True)],
             ["Bad year (1 in 20)", pc(one["p5"], 0, True)], ["Your loss limit", pc(-pr["max_loss"], 0, True)],
             [f"Value after {_yrs(pr['years'])} (median)", f"Rs {rs(pj['pct'][50])}"],
             ["Money put in", f"Rs {rs(pj['paid'])}"], ["Chance of ending below it", pc(pj["p_below_paid"], 0)]]
    p.table(x, y, [sw - 140, 140], rows, size=8.2)
    x, mw = note.MAIN_X, note.MAIN_W
    y = p.section(x, y0, mw, "Your answers and scores (1 = cautious, 5 = bold)")
    ans = {"Time horizon": _yrs(pr["years"]), "Income": a.get("income", "-"), "Age": f"{a['age']:g}" if a.get("age") else "-",
           "Money needed within a year": f"Rs {rs(pr['need'])}" if pr["need"] else "none",
           "Loss you could live with": pc(pr["max_loss"], 0), "Reaction to a 20% fall": a.get("reaction", "sell some (assumed)"),
           "Experience": a.get("experience", "none (assumed)"), "Goal": pr["goal"]}
    rows, cols = [], []
    for scale, parts in (("Ability", pr["cap_parts"]), ("Willingness", pr["tol_parts"])):
        for k, v in parts.items():
            rows.append([k, str(ans.get(k, "-")), scale, str(v)])
            cols.append([None, None, MUTED, UP if v >= 4 else DOWN if v <= 2 else None])
    y = p.table(x, y, [230, 230, 150, mw - 610], rows, header=["Question", "Answer", "Measures", "Score"], size=8,
                colors=cols, align=["left", "left", "left", "right"])
    txt = (f"Ability to take risk {pr['capacity']:.1f} of 5, willingness {pr['tolerance']:.1f} of 5. As advisers do (CFA "
           f"Institute), the more cautious of the two sets the profile: {pr['name'].lower()}"
           + (f", limited by your {'ability' if pr['limited_by'] == 'capacity' else 'willingness'}" if pr["limited_by"] != "both" else "")
           + ". " + (" ".join(n[0].upper() + n[1:] + "." for n in pr["notes"]) if pr["notes"] else ""))
    y = p.para(x, y, txt, mw, size=8.4) + 6
    y = p.section(x, y, mw, "Investment policy")
    bench = f"{pc(w['equity'], 0)} KSE-100, {pc(w['gold'], 0)} gold, {pc(w['tbill'], 0)} T-bills"
    rows = [["Return aim", f"beat T-bills ({pc(P['cma']['tbill'], 1)}) over {_yrs(pr['years'])}; expected {pc(P['exp'], 1)} a year"],
            ["Risk limit", f"a bad year (1 in 20) no worse than {pc(-pr['max_loss'], 0, True)}"],
            ["Time horizon", _yrs(pr["years"]) + "; equity is cut as the date gets closer"],
            ["Liquidity", f"Rs {rs(P['need'])} kept in cash" if P["need"] else "no withdrawals planned"],
            ["Tax", "15% on dividends and on gains (filers); losses offset gains for 3 years"],
            ["Shariah", "yes: KMI All Share stocks, Islamic money market or Ijara sukuk" if P["shariah"] else "not required"],
            ["Benchmark", bench], ["Rebalancing", "when a part drifts 5 points (or a quarter of its target); stocks every 3 months"],
            ["Review", "once a year, or when your income, goal or date changes"]]
    p.table(x, y, [160, mw - 160], rows, size=7.9, align=["left", "left"], max_y=BOTTOM)
    return p


def page2(P: dict, meta: dict) -> Page:
    p = Page(2, PAGES, meta)
    y = _title(p, "Asset allocation")
    w, al, ca, pr = P["w"], P["alloc"], P["cma"], P["prof"]
    # allocation bar
    yy = p.section(M, y + 6, CW, "The mix")
    parts = [("PSX equities", w["equity"], GREEN), ("Gold", w["gold"], GOLD), ("T-bills / money market", w["tbill"], "#9fb8a8")]
    x = M
    for lab, v, col in parts:
        if v <= 0:
            continue
        ww = CW * v
        p.rect(x, yy + 4, ww, 40, col)
        if ww > 90:
            p.text(x + 10, yy + 30, f"{lab} {v * 100:.0f}%", 8.5, "white", bold=True)
        x += ww
    yy += 56
    p.text(M, yy + 10, "  |  ".join(f"{lab}: Rs {rs(P['investable'] * v)}" for lab, v, _ in parts if v > 0)
           + (f"  |  kept aside: Rs {rs(P['need'])}" if P["need"] else ""), 8.5, INK)
    yy += 26
    steps = [f"Start for a {pr['name'].lower()} profile: {pc(al['base'], 0)} in equities.",
             f"Time horizon {_yrs(pr['years'])}: at most {pc(al['cap'], 0)} in equities (money needed sooner cannot wait out "
             "a crash; the same idea as the glide paths of goal planners)." if al["cap"] < al["base"] else
             f"Time horizon {_yrs(pr['years'])}: no cut needed.",
             (f"Loss check: with {pc(al['start_equity'], 0)} in equities a bad year (1 in 20) would be "
              f"{pc(al['steps'][0][1], 0, True)}, beyond your {pc(-pr['max_loss'], 0, True)} limit, so equities were cut to "
              f"{pc(w['equity'], 0)} (bad year {pc(al['steps'][-1][1], 0, True)}).") if al["cut_for_loss"] else
             f"Loss check: a bad year (1 in 20) is {pc(P['one']['p5'], 0, True)}, within your {pc(-pr['max_loss'], 0, True)} limit.",
             (f"Gold {pc(w['gold'], 1)}: in the KSE-100's worst months since 2008 gold in rupees rose 3.2% on average while "
              "shares fell 9.1%: it is the crash and rupee hedge.") if w["gold"] else "No gold (not wanted, or too short a horizon).",
             f"The rest in T-bills / money market, earning about {pc(ca['tbill'], 1)} with no falls."]
    yy, _ = p.bullets(M, yy, steps, CW, size=8.4, gap=4)
    yy = p.section(M, yy + 4, CW, "The five standard mixes at your horizon")
    rows, cols = [], []
    for c in P["compare"]:
        rows.append([("> " if c["level"] == pr["level"] else "") + {1: "Conservative", 2: "Moderately conservative",
                     3: "Balanced", 4: "Growth", 5: "Aggressive"}[c["level"]], pc(c["equity"], 0), pc(c["gold"], 1),
                     pc(c["tbill"], 0), pc(c["exp"], 1), pc(c["p5"], 0, True), pc(c["p50"], 0, True), pc(c["p_loss"], 0)])
        cols.append([GREEN if c["level"] == pr["level"] else None, None, None, None, None,
                     DOWN if c["p5"] < -pr["max_loss"] else None, None, None])
    yy = p.table(M, yy, [250, 100, 90, 100, 130, 150, 130, CW - 950], rows,
                 header=["Mix", "Equities", "Gold", "T-bills", "Expected", "Bad year (1/20)", "Typical year", "Chance of loss"],
                 size=7.8, colors=cols)
    yy = p.para(M, yy, "Red: a bad year beyond your loss limit. Each mix before the loss check; yours after it.", CW,
                size=7.2, color=MUTED) + 4
    yy = p.section(M, yy, CW, "Assumptions (forward-looking) and the history behind the risk")
    hs = P.get("hist_stats") or {}
    rows = [["PSX equities", pc(ca["equity"], 1), f"10-year PIB {ca['rf10']:.2f}% + {ca['erp']:.0f}% equity premium (same as the fair value model)"],
            ["Gold in rupees", pc(ca["gold"], 1), "SBP's 5-7% inflation target midpoint + 1% long-run real return"],
            ["T-bills / money market", pc(ca["tbill"], 1), "today's SBP policy rate"]]
    for r, k in zip(rows, ("equity", "gold", "tbill")):
        h = hs.get(k) or {}
        r.append(f"{pc(h.get('cagr'), 1)} / {pc(h.get('vol'), 0)} / {pc(h.get('mdd'), 0)}" if h else "-")
    yy = p.table(M, yy, [200, 110, CW - 560, 250], rows,
                 header=["Asset", "Expected a year", "Basis", f"Since {P['hist_from']:%Y}: return / vol / worst fall"],
                 size=7.6, align=["left", "right", "left", "right"])
    yy = p.para(M, yy, "Ups and downs come from the real monthly history since 2008 (KSE-100, Comex gold x USD/PKR, SBP "
                "rate), resampled in blocks so crises and rebounds stay together, with each asset's average set to the "
                "assumption above (5,000 simulated paths).", CW, size=7.2, color=MUTED) + 4
    if P["crises"] and yy < BOTTOM - 120:
        yy = p.section(M, yy, CW, "The mix in past crises (bought at the top, held to the bottom)")
        rows, cols = [], []
        for c in P["crises"]:
            rows.append([c["name"], f"{c['peak']:%b %y} - {c['trough']:%b %y}", pc(c["kse"], 0), pc(c["gold"], 0, True),
                         pc(c["tbill"], 0, True), pc(c["mix"], 0, True)])
            cols.append([None, None, DOWN, _col(c["gold"]), _col(c["tbill"]), _col(c["mix"])])
        p.table(M, yy, [380, 200, 120, 120, 120, CW - 940], rows,
                header=["Crisis", "Period", "KSE-100", "Gold", "T-bills", "Your mix"], size=7.6, colors=cols,
                max_y=BOTTOM, align=["left", "left", "right", "right", "right", "right"])
    return p


def page3(P: dict, meta: dict) -> Page:
    p = Page(3, PAGES, meta)
    y = _title(p, "What it could grow to")
    pj, pr = P["proj"], P["prof"]
    yy = p.section(M, y + 6, CW, f"Value of the plan over {_yrs(pr['years'])}, 5,000 simulated paths"
                   + (f", with Rs {rs(P['monthly'])} added each month" if P["monthly"] else ""))
    yy = _legend(p, M, yy, [("median", GREEN), ("middle half of paths", "#a9cdb3"), ("90% of paths", LIGHT),
                           ("money put in", DOWN), ("all in T-bills", GOLD)])
    h = 520
    ax = _ax(p, M + 70, yy, CW - 80, h)
    fan = pj["fan"]
    xs = np.arange(1, len(fan[50]) + 1)
    ax.fill_between(xs, fan[5] / 1e6, fan[95] / 1e6, color=LIGHT, lw=0)
    ax.fill_between(xs, fan[25] / 1e6, fan[75] / 1e6, color="#a9cdb3", lw=0)
    ax.plot(xs, fan[50] / 1e6, color=GREEN, lw=1.6)
    paid = P["investable"] + P["monthly"] * xs
    ax.plot(xs, paid / 1e6, color=DOWN, lw=1.0, ls="--")
    tb = [P["investable"] * (1 + P["cma"]["tbill"]) ** (m / 12) + sum(P["monthly"] * (1 + P["cma"]["tbill"]) ** ((m - k - 1) / 12)
                                                                     for k in range(m)) for m in xs]
    ax.plot(xs, np.array(tb) / 1e6, color=GOLD, lw=1.0)
    ax.set_xlim(1, len(xs))
    ax.set_ylabel("Rs million", fontsize=7, color=MUTED)
    ax.set_xlabel("months", fontsize=7, color=MUTED)
    yy += h + 50
    yy = p.section(M, yy, COL, f"After {_yrs(pr['years'])}")
    rows = [["Bad case (1 in 20)", f"Rs {rs(pj['pct'][5])}"], ["Lower quarter", f"Rs {rs(pj['pct'][25])}"],
            ["Median", f"Rs {rs(pj['pct'][50])}"], ["Upper quarter", f"Rs {rs(pj['pct'][75])}"],
            ["Good case (1 in 20)", f"Rs {rs(pj['pct'][95])}"], ["Money put in", f"Rs {rs(pj['paid'])}"],
            ["All in T-bills instead", f"Rs {rs(pj['tbill_plan'])}"]]
    p.table(M, yy, [COL - 170, 170], rows, size=8.2)
    x2 = M + COL + 40
    y2 = p.section(x2, yy, COL, "Chances")
    rows = [["Ending below the money put in", pc(pj["p_below_paid"], 0)], ["Beating the all-T-bills plan", pc(pj["p_beat_tbill"], 0)],
            ["A loss in any one year", pc(P["one"]["p_loss"], 0)], ["Bad year (1 in 20)", pc(P["one"]["p5"], 0, True)]]
    y2 = p.table(x2, y2, [COL - 130, 130], rows, size=8.2)
    p.para(x2, y2, "Before tax and costs. Rupee figures are not adjusted for inflation (SBP's target is 5-7% a year). "
           "Simulations show a range, not a promise: markets can do worse than anything in the 2008-2026 record.",
           COL, size=7.2, color=MUTED)
    return p


def page4(P: dict, meta: dict) -> Page:
    p = Page(4, PAGES, meta)
    y = _title(p, "Equity model portfolio")
    mo = P.get("model")
    if not mo:
        p.para(M, y + 10, "No equities in this plan (short horizon or a tight loss limit): T-bills / money market"
               + (" and gold" if P["w"]["gold"] else "") + " only.", CW, size=10)
        return p
    t = mo["tested"]
    intro = (f"{P['style']} score, tested on PSX 2010-2026: the 20 best-scored of the 120 most-traded stocks, rebuilt every 3 "
             f"months, returned {t['excess'] * 100:+.1f}% a year more than the average liquid stock (better in {t['years']} "
             f"years), with volatility {t['vol'] * 100:.0f}% against {t['uvol'] * 100:.0f}% and a worst fall of "
             f"{t['mdd'] * 100:.0f}% against {t['umdd'] * 100:.0f}% (Sharpe {t['sharpe']:.2f} vs {t['usharpe']:.2f}). "
             + ("Inputs: latest result vs a year earlier, 12-month momentum (last month skipped), low volatility."
                if P["style"] == "Core growth" else "Inputs: latest result vs a year earlier, low volatility, dividend yield."))
    y = p.para(M, y + 6, intro, CW, size=8) + 4
    if P.get("etf"):
        etf = "Meezan Pakistan ETF (MZNPETF)" if P["shariah"] else "NBP Pakistan Growth ETF (NBPGETF) or UBL Pakistan Enterprise ETF (UBLPETF)"
        y = p.para(M, y, f"For Rs {rs(P['eq_amt'])} in equities, an index ETF on PSX is the practical choice: {etf}. "
                   "The list below shows what the stock portfolio would be with Rs 200,000 or more.", CW, size=9,
                   bold=True) + 6
    rows, cols = [], []
    for i, r in enumerate(mo["rows"], 1):
        rows.append([str(i), r["sym"], r["sector"][:26], f"{r['qty']:,}", nm(r["price"]), rs(r["value"]),
                     pc(r["value"] / max(1, mo["spent"]), 1), f"top {max(1, 100 - r['rank'] * 100):.0f}%",
                     ("> +300%" if r["gro"] > 3 else pc(r["gro"], 0, True)) if r["gro"] == r["gro"] else "-", pc(r["mom"], 0, True), pc(r["vol"], 0),
                     pc(r["dy"], 1)])
        cols.append([MUTED, None, None, None, None, None, None, UP, _col(r["gro"]) if r["gro"] == r["gro"] else None,
                     _col(r["mom"]), None, None])
    y = p.table(M, y, [40, 90, 230, 80, 85, 100, 75, 90, 100, 90, 70, CW - 1050], rows,
                header=["#", "Stock", "Sector", "Shares", "Price", "Rs", "Weight", "Score", "Last result", "12m", "Vol.", "Yield"],
                size=7.4, colors=cols, bold_first=False, max_y=1160,
                align=["right", "left", "left"] + ["right"] * 9)
    y = p.para(M, y, f"Total Rs {rs(mo['spent'])}; Rs {rs(mo['left'])} left over goes to T-bills. Prices of "
               f"{mo['date']:%d %b %Y}" + (f"; kept from what you own: {', '.join(mo['kept'])}" if mo.get("kept") else "")
               + ". Screens not part of the test: no defaulters, no loss-makers over the last 4 quarters"
               + (", KMI All Share only" if P["shariah"] else "") + ". At most 30% of the names in one sector; every "
               "position can be sold in one day at 20% of the stock's usual trading.", CW, size=7.4, color=MUTED) + 6
    sec: dict = {}
    for r in mo["rows"]:
        sec[r["sector"]] = sec.get(r["sector"], 0) + r["value"]
    yy = p.section(M, y, COL, "Sectors")
    rows = [[k[:44], pc(v / max(1, mo["spent"]), 0)] for k, v in sorted(sec.items(), key=lambda kv: -kv[1])][:9]
    p.table(M, yy, [COL - 100, 100], rows, size=7.8, max_y=BOTTOM)
    x2 = M + COL + 40
    y2 = p.section(x2, y, COL, "Putting it in place")
    tips = ["Buy with limit orders; for large positions spread buying over 2-3 sessions.",
            "T-bill part: a money market fund, T-bills through a bank or the HBL Total Treasury ETF (HBLTETF) on PSX"
            + ("; Shariah: an Islamic money market fund or GoP Ijara sukuk." if P["shariah"] else "."),
            "Gold part: physical gold or PMEX gold contracts (no gold ETF on PSX yet)." if P["w"]["gold"] else
            "No gold in this plan.",
            "Rebuild the stock list every 3 months (as tested); new money goes to whatever is furthest below target."]
    p.bullets(x2, y2, tips, COL, size=7.9, gap=4, max_y=BOTTOM)
    return p


def page5(P: dict, meta: dict) -> Page:
    p = Page(5, PAGES, meta)
    y = _title(p, "Switch plan, monitoring and disclosures")
    tr = P.get("trades")
    if tr:
        yy = p.section(M, y + 6, CW, "From what you own to the plan")
        rows, cols = [], []
        tr = ([t for t in tr if t["side"] == "Sell"] + [t for t in tr if t["side"] == "Buy" and not t["qty"]]
              + [t for t in tr if t["side"] == "Buy" and t["qty"]])
        for t in tr[:26]:
            rows.append([t["side"], t["sym"], f"{t['qty']:,.0f}" if t["qty"] else "", nm(t["price"]) if t["price"] else "",
                         rs(t["value"]), t["why"], rs(t["cgt"]) if t.get("cgt") else "-"])
            cols.append([DOWN if t["side"] == "Sell" else UP, None, None, None, None, None, None])
        yy = p.table(M, yy, [80, 100, 100, 100, 130, CW - 650, 140], rows,
                     header=["Trade", "Stock", "Shares", "Price", "Rs", "Why", "Gains tax est."], size=7.3, colors=cols,
                     max_y=1080, align=["left", "left", "right", "right", "right", "left", "right"])
        sells = sum(t["value"] for t in tr if t["side"] == "Sell")
        sbuys = sum(t["value"] for t in tr if t["side"] == "Buy" and t["qty"])
        other = sum(t["value"] for t in tr if t["side"] == "Buy" and not t["qty"])
        cgt = sum(t.get("cgt") or 0 for t in tr)
        ci = P.get("cash_in") or {}
        y = p.para(M, yy, f"Money in: sales Rs {rs(sells)} + new money Rs {rs(ci.get('new', 0))} + cash Rs {rs(ci.get('cash', 0))}. "
                   f"Money out: shares Rs {rs(sbuys)} + T-bills and gold Rs {rs(other)}. Estimated costs about "
                   f"Rs {rs(0.004 * (sells + sbuys))} (0.4% a side on shares), capital gains tax about Rs {rs(cgt)} (15%, shares "
                   "bought from July 2024; older lots by holding period). Holdings that score in the better half were kept "
                   "to save cost and tax.", CW, size=7.6, color=MUTED) + 6
    elif P.get("cur_probs"):
        y = p.para(M, y + 6, "Switch plan not made: " + "; ".join(P["cur_probs"]), CW, size=8.4) + 6
    else:
        yy = p.section(M, y + 6, CW, "Putting new money in")
        mo = P["monthly"]
        lines = ["A lump sum invested at once has beaten investing in parts about two times in three in most markets, "
                 "because markets rise more often than they fall; investing in 3 monthly parts costs a little return but "
                 "softens the regret if a fall comes soon after. Either is reasonable.",
                 f"Each month's Rs {rs(mo)}: {pc(P['w']['equity'], 0)} to the stocks furthest below their weight, "
                 f"{pc(P['w']['gold'], 0)} to gold, the rest to T-bills." if mo else
                 "No monthly additions were given: rebalancing uses dividends and the T-bill income first."]
        y, _ = p.bullets(M, yy, lines, CW, size=8.2, gap=4)
    y = p.section(M, y + 4, CW, "Keeping it on track")
    rows = [["Every 3 months", "Re-score the stock list; replace stocks that fell to the bottom 30% of the score (the tested "
             "rebuild); check each stock's latest result."],
            ["When a part drifts", "Rebalance when equities, gold or T-bills are 5 points (or a quarter of their target) away"],
            ["After a big fall", "Do not sell in panic: the plan was sized for a bad year of "
             f"{pc(P['one']['p5'], 0, True)}; rebalancing buys shares when they are cheap"],
            ["Once a year", "Review the profile: income, goal, horizon (equity is cut as the date gets near)"],
            ["Any time", "Send /portfolio with your holdings for a full risk review and suggested changes"]]
    y = p.table(M, y, [190, CW - 190], rows, size=7.9, align=["left", "left"])
    p.rule(M, max(y + 10, BOTTOM - 260), W - M, color=GREEN, lw=1)
    yd = max(y + 10, BOTTOM - 260) + 12
    p.text(M, yd + 12, "DISCLOSURES", 8, GREEN, bold=True)
    p.para(M, yd + 22,
           "This is a model plan for the risk profile that the answers give, prepared by PAKISTAN INVESTORS from public PSX, "
           "SBP and market data and rules tested on PSX history. It is for information only and is not personal investment "
           "advice, an offer or a recommendation to buy or sell. In Pakistan, investment advice to clients is a licensed "
           "activity (SECP: investment advisory services by licensed NBFCs; asset management companies must assess "
           "suitability and risk-categorise funds, Circular 06 of 2022). Expected returns are assumptions; simulations and "
           "back-tests do not guarantee future results; stock prices can fall below what you paid; tax rates are for "
           "filers and can change. Methods and evidence: portfolio planning notes (docs/portfolio-planning.md).",
           CW, size=6.9, color=MUTED, lead=1.35)
    return p


def build(P: dict, meta: dict) -> tuple[list[bytes], bytes]:
    meta = {"band": "INVESTMENT PLAN  |  PRIVATE", "tag": "Model plan for a risk profile", "foot": FOOT, **meta}
    pages = [f(P, meta) for f in (page1, page2, page3, page4, page5)]
    return note._finish(pages, "Investment plan")


def merge(pdfs: list[bytes]) -> bytes:
    from pypdf import PdfReader, PdfWriter
    wr = PdfWriter()
    for b in pdfs:
        for pg in PdfReader(io.BytesIO(b)).pages:
            wr.add_page(pg)
    wr.add_metadata({"/Title": "Investment plan", "/Author": "PAKISTAN INVESTORS"})
    buf = io.BytesIO()
    wr.write(buf)
    return buf.getvalue()


def summary_text(P: dict) -> str:
    from ..common import esc
    pr, w, pj = P["prof"], P["w"], P["proj"]
    lines = [f"<b>Investment plan</b> · {esc(pr['name'])} profile ({esc(pr['secp'])} risk)",
             f"Rs {rs(P['amount'])}" + (f" + Rs {rs(P['monthly'])} a month" if P["monthly"] else "")
             + f", {_yrs(pr['years'])}, goal {esc(pr['goal'])}", "",
             f"• Mix: {pc(w['equity'], 0)} PSX equities, {pc(w['gold'], 1)} gold, {pc(w['tbill'], 0)} T-bills / money market"
             + (f"; Rs {rs(P['need'])} kept aside" if P["need"] else ""),
             f"• Expected {pc(P['exp'], 1)} a year; typical year {pc(P['one']['p50'], 0, True)}, bad year (1 in 20) "
             f"{pc(P['one']['p5'], 0, True)} (your limit {pc(-pr['max_loss'], 0, True)})",
             f"• After {_yrs(pr['years'])}: median Rs {rs(pj['pct'][50])} (bad case Rs {rs(pj['pct'][5])}, good case "
             f"Rs {rs(pj['pct'][95])}) from Rs {rs(pj['paid'])} put in"]
    mo = P.get("model")
    if mo and mo["rows"]:
        lines.append(f"• Stocks ({esc(P['style'])}, tested on PSX): " + ", ".join(r["sym"] for r in mo["rows"]))
        if P.get("etf"):
            lines.append("• For this amount an index ETF is the practical choice (page 4)")
    if P.get("trades"):
        lines.append(f"• Switch plan: {sum(1 for t in P['trades'] if t['side'] == 'Sell')} sales, "
                     f"{sum(1 for t in P['trades'] if t['side'] == 'Buy' and t['qty'])} share purchases, the rest to T-bills and gold (page 5)")
    if pr["notes"]:
        lines += ["", "<i>" + esc("; ".join(pr["notes"])) + "</i>"]
    lines += ["", "<i>Model plan for a risk profile: for information only, not personal investment advice. The PDF has the "
              "5-page plan and the full risk check of the proposed portfolio.</i>"]
    return "\n".join(lines)


_ = RULE  # keeps the palette import complete for future charts
