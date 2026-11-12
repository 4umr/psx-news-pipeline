"""Research Lab cards for the underdog method and the fair-value panel (experiment; bot chat only)."""
from __future__ import annotations

from datetime import datetime

from .cards import CREAM, DOWN, UP, Card, clean, plt, sp

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def fv_items(fv: dict, rank: float | None) -> list:
    """Tiles for the blue-chip cards: fair value, gap, rank, and assets against liabilities."""
    fair = fv.get("fair")
    items = [("Fair value", "none (losses)" if not fair and (fv.get("eps_used") or 0) <= 0 else
              ("not verified" if not fair else f"Rs {fair:,.2f}"), None),
             ("Gap to price", "n/a" if fv.get("upside") is None else f"{fv['upside']:+.0f}%", None),
             ("Range of methods", "n/a" if not fair else f"{fv['low']:,.0f} to {fv['high']:,.0f}", None),
             ("Book value", "n/a" if fv.get("nav") is None else f"Rs {fv['nav']:,.2f}", None)]
    if fv.get("assets") and not fv.get("financial"):
        y = fv.get("bs_year", "")
        items += [(f"Assets / share, FY{y[2:]}", f"Rs {fv['assets']:,.2f}", None),
                  (f"Liabilities / share, FY{y[2:]}", f"Rs {fv['liabilities']:,.2f}", None),
                  ("Liabilities / assets", f"{fv['liab_pct']:.0f}%", None),
                  ("Cheaper than", "n/a" if rank is None else f"{rank:.0f}% of stocks", None)]
    else:
        items += [("Earnings value gap", "n/a" if fv.get("epv_gap") is None else f"{fv['epv_gap']:+.0f}%", None),
                  ("Required return", f"{fv['coe'] * 100:.0f}%", None),
                  ("Growth used", "n/a" if fv.get("g") is None else f"{fv['g'] * 100:.1f}%", None),
                  ("Cheaper than", "n/a" if rank is None else f"{rank:.0f}% of stocks", None)]
    return items


def _two_pass(cfg: dict, draw, *args) -> bytes:
    probe = Card(cfg, "research", height=3400)
    need = draw(probe, *args)
    plt.close(probe.fig)
    c = Card(cfg, "research", height=int(need + 190))
    draw(c, *args)
    c.footer()
    return c.png()


def _head(c: Card, kicker: str, session: str) -> int:
    y = 160
    c.chip(c.L, y, kicker, "#55665b")
    c.text(c.R, y, sp(datetime.strptime(session, "%Y-%m-%d").strftime("%a %d %b %Y")), 11, c.t["muted"], ha="right")
    return y + 40


# ---------------------------------------------------------------- Underdog Radar (one page)
def _radar(c: Card, r: dict) -> int:
    from .underdog import TRIGGER_TEXT
    t = c.t
    y = _head(c, "Research Lab · underdog radar", r["session"])
    c.text(c.L, y + 40, "UNDERDOG RADAR", 50, CREAM, bold=True, serif=True)
    c.text(c.L + 2, y + 92, "Asset-backed small and mid caps", 24, t["title2"], bold=True, serif=True)
    c.text(c.L + 4, y + 132, sp(f"{r['n']} stocks scanned · {r['n_small']} liquid small and mid · "
                                f"{r['n_backed']} at 1.2x book or less"), 10.5, t["muted"])
    y += 160
    trig = r["triggers"]
    if trig:
        y = c.entries(y, "Tested trigger today",
                      [(x["sym"], f"{x['label']}: {x['eps_line']}"[:60],
                        f"P/B {x['pb']:.2f}" + (f", fair value gap {x['fv']['upside']:+.0f}%" if x["fv"].get("upside") is not None else ""),
                        f"{x['react']:+.1f}%", x["react"]) for x in trig[:4]], right_title="move today")
    else:
        y = c.highlights(y, ["No stock met the tested trigger today: good results, up 3%+ on the day, at 1.2x book or less."],
                         title="Tested trigger today", max_lines=3)
    rows = []
    for b in r["backed"]:
        fv = b["fv"]
        gap = fv.get("epv_gap")
        bad = any(k in f for f in fv.get("flags") or [] for k in ("debt", "current", "liabilities"))
        rows.append([b["sym"], f"{b['price']:,.2f}", f"{b['pb']:.2f}x",
                     "loss" if gap is None else (f"{gap:+.0f}%", UP if gap > 0 else DOWN),
                     "financial" if fv.get("financial") else ("n/a" if fv.get("liab_pct") is None else f"{fv['liab_pct']:.0f}%"),
                     ("flag", DOWN) if bad else ("clean", UP)])
    y = c.grid(y, "Asset-backed list (P/B 1.2x or less)",
               ["Stock", "Price", "P/B", "Earnings value gap", "Liab./assets", "Debt, liquidity"], rows,
               [30, 250, 390, 580, 760, 970], ["left", "right", "right", "right", "right", "right"],
               right_title="every figure cross-checked", row_h=40, max_rows=10)
    y = c.highlights(y, [
        TRIGGER_TEXT,
        "Not enough on their own (2010-2026, versus similar stocks over 3 months): buying at a 3-year price floor "
        "-2.2%, floor plus a volume burst -2.9%, far below the all-time high -1.1%, strong season months about 0%.",
        f"Earnings value gap: the last 4 quarters' profit (checked against the company's own results) valued at a "
        f"{r['coe'] * 100:.0f}% yearly return with no growth, against the price. Small and mid caps with a 50%+ gap beat "
        "similar stocks by 4.8% over 3 months in 2022-2026; cheap P/E did not work before 2023. Refineries and other "
        "cyclicals often look cheap on this measure."],
        title="What the history says", max_lines=14)
    c.text(c.L, y + 8, "Experiment: public data screened by fixed formulas and scored against the market. Not investment advice.",
           10.5, t["muted"], italic=True)
    return y + 30


def radar_card(cfg: dict, r: dict) -> bytes:
    return _two_pass(cfg, _radar, r)


# ---------------------------------------------------------------- one watched stock (one page)
def _watch(c: Card, w: dict, session: str) -> int:
    from .underdog import TRIGGER_TEXT
    t, lv, fv = c.t, w["levels"], w["fv"]
    y = _head(c, "Research Lab · underdog watch", session)
    c.text(c.L, y + 44, w["sym"], 64, CREAM, bold=True, serif=True)
    c.text(c.L + 2, y + 104, clean(w["name"])[:48], 26, t["title2"], bold=True, serif=True)
    c.text(c.R, y + 40, f"Rs {w['price']:,.2f}", 34, CREAM, bold=True, ha="right")
    c.text(c.L + 4, y + 146, sp(clean(w["sector"]).title()[:44]), 11.5, t["muted"])
    y += 176
    az, p = w.get("asset_zone"), w["price"]
    if az:
        where = "inside" if az[0] <= p <= az[1] else ("above" if p > az[1] else "below")
        col = UP if where == "inside" else ("#b8862a" if where == "above" else DOWN)
        h = 150
        c.panel(c.L, y, 1000, h, edge=col, lw=2)
        c.rect(c.L, y, 330, h, col, radius=20, z=2)
        c.text(c.L + 30, y + 44, sp("Asset zone"), 10.5, "white", bold=True)
        c.text(c.L + 28, y + 92, where.upper(), 30, "white", bold=True)
        c.text(c.L + 360, y + 46, f"Rs {az[0]:,.2f} to Rs {az[1]:,.2f}", 24, CREAM, bold=True)
        c.text(c.L + 360, y + 88, f"0.8x to 1.2x book value of Rs {w['bv']:,.2f} (price to book now {p / w['bv']:.2f}x)", 13.5, CREAM)
        c.text(c.L + 360, y + 118, f"Zone breaks below Rs {az[0] * 0.9:,.2f}", 13.5, t["muted"])
        y += h + 16

    def rs(x):
        return "n/a" if x is None else f"Rs {x:,.2f}"

    if w.get("problems"):
        y = c.highlights(y, [f"{x}" for x in w["problems"]] + ["Figures that depend on it are not shown."],
                         title="Data check did not pass", max_lines=6)

    y = c.tiles(y, [("Book value / share", rs(w["bv"]), None), ("Assets / share", rs(fv.get("assets")), None),
                    ("Liabilities / share", rs(fv.get("liabilities")), None),
                    ("Liabilities / assets", "n/a" if fv.get("liab_pct") is None else f"{fv['liab_pct']:.0f}%", None),
                    ("Debt to equity", "n/a" if fv.get("tde") is None else f"{fv['tde'] / 100:.1f}x", None),
                    ("Current ratio", "n/a" if fv.get("cr") is None else f"{fv['cr']:.2f}x", None),
                    ("Cash / share", rs(fv.get("cash")), None),
                    ("Fair value", ("none: losses" if (w.get("ttm") or 0) <= 0 else "not verified")
                     if not fv.get("fair") else rs(fv["fair"]), None)],
                title=f"Assets against liabilities (FY{str(fv.get('bs_year', ''))[2:]} year end)" if fv.get("bs_year")
                else "Assets against liabilities", cols=4, tile_h=88)
    from .fairvalue import lines as fv_lines
    y = c.highlights(y, fv_lines(fv)[:1], title="Fair value", max_lines=5)
    hist = []
    if lv.get("hf"):
        hist.append(f"3-year floor (10% of closes were lower): Rs {lv['hf']:,.2f}; price is {(p / lv['hf'] - 1) * 100:+.0f}% from it")
    if lv.get("hi52"):
        hist.append(f"52-week high Rs {lv['hi52']:,.2f}; 3-year high Rs {lv['hi3y']:,.2f}; 3-year median Rs {lv['med3y']:,.2f}")
    if lv.get("ath"):
        hist.append(f"All-time high since {w.get('since', '2008')} (adjusted for payouts) Rs {lv['ath']:,.2f}: "
                    f"the price is {lv['ath_pct']:.0f}% of it")
    sn, sx = w.get("season_now"), w.get("season_next")
    if sn:
        hist.append(f"{MONTHS[int(session[5:7])]} in past years: average {sn['avg']:+.1f}%, up in {sn['up']} of {sn['n']}")
    if sx:
        hist.append(f"{MONTHS[w['season_month']]} in past years: average {sx['avg']:+.1f}%, up in {sx['up']} of {sx['n']}")
    ig = w.get("ignition") or {}
    if ig.get("x") is not None:
        hist.append(f"Value traded today {ig['x']:.1f}x its 60-day average; last 5 days {ig.get('x5') or 0:.1f}x")
    y = c.highlights(y, hist, title="Price history, season and volume", max_lines=9)
    if w.get("flags"):
        y = c.highlights(y, w["flags"], title="Red flags", max_lines=7)
    wait = ([f"Next board meeting: {w['meeting']}"] if w.get("meeting") else []) + [TRIGGER_TEXT]
    if w.get("notes"):
        wait.append("Data note: " + "; ".join(w["notes"]))
    y = c.highlights(y, wait, title="What to wait for", max_lines=7)
    c.text(c.L, y + 8, "Experiment: public data screened by fixed formulas. Not investment advice.", 10.5, t["muted"], italic=True)
    return y + 30


def watch_card(cfg: dict, w: dict, session: str) -> bytes:
    return _two_pass(cfg, _watch, w, session)
