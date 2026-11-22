"""The portfolio review as an 8-page A4 report (PNG pages for Telegram + one PDF), in the house research style
(note.py). Pages: 1 summary, 2 holdings and exposure, 3 performance and risk statistics, 4 value at risk and
where the risk comes from, 5 stress tests on history, 6 what could happen (scenarios + Monte Carlo),
7 fundamentals, income and events, 8 rule book, other weightings, methods and disclosures.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import note
from ..note import BOTTOM, DOWN, GOLD, GREEN, INK, LIGHT, M, MUTED, RULE, UP, W, Page, px, wrap

PAGES = 9
CW = W - 2 * M                       # content width
COL = (CW - 40) // 2                 # half column
STATUS = {"pass": ("OK", UP), "watch": ("Watch", GOLD), "breach": ("Exceeded", DOWN), "info": ("Info", MUTED)}


def pc(x, d=1, sign=False) -> str:
    if x is None or x != x:
        return "-"
    if abs(x) * 100 < 0.5 * 10 ** -d:
        x = 0.0
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def rs(x, d=0) -> str:
    if x is None or x != x:
        return "-"
    a = abs(x)
    s = f"{a / 1e6:,.2f}m" if a >= 1e7 else f"{a:,.{d}f}"
    return ("-" if x < 0 else "") + s


def nm(x, d=2, suf="") -> str:
    return "-" if x is None or x != x else f"{x:,.{d}f}{suf}"


def _col(x) -> str | None:
    return None if x is None or x != x else (UP if x > 0 else DOWN if x < 0 else None)


def _ax(p: Page, x, y, w, h):
    ax = p.fig.add_axes([x / W, 1 - (y + h) / note.H, w / W, h / note.H])
    ax.tick_params(labelsize=7, colors=MUTED, length=2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(RULE)
    return ax


def _legend(p: Page, x, y, items):
    for lab, col in items:
        p.rect(x, y + 4, 18, 3, col)
        p.text(x + 24, y + 11, lab, 7.5, INK)
        x += 40 + note.text_w(lab, 7.5)
    return y + 22


def _date_ticks(ax, idx: pd.DatetimeIndex, n=6):
    pos = np.linspace(0, len(idx) - 1, n).astype(int)
    ax.set_xticks(pos)
    ax.set_xticklabels([idx[i].strftime("%b %y") for i in pos], fontsize=6.5, color=MUTED, family=note.FONT)


def _title(p: Page, R: dict, title: str) -> float:
    p.text(M, 150, title, 17, GREEN, bold=True)
    return 172


# ---------------------------------------------------------------- page 1
def page1(R: dict, meta: dict) -> Page:
    p = Page(1, PAGES, meta)
    P = R["pos"]
    p.text(M, 160, "Portfolio review", 24, GREEN, bold=True)
    sub = (f"{len(P['rows'])} stocks, value Rs {rs(R['total'])}"
           + (f" incl. cash Rs {rs(P['cash'])}" if P["cash"] else "")
           + f"  |  prices of {meta['price_day']}  |  history since {R['start']:%d %b %Y}")
    p.text(M, 192, sub, 9.5 if note.text_w(sub, 9.5) <= CW else 8.2, MUTED)
    y0 = 222
    # sidebar: key figures
    x, sw = M, note.SIDE_W
    y = p.section(x, y0, sw, "Key figures")
    one, three = R["win"].get("1y") or {}, R["win"].get("3y") or {}
    rel3 = three.get("rel") or one.get("rel") or {}
    v = R["var"]
    fa = R["fund_agg"]
    rows = [["Value", f"Rs {rs(R['total'])}"], ["Stocks / cash", f"{len(P['rows'])} / {pc(P['cash_w'], 0)}"]]
    if R.get("pnl"):
        rows.append(["Unrealised gain", f"Rs {rs(R['pnl']['gain'])} ({pc(R['pnl']['pct'], 0, True)})"])
    rows += [["Beta (3 years)", nm(rel3.get("beta"))],
             ["Volatility, 1 year", pc(one.get("vol"))], ["KSE-100 volatility", pc(one.get("k_vol"))],
             ["Return, 3 years a year", pc(three.get("cagr"))], ["KSE-100 (price)", pc(three.get("k_cagr"))],
             ["Sharpe ratio, 3 years", nm(three.get("sharpe"))], ["Sortino ratio, 3 years", nm(three.get("sortino"))],
             ["Worst fall since " + f"{R['start']:%Y}", pc((R['win'].get('all') or {}).get('dd', {}).get('depth'), 0)],
             ["1-day VaR 95%", f"{pc((v.get(95) or {}).get('hist'))}"],
             ["1-day loss 99% (ES)", f"{pc((v.get(99) or {}).get('es_hist'))}"],
             ["P/E (harmonic)", nm(fa.get("pe"), 1, "x")], ["Dividend yield", pc(fa.get("dy"))],
             ["Dividends after tax", f"Rs {rs(R['income']['net'])} a year" if R["income"]["gross"] else "-"]]
    if R.get("kmi_share") is not None:
        rows.append(["Shariah-compliant", pc(R["kmi_share"], 0)])
    y = p.table(x, y, [sw - 130, 130], rows, size=8.3, bold_first=False)
    # main column: findings, rule check, growth chart
    x, mw = note.MAIN_X, note.MAIN_W
    y = p.section(x, y0, mw, "Main findings")
    y, _ = p.bullets(x, y, R["findings"], mw, size=8.8, gap=6, max_y=1110)
    y = p.section(x, y + 4, mw, "Rule check (details on page 8)")
    order = {"breach": 0, "watch": 1, "pass": 2, "info": 3}
    rr = sorted(R["rules"], key=lambda r: order[r["status"]])[:7]
    rows = [[r["rule"], r["value"], STATUS[r["status"]][0]] for r in rr]
    cols = [[None, None, STATUS[r["status"]][1]] for r in rr]
    y = p.table(x, y, [mw - 360, 260, 100], rows, header=["Check", "Your portfolio", "Status"], size=8,
                colors=cols, max_y=1400)
    # growth chart (price only both sides)
    rp, rm = R["rp_px"], R["rm"]
    n = min(len(rp), int(3 * R["A"]))
    if n > 60 and y < BOTTOM - 180:
        a = (1 + rp.iloc[-n:].fillna(0)).cumprod()
        b = (1 + rm.reindex(a.index).fillna(0)).cumprod()
        yy = p.section(x, y + 4, mw, f"Growth of Rs 100, {'3 years' if n >= 3 * R['A'] * 0.98 else 'since ' + format(a.index[0], '%b %Y')} (price only)")
        h = min(380, BOTTOM - yy - 60)
        p.chart(x, yy, mw, h, a.values, b.values, "Your portfolio (today's weights)", "KSE-100")
        p.text(x, yy + h + 44, "Both lines exclude dividends so they compare like with like; dividends are on page 7.",
               7.3, MUTED)
    return p


# ---------------------------------------------------------------- page 2
def page2(R: dict, meta: dict) -> Page:
    p = Page(2, PAGES, meta)
    y = _title(p, R, "Holdings and exposure")
    P = R["pos"]
    rk = R["risk"].get("pct") or {}
    liq = R["liq"]["rows"]
    tier = meta.get("tier") or {}
    sec = meta.get("sector") or {}
    has_cost = any(r.get("cost") for r in P["rows"])
    head = ["Stock", "Shares", "Price", "Value Rs", "Weight", "Gain" if has_cost else "Index wt", "Sector", "Size",
            "Beta", "Risk", "Sell days"]
    rows, cols = [], []
    for r in P["rows"]:
        s = r["sym"]
        g = (r["price"] / r["cost"] - 1) if r.get("cost") else None
        rows.append([s, f"{r['qty']:,.0f}", nm(r["price"]), rs(r["value"]), pc(r["w"]),
                     pc(g, 0, True) if has_cost else pc(R["kse_w"].get(s), 1) if R["kse_w"].get(s) else "-",
                     (sec.get(s) or "").title()[:22], tier.get(s, "-"), nm(R["betas"].get(s)), pc(rk.get(s), 0),
                     nm(liq.get(s, {}).get("days"), 1)])
        cols.append([None, None, None, None, None, _col(g) if has_cost else None, None, None, None,
                     DOWN if rk.get(s, 0) > 0.2 else None, DOWN if (liq.get(s, {}).get("days") or 0) > 5 else None])
    if P["cash"]:
        rows.append(["Cash", "", "", rs(P["cash"]), pc(P["cash_w"]), "", "", "", "0.00", "0%", "0.0"])
        cols.append(None)
    wd = [78, 84, 80, 98, 70, 80, 230, 62, 56, 60, 88]
    wd[6] += CW - sum(wd)
    al = ["left", "right", "right", "right", "right", "right", "left", "left", "right", "right", "right"]
    y = p.table(M, y + 8, wd, rows, header=head, size=7.8, bold_first=True, colors=cols, max_y=980, align=al)
    y = p.para(M, y, "Risk: each stock's share of the portfolio's volatility (Euler contributions, 2 years of daily "
               "returns). Sell days: position / 20% of the stock's median daily value traded (60 sessions). Size: market-"
               "value rank (Large 1-30, Mid 31-100, Small 101-200, Micro 201+).", CW, size=7.2, color=MUTED)
    # sectors vs KSE-100
    top = max(y, 0) + 8
    yy = p.section(M, top, COL, "Sectors: yours vs the KSE-100")
    sw, ks = R["sector_w"], R["kse_sector_w"]
    names = list(dict.fromkeys(list(sw)[:7] + sorted(ks, key=lambda k: -ks[k])[:10]))[:10]
    h = 30 * len(names) + 30
    ax = _ax(p, M + 150, yy + 22, COL - 160, h)
    ypos = np.arange(len(names))[::-1]
    ax.barh(ypos + 0.18, [sw.get(k, 0) * 100 for k in names], height=0.36, color=GREEN)
    ax.barh(ypos - 0.18, [ks.get(k, 0) * 100 for k in names], height=0.36, color=GOLD)
    ax.set_yticks(ypos)
    ax.set_yticklabels([k.title()[:24] for k in names], fontsize=6.8, color=INK, family=note.FONT)
    ax.set_xlabel("% of equity", fontsize=6.5, color=MUTED)
    _legend(p, M, yy, [("Your portfolio", GREEN), ("KSE-100", GOLD)])
    # concentration + liquidity
    c = R["conc"]
    x2 = M + COL + 40
    y2 = p.section(x2, top, COL, "Concentration")
    rows = [["Largest holding", pc(c["top1"])], ["Largest 5 holdings", pc(c["top5"])],
            ["Herfindahl index (HHI)", nm(c["hhi"], 3)], ["Effective number of holdings", nm(c["eff_n"], 1)],
            ["Active share vs KSE-100", pc(c["active"], 0)], ["In KSE-100 stocks", pc(c["in_kse"], 0)]]
    if R.get("kmi_share") is not None:
        rows.append(["In KMI All Share (Shariah)", pc(R["kmi_share"], 0)])
    y2 = p.table(x2, y2, [COL - 140, 140], rows, size=8.2)
    y2 = p.para(x2, y2, "Active share (Cremers and Petajisto 2009): the part of the portfolio that differs from the index; "
                "above 60% is an active portfolio, below 20% almost an index fund.", COL, size=7.2, color=MUTED) + 8
    y2 = p.section(x2, y2, COL, "How fast could it be sold?")
    b = R["liq"]["buckets"]
    rows = [["Within 1 session", pc(b["1"], 0)], ["2 to 5 sessions", pc(b["5"], 0)], ["6 to 20 sessions", pc(b["20"], 0)],
            ["More than 20 sessions", pc(b["more"], 0)]]
    ffs = [(s, d["ff_pct"]) for s, d in R["liq"]["rows"].items() if d.get("ff_pct")]
    if ffs:
        s, f = max(ffs, key=lambda t: t[1])
        rows.append([f"Largest stake of free float ({s})", pc(f, 3)])
    y2 = p.table(x2, y2, [COL - 140, 140], rows, size=8.2)
    # calendar years
    yrs = R.get("years") or []
    y = max(yy + h + 60, y2 + 10)
    if yrs and y < BOTTOM - 120:
        y = p.section(M, y, CW, "Calendar-year returns (today's holdings at today's weights)")
        rows, cols = [], []
        for e in yrs[-12:]:
            d = e["px"] - e["kse"] if e["px"] is not None and e["kse"] is not None else None
            rows.append([f"{e['year']}" + (f" ({e['partial']})" if e["partial"] else ""), pc(e["tr"], 1, True),
                         pc(e["px"], 1, True), pc(e["kse"], 1, True), pc(d, 1, True)])
            cols.append([None, _col(e["tr"]), _col(e["px"]), _col(e["kse"]), _col(d)])
        p.table(M, y, [CW - 4 * 190, 190, 190, 190, 190], rows,
                header=["Year", "Yours, with dividends", "Yours, price only", "KSE-100", "Difference (price)"],
                size=7.8, colors=cols, max_y=BOTTOM)
    return p


# ---------------------------------------------------------------- page 3
def page3(R: dict, meta: dict) -> Page:
    p = Page(3, PAGES, meta)
    y = _title(p, R, "Performance and risk statistics")
    keys = [k for k in ("1y", "3y", "5y", "all") if k in R["win"]]
    lab = {"1y": "1 year", "3y": "3 years", "5y": "5 years", "all": f"Since {R['start']:%b %Y}"}
    W_ = R["win"]

    def row(name, f, colour=False):
        vals = [f(W_[k]) for k in keys]
        return [name] + vals

    rows = [
        ["RETURN (dividends included)"] + [""] * len(keys),
        row("Total return", lambda s: pc(s["total"], 1, True)),
        row("Return a year (CAGR)", lambda s: pc(s["cagr"], 1, True)),
        row("Return a year, price only", lambda s: pc(s["cagr_px"], 1, True)),
        row("Risk-free rate a year (SBP policy)", lambda s: pc(s["rf"])),
        ["RISK"] + [""] * len(keys),
        row("Volatility a year", lambda s: pc(s["vol"])),
        row("Worst fall (max drawdown)", lambda s: pc(s["dd"].get("depth"), 1)),
        row("Ulcer index", lambda s: pc(s["ulcer"], 1)),
        row("Best / worst month", lambda s: f"{pc(s['best_m'], 0, True)} / {pc(s['worst_m'], 0, True)}"),
        row("Months up", lambda s: pc(s["pos_m"], 0)),
        row("Skewness / excess kurtosis", lambda s: f"{nm(s.get('skew'))} / {nm(s.get('kurt'), 1)}"),
        ["RISK-ADJUSTED"] + [""] * len(keys),
        row("Sharpe ratio", lambda s: nm(s["sharpe"])),
        row("Sortino ratio", lambda s: nm(s["sortino"])),
        row("Calmar ratio", lambda s: nm(s["calmar"])),
        row("Martin ratio", lambda s: nm(s["martin"])),
        row("Omega ratio (vs risk-free)", lambda s: nm(s["omega"])),
        ["AGAINST THE KSE-100 (price only on both sides)"] + [""] * len(keys),
        row("Beta", lambda s: nm(s["rel"].get("beta"))),
        row("Jensen's alpha a year", lambda s: pc(s["rel"].get("alpha"), 1, True)),
        row("Correlation / R squared", lambda s: f"{nm(s['rel'].get('corr'))} / {nm(s['rel'].get('r2'))}"),
        row("Tracking error", lambda s: pc(s["rel"].get("te"))),
        row("Information ratio", lambda s: nm(s["rel"].get("ir"))),
        row("Treynor ratio", lambda s: pc(s["rel"].get("treynor"), 1, True)),
        row("Up capture / down capture", lambda s: f"{nm(s['cap'].get('up'), 0)} / {nm(s['cap'].get('down'), 0)}"),
        row("Months beating the index", lambda s: pc(s["cap"].get("batting"), 0)),
        ["KSE-100 OVER THE SAME PERIOD"] + [""] * len(keys),
        row("Return a year (price index)", lambda s: pc(s["k_cagr"], 1, True)),
        row("Volatility a year", lambda s: pc(s["k_vol"])),
        row("Worst fall", lambda s: pc(s["k_dd"], 1)),
        row("Sharpe ratio", lambda s: nm(s["k_sharpe"])),
    ]
    first = 330
    wd = [first] + [(CW - first) // len(keys)] * len(keys)
    wd[-1] += CW - sum(wd)
    heads = {i for i, r in enumerate(rows) if all(c == "" for c in r[1:])}
    cols = [[GREEN] * len(r) if i in heads else None for i, r in enumerate(rows)]
    y = p.table(M, y + 8, wd, rows, header=["Measure"] + [lab[k] for k in keys], size=7.6, colors=cols, max_y=1420)
    # drawdown chart since the start
    a = R["rp_px"]
    dd = (1 + a.fillna(0)).cumprod()
    dd = dd / dd.cummax() - 1
    m = (1 + R["rm"].reindex(a.index).fillna(0)).cumprod()
    md = m / m.cummax() - 1
    yy = p.section(M, y + 6, CW, f"Falls from the previous high since {R['start']:%b %Y} (price only)")
    yy = _legend(p, M, yy, [("Your portfolio", GREEN), ("KSE-100", GOLD)])
    h = BOTTOM - yy - 40
    if h > 90:
        ax = _ax(p, M + 40, yy, CW - 50, h)
        ax.fill_between(np.arange(len(dd)), dd.values * 100, 0, color=GREEN, alpha=0.35, lw=0)
        ax.plot(np.arange(len(md)), md.values * 100, color=GOLD, lw=0.9)
        ax.set_xlim(0, len(dd) - 1)
        _date_ticks(ax, dd.index, 8)
        ax.set_ylabel("%", fontsize=6.5, color=MUTED)
    return p


# ---------------------------------------------------------------- page 4
def page4(R: dict, meta: dict) -> Page:
    p = Page(4, PAGES, meta)
    y = _title(p, R, "Value at Risk and where the risk comes from")
    v, tot = R["var"], R["total"]
    if v.get(95):
        rows = []
        for name, key in (("Historical (last 2 years)", "hist"), ("Normal distribution", "normal"),
                          ("Cornish-Fisher (skew and fat tails)", "cf"), ("EWMA, RiskMetrics (recent volatility)", "ewma")):
            a, b = v[95].get(key), v[99].get(key)
            rows.append([name, pc(a, 2), rs(a * tot) if a else "-", pc(b, 2), rs(b * tot) if b else "-",
                         pc(b * np.sqrt(10), 1) if b else "-"])
        rows.append(["Expected shortfall, historical", pc(v[95]["es_hist"], 2), rs(v[95]["es_hist"] * tot),
                     pc(v[99]["es_hist"], 2), rs(v[99]["es_hist"] * tot), pc(v[99]["es_hist"] * np.sqrt(10), 1)])
        rows.append(["Expected shortfall, normal", pc(v[95]["es_normal"], 2), rs(v[95]["es_normal"] * tot),
                     pc(v[99]["es_normal"], 2), rs(v[99]["es_normal"] * tot), pc(v[99]["es_normal"] * np.sqrt(10), 1)])
        wd = [330, 140, 150, 140, 150, CW - 910]
        y = p.table(M, y + 8, wd, rows, header=["1-day loss not exceeded on", "95% of days", "Rs", "99% of days", "Rs",
                                                "10 days, 99%"], size=8, max_y=700)
        k99, k95 = v.get("kupiec99") or {}, v.get("kupiec95") or {}
        txt = ("VaR = the loss a bad day should not exceed at that confidence; expected shortfall = the average loss on the "
               "days beyond it (the measure Basel III prefers). 10-day figures scale by the square root of 10. Cornish-"
               "Fisher corrects the normal figure for skew and fat tails and can overshoot when tails are very fat. ")
        if k99:
            txt += (f"Backtest of the historical VaR over the last 250 sessions: {k99['breaches']} days lost more than the 99% "
                    f"VaR (2.5 expected; Basel traffic light {k99.get('zone', '-')}), {k95.get('breaches', '-')} beyond the 95% VaR "
                    f"(12.5 expected); Kupiec test {'passed' if k99.get('ok') else 'failed'} at 99%.")
        y = p.para(M, y, txt, CW, size=7.6, color=MUTED) + 8
    rk = R["risk"]
    if rk.get("pct"):
        inc = rk["included"]
        yy = p.section(M, y, COL, "Weight vs share of risk")
        yy = _legend(p, M, yy, [("Weight", GOLD), ("Share of risk", GREEN)])
        order = sorted(inc, key=lambda s: -rk["pct"][s])[:14]
        h = 26 * len(order) + 30
        ax = _ax(p, M + 70, yy, COL - 80, h)
        ypos = np.arange(len(order))[::-1]
        ax.barh(ypos + 0.18, [R["weights"][s] * 100 for s in order], height=0.36, color=GOLD)
        ax.barh(ypos - 0.18, [rk["pct"][s] * 100 for s in order], height=0.36, color=GREEN)
        ax.set_yticks(ypos)
        ax.set_yticklabels(order, fontsize=7, color=INK, family=note.FONT)
        ax.axvline(0, color=RULE, lw=0.6)
        x2 = M + COL + 40
        y2 = p.section(x2, y, COL, "Diversification")
        rows = [["Portfolio volatility (2 years)", pc(rk["sigma"])],
                ["Average volatility of its stocks", pc(sum(R["weights"][s] * rk["own_vol"][s] for s in inc)
                                                        / max(1e-9, sum(R["weights"][s] for s in inc)))],
                ["Diversification ratio", nm(rk["dr"])], ["Effective number of bets (PCA)", nm(rk["enb"], 1)],
                ["Average correlation between holdings", nm(rk.get("avg_corr"))],
                ["Market (systematic) share of risk", pc(rk.get("systematic"), 0)],
                ["Stock-specific share of risk", pc(1 - rk["systematic"], 0) if rk.get("systematic") is not None else "-"]]
        y2 = p.table(x2, y2, [COL - 130, 130], rows, size=8.2)
        y2 = p.para(x2, y2, "Diversification ratio = weighted average of the stocks' own volatilities / the portfolio's "
                    "(1.0 = no benefit). Effective bets (Meucci 2009) counts independent sources of risk; in a single "
                    "market the market itself is usually by far the biggest, so 1-3 is common.", COL, size=7.2, color=MUTED)
        y = max(yy + h + 30, y2 + 10)
        corr = rk.get("corr")
        if corr is not None and len(inc) > 1 and y < BOTTOM - 260:
            yy = p.section(M, y, CW, "Correlation between holdings (daily returns, 2 years)")
            n = len(inc)
            side = min(BOTTOM - yy - 30, 40 * n + 60, 620)
            ax = _ax(p, M + 70, yy + 4, side, side)
            from matplotlib.colors import LinearSegmentedColormap
            cmap = LinearSegmentedColormap.from_list("pi", ["#ffffff", "#cfe3d4", GREEN])
            ax.imshow(corr.values, cmap=cmap, vmin=0, vmax=1)
            ax.set_xticks(range(n))
            ax.set_yticks(range(n))
            ax.set_xticklabels(inc, rotation=90, fontsize=6.5, family=note.FONT)
            ax.set_yticklabels(inc, fontsize=6.5, family=note.FONT)
            if n <= 14:
                for i in range(n):
                    for j in range(n):
                        ax.text(j, i, f"{corr.values[i, j]:.2f}", ha="center", va="center", fontsize=5.8,
                                color="white" if corr.values[i, j] > 0.6 else INK, family=note.FONT)
            xx = M + 110 + side
            pr = R.get("pairs") or []
            p.para(xx, yy + 20, ("Pairs above 0.8 (they behave almost as one): " +
                                 ", ".join(f"{a}/{b} {c:.2f}" for a, b, c in pr[:6])) if pr else
                   "No pair of holdings is correlated above 0.8.", CW - side - 120, size=8)
            p.para(xx, yy + 80, f"Covariance used for the alternatives on page 8: Ledoit-Wolf shrinkage to constant "
                   f"correlation, intensity {rk['shrink']:.2f} ({rk['n_obs']} sessions).", CW - side - 120, size=7.4,
                   color=MUTED)
    return p


# ---------------------------------------------------------------- page 5
def page5(R: dict, meta: dict) -> Page:
    p = Page(5, PAGES, meta)
    y = _title(p, R, "Stress tests: the past")
    y = p.para(M, y + 6, "Today's holdings and weights, bought at each market top and held to the bottom. A stock that "
               "was not listed yet is represented by its beta x the KSE-100 (coverage = share of the portfolio with real "
               "prices). Price only on both sides.", CW, size=8, color=MUTED) + 4
    eps = R["episodes"]
    rows, cols = [], []
    for e in eps:
        d = e["ret"] - e["idx"]
        rows.append([e.get("name") or f"Fall of {e['peak']:%b %Y}", f"{e['peak']:%d %b %y} - {e['trough']:%d %b %y}",
                     pc(e["idx"], 0), pc(e["ret"], 0), pc(d, 0, True), pc(e["worst"], 0), pc(e["coverage"], 0),
                     (f"{e['recovered']:%b %Y}" if e.get("recovered") is not None else ("inside a longer fall" if e.get("inside") else "not yet"))])
        cols.append([None, None, DOWN, DOWN, _col(d), None, None, None])
    wd = [280, 180, 85, 80, 105, 100, 95, CW - 925]
    al = ["left", "left"] + ["right"] * 5 + ["left"]
    y = p.table(M, y, wd, rows, header=["Crisis (KSE-100 fall of 15%+)", "Top to bottom", "KSE-100", "Yours",
                                        "Difference", "Your worst", "Coverage", "Index regained"], size=7.6, colors=cols,
                max_y=760, align=al)
    if eps:
        yy = p.section(M, y + 2, COL, "Crisis losses: yours vs the KSE-100")
        yy = _legend(p, M, yy, [("Your portfolio", GREEN), ("KSE-100", GOLD)])
        h = 230
        ax = _ax(p, M + 30, yy, COL - 40, h)
        xs = np.arange(len(eps))
        ax.bar(xs - 0.2, [e["ret"] * 100 for e in eps], width=0.4, color=GREEN)
        ax.bar(xs + 0.2, [e["idx"] * 100 for e in eps], width=0.4, color=GOLD)
        ax.set_xticks(xs)
        ax.set_xticklabels([f"{e['peak']:%b %y}" for e in eps], fontsize=6.5, family=note.FONT, color=MUTED)
        ax.axhline(0, color=RULE, lw=0.6)
        ws = R.get("stress_worst")
        x2 = M + COL + 40
        y2 = p.section(x2, y + 2, COL, "Who hurt most in the worst one")
        if ws:
            st = sorted(ws["stocks"].items(), key=lambda kv: kv[1]["ret"])
            rows = [[s, pc(d["ret"], 0), pc(R["weights"][s], 1), "price" if d["real"] else "beta proxy"] for s, d in st[:8]]
            y2 = p.table(x2, y2, [110, 110, 110, COL - 330], rows, header=["Stock", "Return", "Weight", "Basis"],
                         size=7.8)
            p.para(x2, y2, ws.get("name") or f"{ws['peak']:%b %Y} to {ws['trough']:%b %Y}", COL, size=7.4, color=MUTED)
        y = max(yy + h + 26, y2 + 30)
    # worst days + rate cycles side by side
    yy = p.section(M, y, COL, "The KSE-100's worst days")
    rows = [[f"{d['date']:%d %b %Y}", pc(d["idx"], 1), pc(d["ret"], 1), pc(d["ret"] - d["idx"], 1, True)]
            for d in R["worst_days"]]
    cols = [[None, DOWN, _col(d["ret"]), _col(d["ret"] - d["idx"])] for d in R["worst_days"]]
    p.table(M, yy, [COL - 300, 100, 100, 100], rows, header=["Day", "KSE-100", "Yours", "Difference"], size=7.8,
            colors=cols)
    x2 = M + COL + 40
    y2 = p.section(x2, y, COL, "SBP rate cycles")
    rows, cols = [], []
    for c in R["cycles"]:
        d = c["ret"] - c["idx"]
        rows.append([f"{'Hikes' if c['kind'] == 'hikes' else 'Cuts'} {c['from']:g}% to {c['to']:g}%",
                     f"{pd.Timestamp(c['start']):%b %y} - {pd.Timestamp(c['end']):%b %y}", pc(c["idx"], 0), pc(c["ret"], 0),
                     pc(d, 0, True)])
        cols.append([None, None, _col(c["idx"]), _col(c["ret"]), _col(d)])
    y2 = p.table(x2, y2, [150, 135, 75, 70, COL - 430], rows, header=["Cycle", "Period", "KSE-100", "Yours", "Diff."],
                 size=7.4, colors=cols, max_y=BOTTOM, align=["left", "left", "right", "right", "right"])
    p.para(x2, y2, "Each cycle runs from the first to the last move of a run of SBP hikes or cuts totalling 3 points or "
           "more.", COL, size=7, color=MUTED)
    return p


# ---------------------------------------------------------------- page 6
def page6(R: dict, meta: dict) -> Page:
    p = Page(6, PAGES, meta)
    y = _title(p, R, "Stress tests: what could happen")
    fm = R["fm"]
    txt = (f"Each stock's monthly return is explained by the KSE-100, the rupee (USD/PKR) and Brent crude over the last "
           f"{fm['n']} months (regression); a shock to one factor moves the others as they have usually moved with it "
           "(correlated stress test, as in MSCI Barra). The 90% range adds the stocks' own, unexplained moves."
           if len(fm["keys"]) == 3 else
           f"Each stock's monthly return is explained by the KSE-100 over the last {fm['n']} months (rupee and oil data "
           "were not available this time).")
    y = p.para(M, y + 6, txt, CW, size=8, color=MUTED) + 4
    rows, cols = [], []
    for s in R["scenarios"]:
        worst = sorted(s["stocks"].items(), key=lambda kv: kv[1])[:2] if s["ret"] < 0 else \
            sorted(s["stocks"].items(), key=lambda kv: -kv[1])[:2]
        rows.append([s["name"], s["desc"], pc(s["ret"], 1, True), rs(s["ret"] * R["total"]),
                     f"{pc(s['ret'] - s['band'], 0, True)} to {pc(s['ret'] + s['band'], 0, True)}",
                     ", ".join(f"{k} {pc(v, 0, True)}" for k, v in worst)])
        cols.append([None, None, _col(s["ret"]), _col(s["ret"]), None, None])
    wd = [190, 330, 90, 110, 150, CW - 870]
    y = p.table(M, y, wd, rows, header=["Scenario", "Shock", "Portfolio", "Rs", "90% range", "Moves most"], size=7.6,
                colors=cols, max_y=820, align=["left", "left", "right", "right", "right", "left"])
    # factor exposures per stock
    B = fm["B"]
    if B and len(fm["keys"]) == 3:
        y = p.section(M, y + 2, CW, "Sensitivities (monthly regression)")
        rows = [[s, nm(b["mkt"]), nm(b["usd"]), nm(b["oil"]), pc(b["resid"], 1), str(b["n"])]
                for s, b in sorted(B.items(), key=lambda kv: -R["weights"].get(kv[0], 0)) if s in R["weights"]][:12]
        y = p.table(M, y, [150, 190, 190, 190, 190, CW - 910], rows,
                    header=["Stock", "KSE-100 beta", "Dollar (USD/PKR)", "Brent crude", "Own monthly moves", "Months"],
                    size=7.6, max_y=1120)
    mc = R["mc"]
    nrm, cri = mc.get("normal"), mc.get("crisis")
    if nrm:
        yy = p.section(M, y + 2, COL, "Next 12 months: 10,000 simulated paths")
        yy = _legend(p, M, yy, [("median", GREEN), ("50% of paths", "#a9cdb3"), ("90% of paths", LIGHT)])
        h = min(320, BOTTOM - yy - 70)
        fan = nrm["fan"]
        ax = _ax(p, M + 40, yy, COL - 50, h)
        xs = np.arange(len(fan[50]))
        ax.fill_between(xs, (fan[5] - 1) * 100, (fan[95] - 1) * 100, color=LIGHT, lw=0)
        ax.fill_between(xs, (fan[25] - 1) * 100, (fan[75] - 1) * 100, color="#a9cdb3", lw=0)
        ax.plot(xs, (fan[50] - 1) * 100, color=GREEN, lw=1.4)
        ax.axhline(0, color=RULE, lw=0.7)
        ax.set_xlim(0, len(xs) - 1)
        ax.set_xticks([0, len(xs) // 4, len(xs) // 2, 3 * len(xs) // 4, len(xs) - 1])
        ax.set_xticklabels(["now", "3m", "6m", "9m", "12m"], fontsize=6.5, color=MUTED)
        ax.set_ylabel("% gain / loss", fontsize=6.5, color=MUTED)
        x2 = M + COL + 40
        y2 = p.section(x2, y + 2, COL, "Outcomes after 12 months")

        def col(s, k):
            return pc(s["pct"][k], 0, True) if s else "-"
        rows = [["Worst 1 in 100", col(nrm, 1), col(cri, 1)], ["Worst 1 in 20", col(nrm, 5), col(cri, 5)],
                ["1 in 4 below", col(nrm, 25), col(cri, 25)], ["Median", col(nrm, 50), col(cri, 50)],
                ["1 in 4 above", col(nrm, 75), col(cri, 75)], ["Best 1 in 20", col(nrm, 95), col(cri, 95)],
                ["Chance of any loss", pc(nrm["p_loss"], 0), pc(cri["p_loss"], 0) if cri else "-"],
                ["Chance of losing 20%+", pc(nrm["p_loss20"], 0), pc(cri["p_loss20"], 0) if cri else "-"],
                ["Typical worst fall inside the year", pc(nrm["mdd_med"], 0), pc(cri["mdd_med"], 0) if cri else "-"],
                ["Worst fall, 1 in 20", pc(nrm["mdd_95"], 0), pc(cri["mdd_95"], 0) if cri else "-"]]
        y2 = p.table(x2, y2, [COL - 230, 115, 115], rows, header=["", "Normal times", "Crisis times"], size=7.8)
        p.para(x2, y2, f"Normal times: blocks of real daily returns from the last {nrm['n']} sessions (stationary bootstrap, "
               f"Politis-Romano), re-centred on the expected return {pc(mc['exp'], 1)} a year = risk-free "
               f"{mc['rf_now']:.2f}% (10-year PIB) + beta {mc['beta_p']:.2f} x {mc['erp']:.0f}% equity premium (CAPM). "
               + (f"Crisis times: drawn only from the KSE-100's bear-market days ({cri['n']} sessions), their own "
                  "average kept." if cri else ""), COL, size=7.1, color=MUTED)
    return p


# ---------------------------------------------------------------- page 7
def page7(R: dict, meta: dict) -> Page:
    p = Page(7, PAGES, meta)
    y = _title(p, R, "Fundamentals, income and what is coming up")
    rows, cols = [], []
    for f in R["fund"]:
        res = f.get("result") or {}
        rows.append([f["sym"], nm(f.get("pe"), 1, "x") if f.get("pe") else ("loss" if (f.get("ttm") or 0) < 0 else "-"),
                     nm(f.get("pb"), 2, "x"), pc(f.get("dy"), 1), nm(f.get("roe"), 0, "%"),
                     nm(f.get("de"), 0, "%") if not f.get("financial") else "bank/fin.",
                     f"{res.get('label', '-')}{res.get('words', '')}",
                     f"{pc(f.get('up_stay'), 0, True)} / {pc(f.get('up_fade'), 0, True)}" if f.get("up_stay") is not None else "-",
                     "; ".join(f.get("flags") or []) or "-"])
        cols.append([None, None, None, None, None, None, None, _col(f.get("up_stay")), DOWN if f.get("flags") else None])
    fa = R["fund_agg"]
    rows.append(["Portfolio", nm(fa.get("pe"), 1, "x"), nm(fa.get("pb"), 2, "x"), pc(fa.get("dy"), 1), nm(fa.get("roe"), 0, "%"),
                 "", "", f"{pc(fa.get('up_stay'), 0, True)} / {pc(fa.get('up_fade'), 0, True)}" if fa.get("up_stay") is not None else "-", ""])
    cols.append([GREEN] * 9)
    wd = [86, 70, 70, 76, 60, 86, 230, 140, CW - 818]
    al = ["left"] + ["right"] * 5 + ["left", "right", "left"]
    y = p.table(M, y + 8, wd, rows, header=["Stock", "P/E", "P/B", "Yield", "ROE", "Debt/eq.", "Latest result (vs a year ago)",
                                            "Fair value gap", "Flags"], size=7.6, colors=cols, bold_first=True, max_y=900,
                align=al)
    y = p.para(M, y, "P/E on the checked last-4-quarter EPS; P/B on book value brought up to date for results since the "
               "last balance sheet. Portfolio P/E and P/B are harmonic weighted averages (as index providers compute them). "
               "Fair value gap = model value vs price, returns stay / returns fade to the required return over 10 years "
               f"(required return {R['coe'] * 100:.2f}%; model figures, not targets).", CW, size=7.2, color=MUTED) + 6
    # income and tax
    yy = p.section(M, y, COL, "Dividend income and tax")
    inc = R["income"]
    rows = [["Dividends a year (last full-year payouts)", f"Rs {rs(inc['gross'])}"],
            ["Tax withheld at 15% (filers)", f"Rs {rs(inc['gross'] * 0.15)}"], ["Received after tax", f"Rs {rs(inc['net'])}"],
            ["Yield on the portfolio, after tax", pc(inc["yield"] * 0.85 if inc.get("yield") else None)]]
    if R.get("pnl"):
        pn = R["pnl"]
        rows += [["Unrealised gain (holdings with cost)", f"Rs {rs(pn['gain'])} ({pc(pn['pct'], 0, True)})"],
                 ["Capital gains tax if sold now (15%)", f"Rs {rs(pn['cgt'])}"]]
    yy = p.table(M, yy, [COL - 160, 160], rows, size=8)
    yy = p.para(M, yy, "Tax rates for persons on the Active Taxpayers List: 15% on dividends (30% otherwise; 25% from "
                "companies paying no tax), 15% on gains for shares bought from July 2024 (older lots: by holding period). "
                "Check with your tax adviser.", COL, size=7.1, color=MUTED) + 8
    rfn = (R.get("mc") or {}).get("rf_now")
    if fa.get("pe") and rfn:
        yy = p.section(M, yy, COL, "Earnings yield against government bonds")
        ey = 1 / fa["pe"]
        rows = [["Portfolio earnings yield (1 / P/E)", pc(ey)], ["10-year PIB yield", f"{rfn:.2f}%"],
                ["Gap", pc(ey - rfn / 100, 1, True)], ["Dividend yield after tax", pc((fa.get("dy") or 0) * 0.85)]]
        yy = p.table(M, yy, [COL - 160, 160], rows, size=8)
        p.para(M, yy, "Shares earn their keep through growth: a yield gap below zero means the price assumes the "
               "profits will grow (or rates will fall).", COL, size=7.1, color=MUTED)
    probs = [f"{f['sym']}: {f['problems'][0]}" for f in R["fund"] if f.get("problems")]
    if probs:
        y2_ = BOTTOM - 120
        p.section(M, y2_, CW, "Data checks")
        p.para(M, y2_ + 38, "Fair value not shown where a check failed: " + "; ".join(probs[:4]), CW, size=7.4, color=MUTED)
    x2 = M + COL + 40
    y2 = p.section(x2, y, COL, "Coming up (30 days)")
    ev = R["events"]
    if ev:
        rows = [[f"{e['date']:%a %d %b}", e["sym"], e["what"]] for e in ev[:10]]
        y2 = p.table(x2, y2, [110, 80, COL - 190], rows, size=7.8)
    else:
        y2 = p.para(x2, y2, "No board meetings or ex-dates announced for these stocks in the next 30 days.", COL, size=8)
    lab = R.get("lab") or {}
    if lab:
        y2 = p.section(x2, y2 + 6, COL, "Research Lab levels (last evening)")
        rows = []
        for s, it in lab.items():
            z = it.get("zone") or [None, None]
            rows.append([s, (it.get("label") or "").title(), f"{nm(z[0])}-{nm(z[1])}" if z[0] else "-", nm(it.get("risk")),
                         nm(it.get("r1"))])
        p.table(x2, y2, [70, 130, 130, 90, COL - 420], rows, header=["Stock", "Setup", "Buy zone", "Fails below", "Target 1"],
                size=7.4)
    return p


# ---------------------------------------------------------------- page 8
METHODS = ("Methods. Weights are today's market values; history = today's holdings at today's weights, rebalanced daily, "
           "from the listing of the youngest holding. Prices: PSX closes adjusted with PSX's reference prices; total return "
           "includes cash dividends, price return excludes them (dividends identified from the announced payouts and "
           "ex-dates). Risk-free rate: SBP policy rate in force each day. Sessions per year measured from the KSE-100. "
           "Sharpe (1994), Sortino-Price (1994), Calmar, Ulcer index and Martin ratio (Martin 1989), Omega (Keating-"
           "Shadwick 2002), Jensen's alpha, Treynor, tracking error and information ratio; up/down capture and batting "
           "average on monthly returns as Morningstar computes them. VaR: historical, normal, Cornish-Fisher (Favre-"
           "Galeano 2002), EWMA lambda 0.94 (RiskMetrics 1996); expected shortfall; Kupiec (1995) test and Basel traffic "
           "light. Risk contributions: Euler decomposition. Covariance for the alternatives: Ledoit-Wolf (2003) shrinkage. "
           "Diversification ratio (Choueifaty 2008), effective number of bets (Meucci 2009), active share (Cremers-"
           "Petajisto 2009), Blume (1971) adjusted betas for proxies and scenarios. Stress: KSE-100 falls of 15%+ found in "
           "the data since 2008, plus named crises inside longer falls; macro scenarios from a monthly 3-factor regression "
           "with correlated shocks (conditional expectation); Monte Carlo: stationary bootstrap (Politis-Romano 1994), "
           "10,000 paths. Limits: past relationships can break; the KSE-100 is a price index; free data, checked but not "
           "audited.")


def page8(R: dict, meta: dict) -> Page:
    p = Page(8, PAGES, meta)
    y = _title(p, R, "Rule book, other weightings, methods")
    srcs = list(dict.fromkeys(r["src"] for r in R["rules"]))
    rows, cols = [], []
    for r in R["rules"]:
        lab, c = STATUS[r["status"]]
        rows.append([r["rule"], r["limit"], r["value"], lab, str(srcs.index(r["src"]) + 1)])
        cols.append([None, None, None, c, MUTED])
    y = p.table(M, y + 8, [290, 310, 340, 100, CW - 1040], rows, header=["Check", "Yardstick", "Your portfolio", "Status", "Src"],
                size=7.6, colors=cols, max_y=900, align=["left", "left", "left", "left", "right"])
    from .rules import SOURCES
    src_txt = "  ".join(f"[{i + 1}] {SOURCES[s]}." for i, s in enumerate(srcs))
    y = p.para(M, y, "Sources: " + src_txt + " Fund limits are yardsticks for a private investor, not obligations.",
               CW, size=6.8, color=MUTED, lead=1.35) + 6
    alts = R.get("alts") or {}
    if alts:
        y = p.section(M, y, CW, "The same stocks with other weights (not a recommendation)")
        rows = []
        for name, d in alts.items():
            rows.append([name, pc(d["vol"]), nm(d["beta"]), nm(d["dr"]), pc(d["max_risk"], 0), pc(d["top_w"], 0),
                         pc(d["cagr5"], 1, True), pc(d["dd5"], 0)])
        cols = [[GREEN] * 8] + [None] * (len(rows) - 1)
        y = p.table(M, y, [250, 110, 80, 110, 120, 110, 160, CW - 940], rows,
                    header=["Weights", "Volatility", "Beta", "Div. ratio", "Most risk", "Largest", "5y return a yr", "5y worst fall"],
                    size=7.6, colors=cols, max_y=1260)
        cur = alts.get("Your weights", {}).get("w", {})
        names = [k for k in alts if k != "Your weights"]
        top = sorted(cur, key=lambda s: -cur[s])[:10]
        rows = [[s, pc(cur[s], 0)] + [pc(alts[k]["w"].get(s), 0) for k in names] for s in top]
        y = p.table(M, y, [120, 140] + [(CW - 260) // len(names)] * len(names), rows,
                    header=["Stock", "Yours"] + names, size=7.3, max_y=1420)
        y = p.para(M, y, f"Risk model: Ledoit-Wolf covariance of 2 years of daily total returns; long only, at most "
                   f"{R.get('cap', 0.15) * 100:.0f}% per stock (except equal risk contribution). The 5-year figures are what "
                   "these weights would have done, which favours the weights chosen with hindsight of the same data.",
                   CW, size=7.1, color=MUTED) + 4
    y = max(y + 6, BOTTOM - 250)
    p.rule(M, y, W - M, color=GREEN, lw=1)
    y = p.para(M, y + 10, METHODS, CW, size=6.6, color=MUTED, lead=1.33)
    p.para(M, y + 4, "Private review prepared on request by PAKISTAN INVESTORS from public PSX, SBP and market data. For "
           "information only, not investment advice or a recommendation to buy or sell; past results and simulations do "
           "not guarantee future results.", CW, size=6.6, color=INK, lead=1.33)
    return p


ACTION_COL = {"Trim": GOLD, "Replace": DOWN, "Review": DOWN, "Add": UP, "Rebalance": GOLD, "Reduce slowly": GOLD}


def page9(R: dict, meta: dict) -> Page:
    p = Page(9, PAGES, meta)
    y = _title(p, R, "Style check and suggested changes")
    ex = R.get("exposure") or {}
    style = ex.get("style", "Core growth")
    from .select import TESTED
    t = TESTED[style]
    y = p.para(M, y + 6, f"Each holding is placed among today's 120 most-traded PSX stocks on the inputs that were tested on "
               f"PSX 2010-2026 ({'earnings growth, momentum and steadiness' if style == 'Core growth' else 'earnings growth, steadiness and dividend yield'}). "
               f"The 20 best-scored stocks, rebuilt every 3 months, beat the market by {t['excess'] * 100:.1f}% a year "
               f"(better in {t['years']} years) with a worst fall of {t['mdd'] * 100:.0f}% against {t['umdd'] * 100:.0f}%. "
               "Percentiles: 100 = best of the 120, 50 = average.", CW, size=7.8, color=MUTED) + 4
    rows, cols = [], []
    for e in ex.get("rows") or []:
        if not e.get("in"):
            rows.append([e["sym"], "-", "-", "-", "-", "outside the 120 most traded", ""])
            cols.append(None)
            continue
        f = lambda v: "-" if v != v else f"{v * 100:.0f}"  # noqa: E731
        sc = e["score"]
        verdict = "top 30%" if sc >= 0.70 else "bottom 30%" if sc < 0.30 else "middle"
        rows.append([e["sym"], f(e["gro"]), f(e["mom"]), f(e["lowvol"]), f(e["dy"]), f(sc), verdict])
        cols.append([None, None, None, None, None, UP if sc >= 0.7 else DOWN if sc < 0.3 else None,
                     UP if sc >= 0.7 else DOWN if sc < 0.3 else None])
    a = ex.get("avg") or {}
    if a:
        g = lambda k: f"{a.get(k, 0) * 100:.0f}"  # noqa: E731
        rows.append(["Portfolio", g("gro"), g("mom"), g("lowvol"), g("dy"), g("score"),
                     f"covers {ex.get('covered', 0) * 100:.0f}% of the stocks' value"])
        cols.append([GREEN] * 7)
    if rows:
        y = p.table(M, y, [120, 140, 130, 140, 130, 140, CW - 800], rows,
                    header=["Stock", "Earnings growth", "Momentum", "Steadiness", "Div. yield", style, "Verdict"],
                    size=7.6, colors=cols, max_y=820, align=["left"] + ["right"] * 5 + ["left"])
    y = p.section(M, y + 4, CW, "Suggested changes")
    adv = R.get("advice") or []
    if adv:
        rows = [[a_["action"], a_["sym"], a_["detail"], a_["why"], a_["evidence"]] for a_ in adv[:14]]
        cols = [[ACTION_COL.get(a_["action"]), None, None, None, MUTED] for a_ in adv[:14]]
        y = p.table(M, y, [100, 110, 270, 260, CW - 740], rows, header=["Action", "Stock", "Change", "Why", "Evidence"],
                    size=7.4, colors=cols, max_y=BOTTOM - 170, align=["left"] * 5)
    else:
        y = p.para(M, y, "No change needed by the rules and tests used here.", CW, size=9)
    p.para(M, max(y + 6, BOTTOM - 150),
           "How these suggestions are made: limits first (fund yardsticks, risk share), then quality, then the tested score "
           "(a holding in the bottom 30% is matched with the best-scored stock of the same sector that is in the top 30%), "
           "then diversification and, when your plan is known (/plan), your loss limit. Scores use prices and results "
           "available today only. Sales of shares bought from July 2024 carry 15% capital gains tax for filers; a loss can "
           "be set off against gains on listed shares and carried forward 3 years. These are model suggestions from public "
           "data and tested rules, not personal investment advice; past results do not guarantee future results.",
           CW, size=7, color=MUTED, lead=1.35)
    return p


def build(R: dict, meta: dict) -> tuple[list[bytes], bytes]:
    meta = {"band": "PORTFOLIO REVIEW  |  PRIVATE", "tag": "Prepared on request",
            "foot": "PAKISTAN INVESTORS  |  Private portfolio review, for information only, not investment advice. Methods on page 8.",
            **meta}
    pages = []
    for f in (page1, page2, page3, page4, page5, page6, page7, page8, page9):
        pages.append(f(R, meta))
    return note._finish(pages, "Portfolio review")


def summary_text(R: dict, meta: dict) -> str:
    """The Telegram message that goes with the report."""
    from ..common import esc
    P = R["pos"]
    br = [r for r in R["rules"] if r["status"] == "breach"]
    lines = [f"<b>Portfolio review</b> · {esc(meta['price_day'])}",
             f"{len(P['rows'])} stocks, Rs {rs(R['total'])}" + (f" (cash {pc(P['cash_w'], 0)})" if P["cash"] else ""), ""]
    lines += [f"• {esc(f)}" for f in R["findings"]]
    if br:
        lines += ["", "<b>Limits exceeded</b>"] + [f"• {esc(r['rule'])}: {esc(r['value'])} (yardstick {esc(r['limit'])})" for r in br]
    if P["missing"]:
        lines += ["", f"Not included (no price or history found): {esc(', '.join(P['missing']))}"]
    if P["notes"]:
        lines += ["", "<i>" + esc("; ".join(P["notes"])) + "</i>"]
    lines += ["", "<i>Full 9-page report in the PDF. For information only, not investment advice.</i>"]
    return "\n".join(lines)


_ = wrap, px  # re-exported helpers used by callers
