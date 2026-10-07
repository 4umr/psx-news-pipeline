"""Scheduled briefs: morning, market close, week ahead (and the first-run snapshot).

Each builder returns (text, card_png_or_None, card_caption).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from . import watchers as w
from .common import arrow, esc, fmt_num, fmt_pct, link, log, now_pkt, parse_hhmm
from .sources import scs
from .state import State
from .style import DIV, footer, header, section

SBP_URL = "https://www.sbp.org.pk/"


# ---------------------------------------------------------------- blocks
def _kse(idx: list[dict], name: str = "KSE 100") -> dict | None:
    return next((r for r in idx if r["kse_index_type"] == name), None)


def psx_block(idx: list[dict]) -> str:
    lines = []
    for name, label in (("KSE 100", "KSE-100"), ("KSE 30", "KSE-30"), ("KMI 30", "KMI-30"), ("KSE ALL", "All Share")):
        r = _kse(idx, name)
        if not r:
            continue
        close, chg = r["kse_index_close"], r["kse_index_change"]
        prev = close - chg
        pct = chg / prev * 100 if prev else 0
        lines.append(f"{arrow(chg)} {label}: <b>{close:,.0f}</b> ({chg:+,.0f} | {pct:+.2f}%)")
    k = _kse(idx)
    if k:
        lines.append(f"▫️ KSE-100 day range: {k['kse_index_low']:,.0f} – {k['kse_index_high']:,.0f}")
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
    return (f"{section('🎯', 'Index movers (points)')}\n🟢 {w.contrib_line(pos)}\n🔴 {w.contrib_line(neg)}\n\n"
            f"{section('📊', 'KSE-100 top % moves')}\n🟢 {gain or '—'}\n🔴 {lose or '—'}")


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
    lines = [f"🏦 Policy rate: <b>{fmt_num(s.get('policy_rate'))}%</b>"
             + (f" · O/N repo {s['overnight_repo']['rate']:.2f}%" if s.get("overnight_repo") else "")]
    if k := s.get("kibor"):
        lines.append(f"📉 KIBOR: 3M {k['3M'][1]:.2f}% · 6M {k['6M'][1]:.2f}% · 12M {k['12M'][1]:.2f}%")
    if m := s.get("mtb"):
        y = " · ".join(f"{t} {v:.2f}%" if v else f"{t} rej." for t, v in m["yields"].items())
        lines.append(f"📜 T-bills ({esc(m['as_on'])}): {y}")
    if p := s.get("pib"):
        y = " · ".join(f"{t} {v:.2f}%" if v else f"{t} rej." for t, v in p["yields"].items())
        lines.append(f"📜 PIBs ({esc(p['as_on'])}): {y}")
    if r := s.get("reserves"):
        lines.append(f"💵 Reserves ({esc(r['as_on'])}): SBP {w.usd_bn(r['sbp'])} · Total {w.usd_bn(r['total'])}")
    if u := s.get("usdpkr"):
        lines.append(f"💱 USD/PKR: <b>{u['m2m']:.2f}</b> (bid {u['bid']:.2f} / offer {u['offer']:.2f})")
    if a := upcoming_auctions(s.get("upcoming_auctions", "")):
        lines.append(f"🗓️ Next auctions: {esc(a)}")
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
    lines = []
    for i, h in enumerate(hs[:n], 1):
        src = f" — {link(h['u'], h['s'])}" if h.get("u") else ""
        lines.append(f"{i}. {esc(h['t'])}{src}")
    return "\n".join(lines) or "No major headlines."


def calendar_block(start: datetime, days: int) -> tuple[str, int, int]:
    s0 = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    e0 = s0 + timedelta(days=days)
    bms = [r for r in scs.board_meetings() if r.get("date") and s0 <= r["date"].replace(tzinfo=None) < e0]
    bms.sort(key=lambda r: r["date"])
    out = []
    n_board = 0
    if bms:
        by_day: dict[str, list[str]] = {}
        for r in bms:
            codes = by_day.setdefault(r["date"].strftime("%a %d %b"), [])
            if r["company_code"] not in codes:
                codes.append(r["company_code"])
        n_board = sum(len(c) for c in by_day.values())
        out.append("🧾 <b>Board meetings (results expected)</b>")
        for d, codes in by_day.items():
            shown = ", ".join(codes[:25]) + (f" +{len(codes) - 25}" if len(codes) > 25 else "")
            out.append(f"   {d}: {esc(shown)}")
    bcs = [r for r in scs.book_closures() if r.get("date") and s0 <= r["date"] < e0]
    bcs.sort(key=lambda r: r["date"])
    if bcs:
        out.append("💰 <b>Payout book closures</b>")
        for r in bcs[:20]:
            pay = " ".join(x for x in (f"Div {r['bm_dividend']}" if r.get("bm_dividend") else "",
                                       f"Bonus {r['bm_bonus']}" if r.get("bm_bonus") else "",
                                       f"Right {r['bm_right_per']}" if r.get("bm_right_per") else "") if x)
            out.append(f"   {r['date']:%d %b}: {esc(r['company_code'])} {esc(pay)}")
        if len(bcs) > 20:
            out.append(f"   …and {len(bcs) - 20} more")
    return "\n".join(out) or "No board meetings or book closures scheduled.", n_board, len(bcs)


def _rate(state: State) -> float:
    return ((state.snap("sbp") or {}).get("usdpkr") or {}).get("m2m") or 280.0


def fipi_section(state: State) -> str:
    f = state.snap("fipi_last")
    if not f:
        return ""
    return f"\n\n{section('🌍', 'Investor flows (' + f['date'] + ')')}\n" + w.fipi_block(f, _rate(state))


def glance(idx: list[dict], state: State, mk: dict) -> str:
    """Auto-generated 'at a glance' bullets from the numbers (no opinions)."""
    out = []
    k = _kse(idx)
    if k:
        chg = k["kse_index_change"]
        pct = chg / (k["kse_index_close"] - chg) * 100 if k["kse_index_close"] != chg else 0
        verb = "gained" if chg > 0 else ("lost" if chg < 0 else "was flat at")
        out.append(f"KSE-100 {verb} {abs(chg):,.0f} pts ({pct:+.2f}%) to {k['kse_index_close']:,.0f}")
    f = state.snap("fipi_last")
    if f and (net := f["summary"].get("FIPI", {}).get("net")) is not None:
        out.append(f"Foreigners were net {'buyers' if net > 0 else 'sellers'} of ${abs(net):.2f}m ({f['date']})")
    s = state.snap("sbp") or {}
    if s.get("policy_rate") is not None and (m := s.get("mtb")):
        y3 = m["yields"].get("3-M")
        if y3:
            spread = round((y3 - s["policy_rate"]) * 100)
            out.append(f"3M T-bill at {y3:.2f}% vs policy rate {s['policy_rate']:.2f}% ({spread:+d} bps)")
    if (b := mk.get("BZ=F")) and b.get("pct") is not None:
        out.append(f"Brent at ${b['last']:.2f} ({b['pct']:+.1f}%)")
    return "\n".join(f"• {esc(x)}" for x in out)


# ---------------------------------------------------------------- briefs
def _safe_card(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:  # noqa: BLE001 — a card failure must never block the text brief
        log.exception("card render failed")
        return None


def morning(cfg: dict, state: State, mk: dict, view=None, title: str = "Morning Brief"):
    now = now_pkt()
    idx = scs.indices()
    cal, n_board, n_bc = calendar_block(now, 1)
    g = glance(idx, state, mk)
    text = (header("☀️", f"PSX {title}", f"{now:%A, %d %B %Y}") + "\n"
            + (f"{section('⚡', 'At a glance')}\n{g}\n\n" if g else "")
            + f"{section('📈', 'PSX — last close')}\n{psx_block(idx)}"
            f"{fipi_section(state)}\n\n"
            f"{section('🏦', 'SBP · Rates · PKR')}\n{sbp_block(state.snap('sbp') or {})}\n\n"
            f"{section('🌐', 'Global markets')}\n{global_block(mk, cfg)}\n\n"
            f"{section('📅', 'Today on the corporate calendar')}\n{cal}\n\n"
            f"{section('📰', 'Top headlines (last 16h)')}\n{headlines_block(state, 16)}"
            + footer(cfg, ["MorningBrief", "KSE100"]))
    card = None
    if cfg.get("brand", {}).get("cards", True):
        from .cards import morning_card
        card = _safe_card(morning_card, cfg, now, idx, state.snap("sbp") or {}, mk, n_board, n_bc, kicker=title)
    return text, card, f"☀️ <b>PSX {esc(title)}</b> · {now:%d %b %Y} — full details below 👇"


def close(cfg: dict, state: State, mk: dict, view: list[dict] | None = None):
    now = now_pkt()
    idx = scs.indices()
    view = view or scs.kse100_view()
    nxt = now + timedelta(days=1 if now.weekday() < 4 else 7 - now.weekday())
    fipi_today = (state.snap("fipi_last") or {}).get("date") == f"{now:%Y-%m-%d}"
    cal, _, _ = calendar_block(nxt, 1)
    g = glance(idx, state, mk) if fipi_today else glance(idx, _NoFipi(state), mk)
    text = (header("🔔", "PSX Closing Wrap", f"{now:%A, %d %B %Y}") + "\n"
            + (f"{section('⚡', 'At a glance')}\n{g}\n\n" if g else "")
            + f"{section('📈', 'Indices')}\n{psx_block(idx)}\n\n"
            f"{movers_block(view)}"
            + (fipi_section(state) if fipi_today else
               "\n\n🌍 FIPI/LIPI for today will be posted as soon as NCCPL publishes it.")
            + f"\n\n{section('🏦', 'Rates · PKR')}\n{sbp_block(state.snap('sbp') or {})}\n\n"
            f"{section('🌐', 'Global')}\n{global_block(mk, cfg)}\n\n"
            f"{section('📅', f'Next session · {nxt:%a %d %b}')}\n{cal}\n\n"
            f"{section('📰', 'Key headlines today')}\n{headlines_block(state, 10, 10)}"
            + footer(cfg, ["ClosingWrap", "KSE100"]))
    card = None
    if cfg.get("brand", {}).get("cards", True):
        from .cards import close_card
        card = _safe_card(close_card, cfg, now, idx, view, state.snap("sbp") or {}, mk,
                          state.snap("fipi_last") if fipi_today else None)
    return text, card, f"🔔 <b>PSX Closing Wrap</b> · {now:%d %b %Y} — full details below 👇"


class _NoFipi:
    """State view that hides a stale FIPI snapshot from the closing 'at a glance'."""

    def __init__(self, state: State):
        self._s = state

    def snap(self, name, default=None):
        return None if name == "fipi_last" else self._s.snap(name, default)


def week_ahead(cfg: dict, state: State, mk: dict, view=None):
    now = now_pkt()
    closes = state.data["kse_closes"]
    week = ""
    cut = f"{now - timedelta(days=7):%Y-%m-%d}"
    recent = [(d, c) for d, c in closes.items() if d >= cut]
    prior = [(d, c) for d, c in closes.items() if d < cut]
    if recent and prior:
        a, b = prior[-1][1], recent[-1][1]
        week = f"{arrow(b - a)} KSE-100 this week: <b>{b:,.0f}</b> ({b - a:+,.0f} | {(b / a - 1) * 100:+.2f}%)\n\n"
    monday = now + timedelta(days=(7 - now.weekday()) % 7 or 1)
    cal, _, _ = calendar_block(monday, 7)
    text = (header("📅", "PSX Week Ahead", f"Week of {monday:%d %B %Y}") + "\n"
            f"{week}"
            f"{section('🗓️', 'Corporate calendar (next 7 days)')}\n{cal}\n\n"
            f"{section('🏦', 'Rates snapshot')}\n{sbp_block(state.snap('sbp') or {})}\n\n"
            f"{section('🔁', 'Regular data releases to watch')}\n"
            f"   • SBP reserves — every Thursday\n"
            f"   • PBS SPI (weekly inflation) — every Friday\n"
            f"   • T-bill auctions — see 'Next auctions' above\n"
            f"   • CPI inflation — first working days of the month\n"
            f"   • Current account / remittances — mid-month (SBP)\n"
            f"   • Petrol price revision — 15th & last day of month\n\n"
            f"{section('🌐', 'Global')}\n{global_block(mk, cfg)}\n\n"
            f"{section('📰', 'Biggest stories of the last 36h')}\n{headlines_block(state, 36, 10)}"
            + footer(cfg, ["WeekAhead", "KSE100"]))
    card = None
    if cfg.get("brand", {}).get("cards", True):
        from .cards import morning_card
        card = _safe_card(morning_card, cfg, now, scs.indices(), state.snap("sbp") or {}, mk, kicker="Week Ahead")
    return text, card, f"📅 <b>PSX Week Ahead</b> · week of {monday:%d %b} — full details below 👇"


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
