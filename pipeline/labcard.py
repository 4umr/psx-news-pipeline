"""One-page Research Lab card (experiment; private test chat only). Uses the brand Card from cards.py."""
from __future__ import annotations

from datetime import datetime

import numpy as np

from .cards import CREAM, DOWN, FLAT, GOLD, UP, Card, _col, _tri, _wrap, clean, plt, sp

VIEW_COL = {"Outperform": UP, "Underperform": DOWN, "Neutral": "#b8862a"}


def _f(x, u="x", d=1) -> str:
    return "n/a" if x is None else f"{x:,.{d}f}{u}"


def idx_now(d: dict, px: list) -> float | None:
    return (d.get("index_map") or {}).get(px[-1][0]) if px else None


def _draw(c: Card, d: dict, call: dict, sb: dict, session: str) -> int:
    from .lab import record_line
    v, t = d["snap"]["values"], c.t
    y = 160
    c.chip(c.L, y, d.get("kicker") or "Research Lab · experiment", "#55665b")
    c.text(c.R, y, sp(datetime.strptime(session, "%Y-%m-%d").strftime("%a %d %b %Y")), 11, t["muted"], ha="right")
    y += 40
    c.text(c.L, y + 44, d["sym"], 64, CREAM, bold=True, serif=True)
    c.text(c.L + 2, y + 104, clean(d["name"]).replace(" Consolidated", "")[:48], 26, t["title2"], bold=True, serif=True)
    chg = v.get("chg_pct")
    c.text(c.R, y + 40, f"Rs {v.get('price', 0):,.2f}", 34, CREAM, bold=True, ha="right")
    if chg is not None:
        c.text(c.R, y + 84, f"{_tri(chg)} {chg:+.2f}% today", 15, _col(chg), bold=True, ha="right")
    c.text(c.L + 4, y + 146, sp(clean(d["sector"]).title()[:44]), 11.5, t["muted"])
    y += 176

    # the call
    col = VIEW_COL.get(call["view"], FLAT)
    head = _wrap(call["headline"], 600, 15.5)[:5]
    h = max(196, 70 + len(head) * 32)
    c.panel(c.L, y, 1000, h, edge=col, lw=2)
    c.rect(c.L, y, 330, h, col, radius=20, z=2)
    c.text(c.L + 30, y + 46, sp("Next 20 sessions"), 10.5, "white", bold=True)
    c.text(c.L + 28, y + 100, call["view"].upper(), 30 if call["view"] != "Underperform" else 25, "white", bold=True)
    c.text(c.L + 30, y + 144, "versus the KSE-100", 13, "white")
    for k in range(5):
        c.rect(c.L + 30 + k * 30, y + 166, 22, 10, "white", radius=4, alpha=1.0 if k < call["confidence"] else 0.35, z=6)
    c.text(c.L + 190, y + 172, f"confidence {call['confidence']}/5", 11, "white")
    for j, ln in enumerate(head):
        c.text(c.L + 360, y + 46 + j * 32, ln, 15.5, CREAM)
    y += h + 16

    # buy and sell levels: direction from the tested signals, levels from the chart
    su = d.get("setup")
    if su:
        scol = "#b8862a" if su["label"].startswith("MIXED") else UP if su["score"] >= 1 else DOWN if su["score"] <= -1 else "#b8862a"
        z, rr = su.get("zone"), su.get("rr")
        if su.get("side") == "sell":
            sz = su.get("sell_zone")
            y = c.strip(y, [("Setup, tested signals", su["label"], scol),
                            ("Sell zone (resistance), Rs", f"{sz[0]:,.2f} to {sz[1]:,.2f}" if sz else "at a high", DOWN),
                            ("Support breaks below, Rs", f"{su['exit']:,.2f}" if su.get("exit") else "n/a", DOWN)], h=104)
        else:
            y = c.strip(y, [("Setup, tested signals", su["label"], scol),
                            ("Buy zone, Rs", f"{z[0]:,.2f} to {z[1]:,.2f}" if z else "n/a", None),
                            ("Reward to risk", f"{rr:.1f} to 1" if rr is not None and rr > 0 else "n/a", None)], h=104)
            y = c.strip(y, [("Setup fails below, Rs", f"{su['risk']:,.2f}" if su.get("risk") else "n/a", DOWN),
                            ("Target 1, Rs", f"{su['r1']:,.2f}" if su.get("r1") else "at a high", UP),
                            ("Target 2, Rs", f"{su['r2']:,.2f}" if su.get("r2") else "n/a", UP)], h=104)
        from .levels import lines as su_lines
        extra = []
        if su.get("asset_zone"):
            extra.append(f"Asset zone (0.8x to 1.2x book value): Rs {su['asset_zone'][0]:,.2f} to {su['asset_zone'][1]:,.2f}")
        extra.append("Technicals set the levels only: chart patterns had no edge on PSX in the 2010-2026 tests, "
                     "results and value did.")
        y = c.highlights(y, su_lines(su) + extra, title="Buy or sell: how the setup is built", max_lines=11)

    # key numbers
    a20, b20 = d["rel"].get(20) or (None, None)
    rel20 = a20 - b20 if a20 is not None and b20 is not None else None
    hi52 = max((p[2] for p in d["px"][-250:]), default=None) or v.get("52high")  # adjusted for bonus / split
    off_hi = (v["price"] / hi52 - 1) * 100 if hi52 and v.get("price") else None
    y = c.tiles(y, [("Expected P/E (SCS)", _f(v.get("epe")), None), ("Price to book", _f(v.get("pbv"), "x", 2), None),
                    ("Expected dividend yield", _f(v.get("edy"), "%"), None), ("Return on equity", _f(v.get("roe"), "%"), None),
                    ("Beta", _f(v.get("beta"), "", 2), None), ("EV to EBITDA", _f(v.get("eve")), None),
                    ("1 month vs KSE-100", "n/a" if rel20 is None else f"{rel20:+.1f} pts", None),
                    ("From 52-week high", "n/a" if off_hi is None else f"{off_hi:+.1f}%", None)],
                cols=4, tile_h=90)
    if d.get("fv"):
        from .underdogcard import fv_items
        y = c.tiles(y, fv_items(d["fv"], d.get("fv_rank")), title="Fair value and balance sheet", cols=4, tile_h=88)

    # 12-month chart: price, 50-day average, support / resistance, KSE-100 rebased
    px = d["px"][-250:]
    if len(px) > 30:
        ph, aw, top = 390, 760, y + 70
        ah = ph - 110
        c.panel(c.L, y, 1000, ph)
        c.label(c.L + 30, y + 38, "12 months: price vs KSE-100", 14)
        ax = c.axes(c.L + 30, top, aw, ah)
        closes = np.array([p[2] for p in px], dtype=float)
        xs = np.arange(len(px))
        ax.plot(xs, closes, color=t["accent"], lw=2.4)
        ax.fill_between(xs, closes, closes.min() * 0.97, color=t["accent"], alpha=0.07, lw=0)
        sma = None
        if len(closes) >= 50:
            sma = np.convolve(closes, np.ones(50) / 50, mode="valid")
            ax.plot(xs[49:], sma, color=GOLD, lw=1.6, alpha=0.9)
        reb = d.get("_index_rebased")
        lo, hi = closes.min(), closes.max()
        if reb is not None and len(reb) == len(px):
            ax.plot(xs, reb, color=FLAT, lw=1.4, ls=(0, (4, 3)))
            lo, hi = min(lo, reb.min()), max(hi, reb.max())
        # support / resistance: the same levels as the buy and sell panel (else SCS / computed technicals)
        su, tech = d.get("setup") or {}, d.get("tech") or {}
        sup = su.get("s1") if su else tech.get("support1")
        res = su.get("r1") if su else tech.get("resistance1")
        shown = {}
        for name, lv, colr in (("support", sup, UP), ("resistance", res, DOWN)):
            if lv and lo * 0.9 < lv < hi * 1.1:
                ax.axhline(lv, color=colr, lw=1.1, ls=(0, (2, 3)), alpha=0.8)
                lo, hi = min(lo, lv), max(hi, lv)
                shown[name] = (lv, colr)
        y0, y1 = lo * 0.96, hi * 1.03
        ax.set_xlim(0, len(px) - 1)
        ax.set_ylim(y0, y1)
        tags = []  # the number at the end of each line, just right of the plot, where no price line can cover it
        for name, (lv, colr) in shown.items():
            tags.append([top + (1 - (lv - y0) / (y1 - y0)) * ah, f"{lv:,.2f}", colr])
        if reb is not None and len(reb) == len(px) and idx_now(d, px):
            tags.append([top + (1 - (reb[-1] - y0) / (y1 - y0)) * ah, f"{idx_now(d, px):,.0f}", FLAT])
        tags.sort()
        for i in range(1, len(tags)):  # keep tags at least 20 px apart
            tags[i][0] = max(tags[i][0], tags[i - 1][0] + 20)
        for py, txt, colr in tags:
            c.text(c.L + 36 + aw, py + 5, txt, 10, colr, bold=True)
        c.text(c.L + 30, y + ph - 22, px[0][0][:7], 10, t["muted"])
        c.text(c.L + 30 + aw, y + ph - 22, px[-1][0][:7], 10, t["muted"], ha="right")
        # legend with today's numbers
        ch12 = (closes[-1] / closes[0] - 1) * 100
        idx = d.get("index_map") or {}
        kse_now, kse_then = idx.get(px[-1][0]), idx.get(px[0][0])
        legend = [("price", f"Rs {closes[-1]:,.2f}", f"{ch12:+.1f}% in 12m", t["accent"]),
                  ("50-day avg", f"Rs {sma[-1]:,.2f}" if sma is not None else "n/a",
                   f"price {(closes[-1] / sma[-1] - 1) * 100:+.1f}% vs it" if sma is not None else "", GOLD),
                  ("KSE-100", f"{kse_now:,.0f}" if kse_now else "n/a",
                   f"{(kse_now / kse_then - 1) * 100:+.1f}% in 12m" if kse_now and kse_then else "", FLAT),
                  ("support", f"Rs {sup:,.2f}" if sup else "none below",
                   f"{(sup / closes[-1] - 1) * 100:+.1f}% from price" if sup else "", UP),
                  ("resistance", f"Rs {res:,.2f}" if res else "at a high",
                   f"{(res / closes[-1] - 1) * 100:+.1f}% from price" if res else "", DOWN)]
        for k, (lab, val, sub, colr) in enumerate(legend):
            yy = y + 74 + k * 60
            c.text(c.R - 24, yy, lab, 10, colr, bold=True, ha="right")
            c.text(c.R - 24, yy + 22, val, 13, CREAM, bold=True, ha="right")
            if sub:
                c.text(c.R - 24, yy + 40, sub, 9, t["muted"], ha="right")
        y += ph + 16

    # chart read (facts) and what the chart evidence says
    if d.get("chart"):
        from .ta import TESTED, lines as chart_lines
        y = c.highlights(y, chart_lines(d["chart"]) + [TESTED["alone"][0].upper() + TESTED["alone"][1:]
                                                     + "; it counts only as confirmation of results, or as a warning."],
                         title="Chart read", max_lines=12)

    if d.get("patterns"):
        from .patterns import lines as pat_lines
        pl = pat_lines(d["patterns"])
        if pl:
            y = c.highlights(y, pl, title="Patterns and price mathematics", max_lines=12)

    # quarterly EPS vs the same quarter a year earlier
    # the snapshot can be a quarter behind: add the quarters checked against the results announcements
    q = {**(d["snap"].get("eps_quarters") or {}), **((d.get("ver") or {}).get("quarters") or {})}
    keys = sorted(q, key=lambda k: (k[:4], k[-1]))[-5:]
    pairs = [(k, q[k], q.get(f"{int(k[:4]) - 1} {k[-2:]}")) for k in keys]
    pairs = [p for p in pairs if p[2] is not None]
    if pairs:
        ph = 250
        c.panel(c.L, y, 1000, ph)
        c.label(c.L + 30, y + 38, "Quarterly EPS vs a year earlier (Rs)", 14)
        c.text(c.R - 30, y + 38, sp("grey = a year earlier"), 9.5, t["muted"], bold=True, ha="right")
        ax = c.axes(c.L + 40, y + 96, 920, ph - 142)
        xs = np.arange(len(pairs))
        ax.bar(xs - 0.18, [p[2] for p in pairs], width=0.34, color=FLAT, alpha=0.55)
        ax.bar(xs + 0.18, [p[1] for p in pairs], width=0.34, color=[_col(p[1] - p[2]) for p in pairs])
        top = max(max(p[1], p[2]) for p in pairs) or 1
        ax.set_ylim(min(0, min(min(p[1], p[2]) for p in pairs)) * 1.2, top * 1.08)
        ax.set_xlim(-0.6, len(pairs) - 0.4)
        for i, (k, now_, ly) in enumerate(pairs):
            fx = c.L + 40 + 920 * (i + 0.6) / (len(pairs) + 0.2)
            g = (now_ / ly - 1) * 100 if ly and ly > 0 else None
            tag = f" ({g:+.0f}%)" if g is not None else (" (to profit)" if now_ > 0 > ly else "")
            c.text(fx, y + 76, f"{now_:.2f}" + tag, 11.5, _col(g) if g is not None else (UP if tag else CREAM),
                   bold=True, ha="center")
            c.text(fx, y + ph - 26, f"FY{k[2:4]} {k[-2:]}", 10.5, t["muted"], ha="center")
        y += ph + 16

    y = c.highlights(y, call["thesis"] + [x for x in (call.get("valuation"), call.get("momentum")) if x],
                     title="Why this view", max_lines=10)
    y = c.highlights(y, call["risks"] + ([call["change_view_if"]] if call.get("change_view_if") else []),
                     title="What could make it wrong", max_lines=7)
    coming = list(call.get("catalysts") or [])
    for m in d.get("meetings") or []:
        if not any(m[:10] in x for x in coming):
            coming.append(f"Board meeting {m}")
    for x in d.get("exdates") or []:
        coming.append(f"Ex-date {x}")
    if coming:
        y = c.highlights(y, coming[:4], title="Coming up", max_lines=5)

    # signals and the experiment's record
    tech, sg, rc = d.get("tech") or {}, d.get("signal") or {}, d.get("record") or {}
    items = []
    if tech:
        items.append(("MACD day / week", f"{tech.get('macddaily', '?')} / {tech.get('macdweekly', '?')}", None))
        if tech.get("rsidaily") is not None:
            items.append(("RSI (14)", f"{tech['rsidaily']:.0f}", None))
    if sg:
        since = datetime.strptime(sg["since"], "%Y-%m-%d") if sg.get("since") else None
        items.append(("SCS signal", f"{sg['signal']}" + (f", {since.day} {since:%b}" if since else ""),
                      UP if sg["signal"] == "Buy" else DOWN))
    if rc:
        items.append(("SCS record here, 3y", f"{rc['win']}% right", None))
    if tech.get("sma50") and v.get("price") and len(items) < 4:
        gap = (v["price"] / tech["sma50"] - 1) * 100
        items.append(("Price vs 50-day avg", f"{gap:+.1f}%", _col(gap)))
    if tech.get("sma200") and v.get("price") and len(items) < 4:
        gap = (v["price"] / tech["sma200"] - 1) * 100
        items.append(("Price vs 200-day avg", f"{gap:+.1f}%", _col(gap)))
    y = c.strip(y, items[:4], h=100)
    ver = d.get("ver") or {}
    chk = ("Data checks passed: price matches the exchange, the last 4 quarters match the results announcements, "
           "book value and price to book cross-checked." if not ver.get("problems") else
           "Data check problem (no fair value shown): " + "; ".join(ver["problems"][:2]))
    chk += "".join(f" {n[0].upper()}{n[1:]}." for n in (ver.get("notes") or [])[:2])
    lines = _wrap(chk, 930, 12.5)[:4] + _wrap("Lab record: " + record_line(sb), 930, 12.5)[:3]
    ph = 40 + 26 * len(lines)
    c.panel(c.L, y, 1000, ph, fill=t["tile"])
    for j, ln in enumerate(lines):
        c.text(c.L + 30, y + 30 + j * 26, ln, 12.5, t["muted"])
    y += ph + 12
    c.text(c.L, y + 8, "Experiment: an AI reading of public data, scored against the market. Not investment advice.",
           10.5, t["muted"], italic=True)
    return y + 30


def lab_card(cfg: dict, d: dict, call: dict, sb: dict, session: str) -> bytes:
    idx = d.get("index_map")
    px = d["px"][-250:]
    if idx and px and px[0][0] in idx:
        base, last, reb = idx[px[0][0]], idx[px[0][0]], []
        for p in px:
            last = idx.get(p[0], last)
            reb.append(last / base * px[0][2])
        d["_index_rebased"] = np.array(reb)
    probe = Card(cfg, "research", height=3400)
    need = _draw(probe, d, call, sb, session)
    plt.close(probe.fig)
    c = Card(cfg, "research", height=int(need + 190))
    _draw(c, d, call, sb, session)
    c.footer()
    return c.png()
