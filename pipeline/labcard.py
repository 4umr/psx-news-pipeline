"""One-page Research Lab card (experiment; private test chat only). Uses the brand Card from cards.py."""
from __future__ import annotations

from datetime import datetime

import numpy as np

from .cards import CREAM, DOWN, FLAT, GOLD, UP, Card, _col, _tri, _wrap, clean, plt, sp

VIEW_COL = {"Outperform": UP, "Underperform": DOWN, "Neutral": "#b8862a"}


def _f(x, u="x", d=1) -> str:
    return "n/a" if x is None else f"{x:,.{d}f}{u}"


def _draw(c: Card, d: dict, call: dict, sb: dict, session: str) -> int:
    from .lab import record_line
    v, t = d["snap"]["values"], c.t
    y = 160
    c.chip(c.L, y, "Research Lab · experiment", "#55665b")
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

    # key numbers
    a20, b20 = d["rel"].get(20) or (None, None)
    rel20 = a20 - b20 if a20 is not None and b20 is not None else None
    hi52 = v.get("52high")
    off_hi = (v["price"] / hi52 - 1) * 100 if hi52 and v.get("price") else None
    y = c.tiles(y, [("Expected P/E (SCS)", _f(v.get("epe")), None), ("Price to book", _f(v.get("pbv"), "x", 2), None),
                    ("Expected dividend yield", _f(v.get("edy"), "%"), None), ("Return on equity", _f(v.get("roe"), "%"), None),
                    ("Beta", _f(v.get("beta"), "", 2), None), ("EV to EBITDA", _f(v.get("eve")), None),
                    ("1 month vs KSE-100", "n/a" if rel20 is None else f"{rel20:+.1f} pts", None),
                    ("From 52-week high", "n/a" if off_hi is None else f"{off_hi:+.1f}%", None)],
                cols=4, tile_h=90)

    # 12-month chart: price, 50-day average, support / resistance, KSE-100 rebased
    px = d["px"][-250:]
    if len(px) > 30:
        ph = 330
        c.panel(c.L, y, 1000, ph)
        c.label(c.L + 30, y + 38, "12 months: price vs KSE-100", 14)
        ax = c.axes(c.L + 30, y + 70, 800, ph - 110)
        closes = np.array([p[2] for p in px], dtype=float)
        xs = np.arange(len(px))
        ax.plot(xs, closes, color=t["accent"], lw=2.4)
        ax.fill_between(xs, closes, closes.min() * 0.97, color=t["accent"], alpha=0.07, lw=0)
        if len(closes) >= 50:
            sma = np.convolve(closes, np.ones(50) / 50, mode="valid")
            ax.plot(xs[49:], sma, color=GOLD, lw=1.6, alpha=0.9)
        reb = d.get("_index_rebased")
        lo, hi = closes.min(), closes.max()
        if reb is not None and len(reb) == len(px):
            ax.plot(xs, reb, color=FLAT, lw=1.4, ls=(0, (4, 3)))
            lo, hi = min(lo, reb.min()), max(hi, reb.max())
        tech = d.get("tech") or {}
        for key, colr in (("support1", UP), ("resistance1", DOWN)):
            lv = tech.get(key)
            if lv and lo * 0.9 < lv < hi * 1.1:
                ax.axhline(lv, color=colr, lw=1, ls=(0, (2, 3)), alpha=0.7)
        ax.set_xlim(0, len(px) - 1)
        ax.set_ylim(lo * 0.96, hi * 1.03)
        c.text(c.L + 30, y + ph - 22, px[0][0][:7], 10, t["muted"])
        c.text(c.L + 830, y + ph - 22, px[-1][0][:7], 10, t["muted"], ha="right")
        for k, (lab, colr) in enumerate((("price", t["accent"]), ("50-day avg", GOLD), ("KSE-100", FLAT),
                                         ("support", UP), ("resistance", DOWN))):
            c.text(c.R - 24, y + 90 + k * 24, lab, 10.5, colr, bold=True, ha="right")
        y += ph + 16

    # quarterly EPS vs the same quarter a year earlier
    q = d["snap"].get("eps_quarters") or {}
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
            c.text(fx, y + 76, f"{now_:.2f}" + (f" ({g:+.0f}%)" if g is not None else ""), 11.5,
                   _col(g) if g is not None else CREAM, bold=True, ha="center")
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
    y = c.strip(y, items[:4], h=100)
    lines = _wrap("Lab record: " + record_line(sb), 930, 12.5)[:3]
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
