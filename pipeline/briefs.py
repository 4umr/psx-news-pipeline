"""Scheduled briefs: morning, market close, week ahead (and the first-run snapshot)."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from . import watchers as w
from .common import arrow, esc, fmt_num, fmt_pct, link, now_pkt, parse_hhmm
from .sources import scs
from .state import State

SBP_URL = "https://www.sbp.org.pk/"


# ---------------------------------------------------------------- blocks
def psx_block(idx: list[dict]) -> str:
    order = ["KSE 100", "KSE 30", "KMI 30", "KSE ALL"]
    rows = {r["kse_index_type"]: r for r in idx}
    lines = []
    for name in order:
        r = rows.get(name)
        if not r:
            continue
        close, chg = r["kse_index_close"], r["kse_index_change"]
        prev = close - chg
        pct = chg / prev * 100 if prev else 0
        lines.append(f"{arrow(chg)} {name}: <b>{close:,.0f}</b> ({chg:+,.0f} | {pct:+.2f}%)")
    k = rows.get("KSE 100")
    if k:
        lines.append(f"  KSE-100 range {k['kse_index_low']:,.0f} – {k['kse_index_high']:,.0f}")
    return "\n".join(lines) or "PSX data unavailable"


def movers_block(view: list[dict]) -> str:
    if not view:
        return ""
    pos, neg = w.contributors(view)
    rows = [r for r in view if r.get("LDCP")]
    for r in rows:
        r["_pct"] = (r["CurrentPrice"] / r["LDCP"] - 1) * 100
    rows.sort(key=lambda r: r["_pct"])
    gain = " · ".join(f"{esc(r['company_code'])} {r['_pct']:+.1f}%" for r in reversed(rows[-5:]) if r["_pct"] > 0)
    lose = " · ".join(f"{esc(r['company_code'])} {r['_pct']:+.1f}%" for r in rows[:5] if r["_pct"] < 0)
    return (f"<b>Index points</b>\n🟢 {w.contrib_line(pos)}\n🔴 {w.contrib_line(neg)}\n"
            f"<b>KSE-100 top % moves</b>\n🟢 {gain or '—'}\n🔴 {lose or '—'}")


def upcoming_auctions(text: str) -> str:
    """Keep only auctions dated today or later from SBP's 'Upcoming Auction' panel."""
    today = now_pkt().date()
    out = []
    for name, d in re.findall(r"(.+?)\s+(\d{1,2}-[A-Za-z]{3}-\d{2})\s*", text or ""):
        try:
            when = datetime.strptime(d, "%d-%b-%y").date()
        except ValueError:
            continue
        if when >= today:
            out.append(f"{name.strip()} {when:%d %b}")
    return " · ".join(out)


def sbp_block(s: dict) -> str:
    if not s:
        return "SBP data unavailable"
    lines = [f"Policy rate: <b>{fmt_num(s.get('policy_rate'))}%</b>"
             + (f" · O/N repo {s['overnight_repo']['rate']:.2f}%" if s.get("overnight_repo") else "")]
    if k := s.get("kibor"):
        lines.append(f"KIBOR ({esc(k['as_on'])}): 3M {k['3M'][1]:.2f} · 6M {k['6M'][1]:.2f} · 12M {k['12M'][1]:.2f}")
    if m := s.get("mtb"):
        y = " · ".join(f"{t} {v:.2f}" if v else f"{t} rej." for t, v in m["yields"].items())
        lines.append(f"T-bills ({esc(m['as_on'])}): {y}")
    if p := s.get("pib"):
        y = " · ".join(f"{t} {v:.2f}" if v else f"{t} rej." for t, v in p["yields"].items())
        lines.append(f"PIBs ({esc(p['as_on'])}): {y}")
    if r := s.get("reserves"):
        lines.append(f"Reserves ({esc(r['as_on'])}): SBP {w.usd_bn(r['sbp'])} · Total {w.usd_bn(r['total'])}")
    if u := s.get("usdpkr"):
        lines.append(f"USD/PKR ({esc(u['as_on'])}): <b>{u['m2m']:.2f}</b> (bid {u['bid']:.2f} / offer {u['offer']:.2f})")
    if a := upcoming_auctions(s.get("upcoming_auctions", "")):
        lines.append(f"Upcoming auctions: {esc(a)}")
    return "\n".join(lines)


def global_block(mk: dict, cfg: dict) -> str:
    lines = []
    for sym, (name, unit, _th) in cfg["global_markets"].items():
        d = mk.get(sym)
        if not d:
            continue
        val = f"${d['last']:,.2f}" if unit == "$" else (f"{d['last']:.2f}%" if unit == "%" else f"{d['last']:,.2f}")
        lines.append(f"{arrow(d['pct'])} {esc(name)}: {val} ({fmt_pct(d['pct'], 1)})")
    return "\n".join(lines) or "Global data unavailable"


def headlines_block(state: State, hours: int, n: int = 8) -> str:
    cutoff = time.time() - hours * 3600
    hs = [h for h in state.data["headlines"] if h["ts"] >= cutoff]
    hs.sort(key=lambda h: (-h["sc"], -h["ts"]))
    lines = [f"• {esc(h['t'])} — {link(h['u'], h['s'])}" if h.get("u") else f"• {esc(h['t'])}" for h in hs[:n]]
    return "\n".join(lines) or "No major headlines."


def calendar_block(start: datetime, days: int) -> str:
    s0 = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    e0 = s0 + timedelta(days=days)
    bms = [r for r in scs.board_meetings() if r.get("date") and s0 <= r["date"].replace(tzinfo=None) < e0]
    bms.sort(key=lambda r: r["date"])
    out = []
    if bms:
        by_day: dict[str, list[str]] = {}
        for r in bms:
            codes = by_day.setdefault(r["date"].strftime("%a %d %b"), [])
            if r["company_code"] not in codes:
                codes.append(r["company_code"])
        out.append("<b>Board meetings (results expected)</b>")
        for d, codes in by_day.items():
            shown = ", ".join(codes[:25]) + (f" +{len(codes) - 25}" if len(codes) > 25 else "")
            out.append(f"  {d}: {esc(shown)}")
    bcs = [r for r in scs.book_closures() if r.get("date") and s0 <= r["date"] < e0]
    bcs.sort(key=lambda r: r["date"])
    if bcs:
        out.append("<b>Payout book closures</b>")
        for r in bcs[:20]:
            pay = " ".join(x for x in (f"Div {r['bm_dividend']}" if r.get("bm_dividend") else "",
                                       f"Bonus {r['bm_bonus']}" if r.get("bm_bonus") else "",
                                       f"Right {r['bm_right_per']}" if r.get("bm_right_per") else "") if x)
            out.append(f"  {r['date']:%d %b}: {esc(r['company_code'])} {esc(pay)}")
    return "\n".join(out) or "No board meetings / book closures found."


def fipi_section(state: State) -> str:
    f = state.snap("fipi_last")
    if not f:
        return ""
    rate = ((state.snap("sbp") or {}).get("usdpkr") or {}).get("m2m") or 280.0
    return f"\n\n🌍 <b>Investor flows ({f['date']})</b>\n" + w.fipi_block(f, rate)


def footer(cfg: dict) -> str:
    return f"\n\n<i>{esc(cfg.get('disclaimer', ''))}</i>" if cfg.get("disclaimer") else ""


# ---------------------------------------------------------------- briefs
def morning(cfg: dict, state: State, mk: dict, title: str = "☀️ PSX MORNING BRIEF") -> str:
    now = now_pkt()
    return (f"{title} — {now:%a %d %b %Y}\n\n"
            f"📈 <b>PSX (last close)</b>\n{psx_block(scs.indices())}"
            f"{fipi_section(state)}\n\n"
            f"🏦 <b>SBP / Rates / PKR</b>\n{sbp_block(state.snap('sbp') or {})}\n\n"
            f"🌐 <b>Global markets</b>\n{global_block(mk, cfg)}\n\n"
            f"📅 <b>Today</b>\n{calendar_block(now, 1)}\n\n"
            f"📰 <b>Top headlines (last 16h)</b>\n{headlines_block(state, 16)}"
            + footer(cfg))


def close(cfg: dict, state: State, mk: dict, view: list[dict] | None = None) -> str:
    now = now_pkt()
    view = view or scs.kse100_view()
    tomorrow = now + timedelta(days=1 if now.weekday() < 4 else 7 - now.weekday())
    fipi_today = (state.snap("fipi_last") or {}).get("date") == f"{now:%Y-%m-%d}"
    return (f"🔔 <b>PSX CLOSING WRAP</b> — {now:%a %d %b %Y}\n\n"
            f"📈 <b>Indices</b>\n{psx_block(scs.indices())}\n\n"
            f"{movers_block(view)}"
            + (fipi_section(state) if fipi_today else "\n\n🌍 FIPI/LIPI for today will be posted as soon as it is published.")
            + f"\n\n🏦 <b>Rates / PKR</b>\n{sbp_block(state.snap('sbp') or {})}\n\n"
            f"🌐 <b>Global</b>\n{global_block(mk, cfg)}\n\n"
            f"📅 <b>Next session ({tomorrow:%a %d %b})</b>\n{calendar_block(tomorrow, 1)}\n\n"
            f"📰 <b>Today's key headlines</b>\n{headlines_block(state, 10, 10)}"
            + footer(cfg))


def week_ahead(cfg: dict, state: State, mk: dict) -> str:
    now = now_pkt()
    closes = state.data["kse_closes"]
    week = ""
    recent = [(d, c) for d, c in closes.items() if d >= f"{now - timedelta(days=7):%Y-%m-%d}"]
    prior = [(d, c) for d, c in closes.items() if d < f"{now - timedelta(days=7):%Y-%m-%d}"]
    if recent and prior:
        a, b = prior[-1][1], recent[-1][1]
        week = f"{arrow(b - a)} KSE-100 this week: <b>{b:,.0f}</b> ({b - a:+,.0f} | {(b / a - 1) * 100:+.2f}%)\n\n"
    monday = now + timedelta(days=(7 - now.weekday()) % 7 or 1)
    return (f"📅 <b>PSX WEEK AHEAD</b> — week of {monday:%d %b %Y}\n\n"
            f"{week}"
            f"🗓️ <b>Corporate calendar (next 7 days)</b>\n{calendar_block(monday, 7)}\n\n"
            f"🏦 <b>Rates snapshot</b>\n{sbp_block(state.snap('sbp') or {})}\n\n"
            f"🔁 <b>Regular data releases to watch</b>\n"
            f"  • SBP reserves — every Thursday\n"
            f"  • PBS SPI (weekly inflation) — every Friday\n"
            f"  • T-bill auctions — usually every other Wednesday (see SBP schedule above)\n"
            f"  • CPI inflation — first working days of the month\n"
            f"  • Current account / remittances — mid-month (SBP)\n"
            f"  • Petrol price revision — 15th & last day of month\n\n"
            f"🌐 <b>Global</b>\n{global_block(mk, cfg)}\n\n"
            f"📰 <b>Biggest stories of the last 36h</b>\n{headlines_block(state, 36, 10)}"
            + footer(cfg))


BUILDERS = {"morning": morning, "close": close, "week_ahead": week_ahead}


def due(cfg: dict, state: State, now: datetime, grace_hours: int = 3) -> list[str]:
    """Briefs whose scheduled time has passed today and haven't been sent yet."""
    out = []
    for name, b in cfg.get("briefs", {}).items():
        if now.weekday() not in b["days"]:
            continue
        h, m = parse_hhmm(b["time"])
        sched = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if sched <= now <= sched + timedelta(hours=grace_hours) and state.data["briefs"].get(name) != f"{now:%Y-%m-%d}":
            out.append(name)
    return out
