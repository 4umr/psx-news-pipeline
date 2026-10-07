"""Data watchers: turn changes in official numbers into instant alerts."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .common import Alert, arrow, esc, fmt_num, fmt_pct, in_window, link, log, now_pkt, now_utc
from .sources import scs
from .state import State
from .style import header

SBP_URL = "https://www.sbp.org.pk/"
SCS_URL = "https://www.scstrade.com/"


# ---------------------------------------------------------------- formatting
def trend(x: float | None) -> str:
    """Neutral direction marker (for inflation etc. where up/down isn't good/bad)."""
    if not x:
        return "➡️"
    return "⬆️" if x > 0 else "⬇️"


def bps(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return ""
    d = round((new - old) * 100)
    return "(unch)" if d == 0 else f"({d:+d} bps)"


def usd_bn(m: float | None) -> str:
    return "n/a" if m is None else f"${m / 1000:,.2f}bn"


def tenor_line(yields: dict, old: dict | None = None) -> str:
    parts = []
    for t, y in yields.items():
        if y is None:
            parts.append(f"{t}: rejected")
        else:
            delta = bps(y, (old or {}).get(t)) if old else ""
            parts.append(f"{t}: <b>{y:.2f}%</b> {delta}".strip())
    return "\n".join(f"  • {p}" for p in parts)


# ---------------------------------------------------------------- SBP
def _suspicious(what: str, detail: str) -> Alert:
    """A data-quality warning for the owner only — the channel never sees questionable numbers."""
    return Alert(f"🧐 <b>Data check — not posted to channel</b>\n{esc(what)}: {esc(detail)}\n"
                 "This looks like a parsing/source glitch. If it's real, it will be confirmed by the news feed.",
                 0, f"sus:{what}", admin=True)


def _sbp_sane(new: dict, old: dict) -> list[str]:
    """Return a list of problems with a freshly parsed SBP snapshot (empty = looks fine)."""
    bad = []
    pr, opr = new.get("policy_rate"), old.get("policy_rate")
    if pr is not None and not (3 <= pr <= 30):
        bad.append(f"policy rate {pr}% out of range")
    if pr is not None and opr is not None and abs(pr - opr) > 5:
        bad.append(f"policy rate jump {opr}% → {pr}%")
    r, o = new.get("reserves"), old.get("reserves")
    if r and o and r.get("sbp") and o.get("sbp") and abs(r["sbp"] - o["sbp"]) > 5000:
        bad.append(f"SBP reserves jump {o['sbp']:.0f} → {r['sbp']:.0f} USD m")
    for key in ("mtb", "pib"):
        for t, y in ((new.get(key) or {}).get("yields") or {}).items():
            if y is not None and not (2 <= y <= 35):
                bad.append(f"{key.upper()} {t} yield {y}% out of range")
    return bad


def sbp_changes(new: dict, state: State) -> list[Alert]:
    if not new:
        return []
    old = state.snap("sbp") or {}
    alerts: list[Alert] = []
    problems = _sbp_sane(new, old)
    if problems:
        k = f"sus:sbp:{'|'.join(problems)}"
        if not state.flag(k):
            state.set_flag(k)
            alerts.append(_suspicious("SBP homepage numbers", "; ".join(problems)))
        return alerts  # keep the previous good snapshot
    if old:
        pr_new, pr_old = new.get("policy_rate"), old.get("policy_rate")
        if pr_new is not None and pr_old is not None and pr_new != pr_old:
            from . import street
            move = "cut" if pr_new < pr_old else "hike"
            called = street.score_mpc(state, round((pr_new - pr_old) * 100))
            alerts.append(Alert(
                header("🚨", f"SBP Policy Rate {move}", "Breaking · State Bank of Pakistan") + "\n"
                f"🏦 Policy rate: <b>{pr_old:.2f}% ➜ {pr_new:.2f}%</b> {bps(pr_new, pr_old)}\n\n"
                f"💡 The policy rate sets bank lending/deposit rates and T-bill "
                f"yields; cuts usually support equity valuations, hikes weigh on them.\n"
                f"🏭 In focus: Banks · Cement · Autos · Steel · Fertilizer\n"
                + (f"\n{called}\n" if called else "") +
                f"🔗 {link(SBP_URL, 'State Bank of Pakistan')}", 10, f"sbp:pr:{pr_new}",
                tags=["SBP", "PolicyRate", "InterestRates"]))

        r_new, r_old = new.get("reserves"), old.get("reserves")
        if r_new and r_old and r_new.get("as_on") != r_old.get("as_on"):
            d_sbp = (r_new["sbp"] or 0) - (r_old["sbp"] or 0)
            d_tot = (r_new["total"] or 0) - (r_old["total"] or 0)
            alerts.append(Alert(
                header("💵", "SBP Forex Reserves", f"Weekly update · as on {r_new['as_on']}") + "\n"
                f"{arrow(d_sbp)} SBP reserves: <b>{usd_bn(r_new['sbp'])}</b> ({d_sbp:+,.0f}m WoW)\n"
                f"{arrow(d_tot)} Total liquid: <b>{usd_bn(r_new['total'])}</b> ({d_tot:+,.0f}m WoW)\n"
                f"▫️ Commercial banks: {usd_bn(r_new['banks'])}\n\n"
                f"💡 Rising reserves support the rupee and investor confidence; "
                f"falling reserves add external pressure.\n"
                f"🔗 {link(SBP_URL, 'State Bank of Pakistan')}", 9, f"sbp:res:{r_new['as_on']}",
                tags=["Reserves", "SBP", "PKR"]))

        for key, name in (("mtb", "T-Bill (MTB)"), ("pib", "Fixed-rate PIB")):
            n, o = new.get(key), old.get(key)
            if n and o and n.get("as_on") != o.get("as_on") and n.get("yields"):
                alerts.append(Alert(
                    header("📜", f"{name} Auction Result", f"Auction of {n['as_on']}") + "\n"
                    f"Cut-off yields (change vs previous auction):\n{tenor_line(n['yields'], o.get('yields'))}\n"
                    f"🏦 Policy rate: {fmt_num(new.get('policy_rate'))}%\n\n"
                    f"💡 Cut-off yields show where the market expects rates "
                    f"to go — falling yields often front-run policy rate cuts.\n"
                    f"🔗 {link(SBP_URL, 'State Bank of Pakistan')}", 9, f"sbp:{key}:{n['as_on']}",
                    tags=["TBills" if key == "mtb" else "PIB", "Yields", "SBP"]))
    merged = {**old, **new}
    state.set_snap("sbp", merged)
    return alerts


# ---------------------------------------------------------------- results
def corporate_results(rows: list[dict], state: State, kse100: set[str]) -> list[Alert]:
    cutoff = now_pkt() - timedelta(days=4)
    fresh = []
    for r in rows:
        key = f"res:{r.get('company_code')}:{r.get('bm_year')}:{r.get('bm_quarter_number')}:{r.get('bm_PDFLink')}"
        if state.seen(key):
            continue
        state.mark(key)
        if r.get("date") and r["date"] < cutoff:
            continue
        fresh.append(r)
    if not fresh:
        return []
    fresh.sort(key=lambda r: (r.get("company_code") not in kse100, r.get("company_code") or ""))
    limit = 40
    lines = []
    for r in fresh[:limit]:
        code = r.get("company_code", "")
        star = "⭐" if code in kse100 else "▫️"
        bits = []
        if r.get("bm_eps_quarter"):
            eps = f"EPS {r['bm_eps_quarter']}"
            if r.get("bm_eps_cum") and r["bm_eps_cum"] != r["bm_eps_quarter"]:
                eps += f" (cum {r['bm_eps_cum']})"
            bits.append(eps)
        if r.get("bm_dividend", "").strip():
            bits.append(f"💰Div {esc(r['bm_dividend'].strip())}")
        if r.get("bm_bonus", "").strip():
            bits.append(f"🎁Bonus {esc(r['bm_bonus'].strip())}")
        if r.get("bm_right_per", "").strip():
            bits.append(f"Right {esc(r['bm_right_per'].strip())}")
        period = esc(r.get("bm_quarter_number") or "")
        doc = link(r["bm_PDFLink"], "📄") if r.get("bm_PDFLink") else ""
        yoy = _fy_yoy(code, r) if code in kse100 else ""
        lines.append(f"{star}<b>{esc(code)}</b> {period}: {' · '.join(bits) or 'see filing'}{yoy} {doc}".rstrip())
    more = f"\n…and {len(fresh) - limit} more" if len(fresh) > limit else ""
    has_major = any(r.get("company_code") in kse100 for r in fresh)
    text = (header("📊", "Corporate Results & Payouts", f"{len(fresh)} new announcement(s)") + "\n"
            f"⭐ KSE-100 company · EPS in Rs · 📄 PSX filing\n\n" + "\n".join(lines) + more +
            f"\n\n🔗 {link(SCS_URL + 'MarketStatistics/MS_Announcements.aspx', 'SCS Trade')} · filings from PSX")
    return [Alert(text, 8 if has_major else 7, "results", tags=["Results", "Dividends", "Corporate"])]


def _fy_yoy(code: str, r: dict) -> str:
    """Full-year EPS vs last year, from the company's PSX page (annual results only)."""
    m = re.match(r"FY(\d{2})$", (r.get("bm_quarter_number") or "").strip())
    if not m:
        return ""
    try:
        cur = float((r.get("bm_eps_cum") or r.get("bm_eps_quarter") or "").strip())
    except ValueError:
        return ""
    from .sources import psx
    page = psx.company(code)
    prev = (page or {}).get("annual_eps", {}).get(str(2000 + int(m.group(1)) - 1))
    if prev is None:
        return ""
    if prev > 0:
        return f" · <b>{(cur / prev - 1) * 100:+.0f}% YoY</b> (LY {prev:.2f})"
    return f" · LY {prev:.2f}"


# ---------------------------------------------------------------- FIPI
def fipi_block(data: dict, usdpkr: float) -> str:
    s = data["summary"]
    f, l = s.get("FIPI", {}), s.get("LIPI", {})
    out = [f"{arrow(f.get('net'))} Foreign (FIPI): <b>net {f.get('net', 0):+.2f}m USD</b> "
           f"(buy {abs(f.get('buy') or 0):.2f} / sell {abs(f.get('sell') or 0):.2f})",
           f"{arrow(l.get('net'))} Local (LIPI): <b>net {l.get('net', 0):+.2f}m USD</b>"]
    for kind, title in (("lipi", "Locals"), ("fipi", "Foreigners")):
        parts = sorted(data.get(kind, {}).items(), key=lambda kv: kv[1])
        parts = [f"{k} {v / usdpkr / 1e6:+.2f}" for k, v in parts if abs(v / usdpkr) >= 10_000]
        if parts:
            out.append(f"  {title} (USD m): " + " · ".join(parts))
    return "\n".join(out)


def fipi_sectors(day) -> str:
    """Where foreigners bought / sold, by sector (USD m)."""
    d = day.strftime("%m/%d/%Y")
    rows = [r for r in scs._post("FIPILIPI.aspx/loadfipisector", {"date1": d, "date2": d})
            if (r.get("FLTypeNew") or "").strip().upper() == "FIPI" and r.get("FLNetValueUSD") is not None
            and "all other" not in (r.get("FLSectorName") or "").lower()]
    if not rows:
        return ""
    rows.sort(key=lambda r: r["FLNetValueUSD"])
    buy = [r for r in reversed(rows[-3:]) if r["FLNetValueUSD"] > 0.005]
    sell = [r for r in rows[:3] if r["FLNetValueUSD"] < -0.005]
    from .briefs import _sector_name
    name = lambda r: esc(_sector_name(re.sub(r"\s+and\s+", " & ",  # noqa: E731
                                             re.sub(r"\s+", " ", r["FLSectorName"].replace("(mn$)", "")),
                                             flags=re.I).strip()))
    out = ["🏭 <b>Foreigners by sector (USD m)</b>"]
    if buy:
        out.append("🟢 Bought: " + " · ".join(f"{name(r)} {r['FLNetValueUSD']:+.2f}" for r in buy))
    if sell:
        out.append("🔴 Sold: " + " · ".join(f"{name(r)} {r['FLNetValueUSD']:+.2f}" for r in sell))
    return "\n".join(out) if len(out) > 1 else ""


def fipi_alert(now, state: State) -> list[Alert]:
    if now.weekday() > 4 or now.hour < 16:
        return []
    key = f"fipi:{now:%Y-%m-%d}"
    if state.flag(key):
        return []
    data = scs.fipi(now)
    if not data:
        return []
    state.set_flag(key)
    state.set_snap("fipi_last", data)
    net = data["summary"].get("FIPI", {}).get("net")
    if net is not None:
        daily_rec(state, data["date"])["fipi"] = net
    rate = ((state.snap("sbp") or {}).get("usdpkr") or {}).get("m2m") or 280.0
    sectors = fipi_sectors(now)
    text = (header("🌍", "Investor Flows · FIPI / LIPI", f"{now:%A %d %b %Y}") + "\n" + fipi_block(data, rate) +
            (f"\n\n{sectors}" if sectors else "") +
            f"\n\n💡 Sustained foreign buying or selling is a key driver of PSX direction.\n"
            f"🔗 {link(SCS_URL + 'FIPILIPI.aspx', 'SCS Trade / NCCPL')}")
    return [Alert(text, 8, key, tags=["FIPI", "ForeignFlows"])]


# ---------------------------------------------------------------- KSE-100 moves
def contributors(view: list[dict], n: int = 5) -> tuple[list, list]:
    rows = [r for r in view if r.get("NetIndexPoint") is not None]
    rows.sort(key=lambda r: r["NetIndexPoint"])
    neg = [r for r in rows[:n] if r["NetIndexPoint"] < 0]
    pos = [r for r in reversed(rows[-n:]) if r["NetIndexPoint"] > 0]
    return pos, neg


def contrib_line(rows: list[dict]) -> str:
    return " · ".join(f"{esc(r['company_code'])} {r['NetIndexPoint']:+.0f}" for r in rows) or "—"


def kse_moves(now, state: State, cfg: dict) -> tuple[list[Alert], list[dict]]:
    mh = cfg["market_hours"]
    if now.weekday() > 4 or not in_window(now, mh["start"], mh["end"]):
        return [], []
    view = scs.kse100_view()
    if not view:
        return [], []
    cur, pre = view[0].get("CurrentIndex"), view[0].get("PreIndex")
    if not cur or not pre:
        return [], view
    last_close = state.snap("kse_last_close")
    if last_close and abs(pre - last_close) > 1:
        return [], view  # data still shows a previous session (pre-open / holiday)
    if not last_close and now.hour < 10:
        return [], view
    pct = (cur / pre - 1) * 100
    if abs(pct) > 12:  # PSX index halts long before this — almost certainly bad data
        k = f"sus:kse:{now:%Y-%m-%d}"
        if state.flag(k):
            return [], view
        state.set_flag(k)
        return [_suspicious("KSE-100 intraday", f"{pct:+.1f}% ({pre:,.0f} → {cur:,.0f})")], view
    hit = None
    for th in sorted(cfg["kse100_move_alerts_pct"]):
        k = f"kse:{now:%Y-%m-%d}:{'up' if pct > 0 else 'dn'}:{th}"
        if abs(pct) >= th and not state.flag(k):
            state.set_flag(k)
            hit = th
    stock_alerts = stock_moves(now, state, cfg, view)
    if hit is None:
        return stock_alerts, view
    pos, neg = contributors(view)
    icon = "🚀" if pct > 0 else "🚨"
    direction = "up" if pct > 0 else "down"
    text = (header(icon, f"KSE-100 {direction} {abs(pct):.2f}% intraday", f"Market alert · {now:%H:%M} PKT") + "\n"
            f"📈 KSE-100: <b>{cur:,.0f}</b> ({cur - pre:+,.0f} pts)\n\n"
            f"🟢 <b>Lifting:</b> {contrib_line(pos)}\n"
            f"🔴 <b>Dragging:</b> {contrib_line(neg)}\n\n"
            f"💡 Check the news feed for the trigger before reacting.\n"
            f"🔗 {link(SCS_URL + 'MarketStatistics/MS_IndexView.aspx', 'SCS Trade index view')}")
    return [Alert(text, 10 if abs(pct) >= 3 else 9, f"kse:{hit}", tags=["KSE100", "MarketAlert"])] + stock_alerts, view


def record_close(now, state: State) -> list[dict]:
    """After the session, store the KSE-100 close (used for freshness checks & weekly change)."""
    if now.weekday() > 4 or (now.hour, now.minute) < (16, 50):
        return []
    key = f"closerec:{now:%Y-%m-%d}"
    if state.flag(key):
        return []
    idx = scs.indices()
    k100 = next((r for r in idx if r["kse_index_type"].replace(" ", "") == "KSE100"), None)
    if not k100:
        return idx
    state.set_flag(key)
    close = k100["kse_index_close"]
    closes = state.data["kse_closes"]
    if not closes or abs(list(closes.values())[-1] - close) > 0.01:
        closes[f"{now:%Y-%m-%d}"] = close
        state.set_snap("kse_session_date", f"{now:%Y-%m-%d}")  # a new session actually traded today
        # keep each session's KSE-100 constituent closes for the weekly review
        px = {r["company_code"]: r["CurrentPrice"] for r in scs.kse100_view() if r.get("CurrentPrice")}
        rec = daily_rec(state, f"{now:%Y-%m-%d}")
        if px:
            rec["px"] = px
        vol = {r["company_code"]: r["trading_vol"] for r in scs.daily_activity() if r.get("trading_vol")}
        if vol:
            rec["vol"] = vol  # history for the unusual-volume detector
    state.set_snap("kse_last_close", close)
    return idx


def daily_rec(state: State, day: str) -> dict:
    """Per-session record (prices, FIPI) kept ~3 weeks for weekly reviews."""
    daily = state.data.setdefault("daily", {})
    for d in sorted(daily)[:-15]:
        del daily[d]
    return daily.setdefault(day, {})


# ---------------------------------------------------------------- stock moves
def stock_moves(now, state: State, cfg: dict, view: list[dict]) -> list[Alert]:
    """KSE-100 stocks with unusually large moves, plus the user's watchlist (one alert per run)."""
    if not view:
        return []
    big = cfg.get("big_stock_move_pct", 7.5)
    wl = {s.upper(): float(p) for s, p in (cfg.get("watchlist") or {}).items()}
    hits = []
    for r in view:
        code, cur, ldcp = r.get("company_code"), r.get("CurrentPrice"), r.get("LDCP")
        if not code or not cur or not ldcp:
            continue
        pct = (cur / ldcp - 1) * 100
        th = min(big, wl.get(code, big))
        if abs(pct) < th:
            continue
        # PSX circuit: ±10% or Rs1, whichever is higher — larger moves on Rs10+ stocks are bad data
        if abs(pct) > 10.5 and abs(cur - ldcp) > 1.05:
            continue
        k = f"stk:{now:%Y-%m-%d}:{code}:{'up' if pct > 0 else 'dn'}"
        if state.flag(k):
            continue
        state.set_flag(k)
        hits.append((code, pct, cur, code in wl))
    if not hits:
        return []
    hits.sort(key=lambda h: -abs(h[1]))
    lines = []
    for code, pct, cur, mine in hits:
        note = " · near circuit" if abs(pct) >= 9.5 else ""
        star = "👁️ " if mine else ""
        lines.append(f"{arrow(pct)} {star}<b>{esc(code)}</b> {pct:+.2f}% → Rs {cur:,.2f}{note}")
    text = (header("⚡", "Big stock moves", f"KSE-100 / watchlist · {now:%H:%M} PKT") + "\n"
            + "\n".join(lines) +
            "\n\n💡 Large single-stock moves often follow company news — check announcements before reacting."
            + (f"\n👁️ = on your watchlist" if any(h[3] for h in hits) else ""))
    return [Alert(text, 8, "stocks", tags=["StockAlert", "KSE100"])]


# ---------------------------------------------------------------- PBS: CPI / SPI with numbers
def pbs_releases(state: State) -> list[Alert]:
    from .sources import pbs

    alerts = []
    rels = []
    for rel in pbs.releases():
        t = rel["title"].lower()
        kind = "cpi" if "inflation report" in t else ("spi" if "sensitive price" in t else None)
        if kind and rel["url"]:
            rels.append((kind, rel))
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    rels.sort(key=lambda kr: kr[1]["published"] or epoch)  # oldest -> newest
    latest = {k: r["url"] for k, r in rels}                 # newest url per kind
    for kind, rel in rels:
        url = rel["url"]
        fresh = not state.seen(url)
        is_latest = latest[kind] == url
        if not fresh and not (is_latest and not state.snap(kind)):
            continue
        state.mark(url)  # the generic news item for this release is now redundant
        recent = rel["published"] is None or (now_utc() - rel["published"]).days < 3
        if not is_latest and not (fresh and recent):
            continue
        d = pbs.cpi_details(url) if kind == "cpi" else pbs.spi_details(url)
        if not d:
            continue
        if is_latest:
            state.set_snap(kind, d)
        if not (fresh and recent):
            continue
        if kind == "cpi":
            alerts.append(_cpi_alert(d, url, state))
        else:
            alerts.append(Alert(
                    header("🛒", "Weekly SPI (Sensitive Price Indicator)", f"Week ended {d['week']} · PBS") + "\n"
                    f"{trend(d['wow'])} SPI: <b>{d['index']:.2f}</b> ({d['wow']:+.2f}% WoW)\n\n"
                    f"💡 SPI tracks weekly prices of essential items — an early "
                    f"signal for monthly CPI and the SBP's rate path.\n"
                    f"🔗 {link(url, 'Pakistan Bureau of Statistics')}", 8, f"spi:{d['week']}",
                    tags=["SPI", "Inflation"]))
    return alerts


def _cpi_alert(d: dict, url: str, state: State) -> Alert:
    g = d["general"]
    lines = [header("📈", f"CPI Inflation · {g['month']}", "Official release · Pakistan Bureau of Statistics"),
             f"{trend(g['yoy'] - g['prev_yoy'])} National CPI: <b>{g['yoy']:.1f}% YoY</b> "
             f"(previous month {g['prev_yoy']:.1f}%) · MoM {g['mom']:+.1f}%"]
    for k in ("urban", "rural"):
        if v := d.get(k):
            lines.append(f"▫️ {k.title()}: {v['yoy']:.1f}% YoY (prev {v['prev_yoy']:.1f}%)")
    if d.get("spi_yoy") is not None:
        lines.append(f"▫️ SPI: {d['spi_yoy']:.1f}% YoY · WPI: {d.get('wpi_yoy', 0):.1f}% YoY")
    pr = (state.snap("sbp") or {}).get("policy_rate")
    if pr is not None:
        real = pr - g["yoy"]
        lines.append(f"🏦 Real policy rate: <b>{real:+.1f}%</b> (policy {pr:.2f}% − CPI {g['yoy']:.1f}%)")
    from . import street
    if called := street.score_cpi(state, g["yoy"]):
        lines += ["", called]
    lines += ["", "💡 Inflation is the main input for the SBP's next rate decision; "
              "a positive real rate gives room for cuts, a negative one pressure for hikes.",
              "🏭 In focus: Banks · Cement · FMCG · Autos",
              f"🔗 {link(url, 'Pakistan Bureau of Statistics')} · {link(d['doc'], 'full review')}"]
    return Alert("\n".join(lines), 10, f"cpi:{g['month']}", tags=["CPI", "Inflation", "SBP"])


# ---------------------------------------------------------------- MPC calendar
def mpc_watch(now, state: State) -> list[Alert]:
    """Refresh SBP's MPC calendar daily; remind the evening before a meeting."""
    from .sources.sbp import mpc_calendar

    today = f"{now:%Y-%m-%d}"
    snap = state.snap("mpc") or {}
    if snap.get("fetched") != today:
        dates = mpc_calendar()
        if dates:
            snap = {"fetched": today, "dates": dates}
            state.set_snap("mpc", snap)
    nxt = next_mpc(state, now)
    if not nxt:
        return []
    days = (nxt - now.date()).days
    if days == 0:  # remember the pre-decision rate, to score 'hold' calls the next morning
        at = state.snap("policy_rate_at") or {}
        if str(nxt) not in at and (pr0 := (state.snap("sbp") or {}).get("policy_rate")) is not None:
            at[str(nxt)] = pr0
            state.set_snap("policy_rate_at", at)
    k = f"mpc_remind:{nxt}"
    if days == 1 and now.hour >= 18 and not state.flag(k):
        state.set_flag(k)
        pr = (state.snap("sbp") or {}).get("policy_rate")
        cpi = (state.snap("cpi") or {}).get("general")
        ctx = []
        if pr is not None:
            ctx.append(f"🏦 Current policy rate: <b>{pr:.2f}%</b>")
        if cpi:
            ctx.append(f"📈 Latest CPI: {cpi['yoy']:.1f}% YoY ({cpi['month']})")
            if pr is not None:
                ctx.append(f"⚖️ Real policy rate: {pr - cpi['yoy']:+.1f}%")
        if sig := rate_signal(state.snap("sbp") or {}):
            ctx.append(sig)
        from . import street
        if calls := street.pending_calls(state, "mpc"):
            ctx.append(f"🧠 Street calls: {calls}")
        return [Alert(header("🏦", "SBP MPC meeting tomorrow", f"{nxt:%A %d %B %Y} · SBP Monetary Policy Committee")
                      + "\n" + "\n".join(ctx) +
                      "\n\n💡 The decision is usually announced in the afternoon/evening — we'll alert you "
                      "the moment it's out.\n🔗 " + link("https://www.sbp.org.pk/our-operations/monetary-policy",
                                                         "SBP MPC calendar"),
                      9, k, tags=["SBP", "MPC", "PolicyRate"])]
    return []


def rate_signal(sbp: dict) -> str:
    """What T-bill cut-offs say about rate expectations vs the policy rate (a market reading, not a forecast)."""
    pr, y = sbp.get("policy_rate"), ((sbp.get("mtb") or {}).get("yields") or {})
    if pr is None or not y.get("6-M"):
        return ""
    s3 = round((y["3-M"] - pr) * 100) if y.get("3-M") else None
    s6 = round((y["6-M"] - pr) * 100)
    if s6 <= -50:
        read = "market is pricing rate CUTS"
    elif s6 >= 50:
        read = "market is pricing NO cut (or a possible hike)"
    else:
        read = "market is broadly neutral on the next move"
    spreads = (f"3M {s3:+d} bps · " if s3 is not None else "") + f"6M {s6:+d} bps"
    return f"📡 T-bills vs policy rate: {spreads} → {read}"


def mpc_hold_check(now, state: State) -> None:
    """Morning after an MPC meeting: if the policy rate didn't change, score 'hold' forecasts."""
    from . import street

    nxt_prev = [d for d in (state.snap("mpc") or {}).get("dates", []) if d < f"{now:%Y-%m-%d}"]
    if not nxt_prev or now.hour < 10:
        return
    last = nxt_prev[-1]
    k = f"mpc_scored:{last}"
    if state.flag(k) or (now.date() - datetime.fromisoformat(last).date()).days > 3:
        return
    state.set_flag(k)
    pr_on = (state.snap("policy_rate_at") or {}).get(last)
    pr_now = (state.snap("sbp") or {}).get("policy_rate")
    if pr_now is not None and (pr_on is None or pr_on == pr_now):
        street.score_mpc(state, 0)


# ---------------------------------------------------------------- PSX company filings
NOISE = re.compile(r"transmission of|unclaimed|e-?dividend|zakat|change of (registered )?address|dividend warrant|"
                   r"credit of|duplicate|lost share|cdc|annual general meeting|notice of agm|closed period|"
                   r"resolutions passed|proxy|video recording|dissemination of", re.I)


def psx_filings(now, state: State, cfg: dict, view: list[dict]) -> list[Alert]:
    """New company filings on PSX (material information, results, corporate actions).

    Checks your watchlist every run and rotates through the KSE-100 (heaviest stocks first),
    so each constituent is checked every few runs without hammering PSX.
    """
    from concurrent.futures import ThreadPoolExecutor

    from .sources import psx

    wl = [s.upper() for s in (cfg.get("watchlist") or {})]
    ranked = [r["company_code"] for r in sorted(view, key=lambda r: -(r.get("IndexWeightCur") or 0))]
    if not ranked:
        return []
    per_run = cfg.get("psx_filings_per_run", 30)
    cur = state.data.get("psx_cursor", 0) % len(ranked)
    batch = (ranked + ranked)[cur:cur + per_run]
    state.data["psx_cursor"] = cur + per_run
    symbols = list(dict.fromkeys(wl + ranked[:10] + batch))  # top-10 weights checked every run
    with ThreadPoolExecutor(max_workers=8) as ex:
        pages = [p for p in ex.map(psx.company, symbols) if p]
    known = set(state.data.setdefault("psx_known", []))
    px = {r["company_code"]: r for r in view}
    fresh = []
    since = f"{now - timedelta(days=2):%Y-%m-%d}"
    for p in pages:
        sym = p["symbol"]
        first_time = sym not in known
        for f in p["filings"]:
            key = f"psx:{sym}:{f['url'] or f['title']}"
            if state.seen(key):
                continue
            state.mark(key)
            if first_time or f["date"] < since or f["kind"] == "board" or NOISE.search(f["title"]):
                continue
            fresh.append((sym, f))
        if first_time:
            known.add(sym)
        state.set_snap(f"eps:{sym}", {"annual": p["annual_eps"], "quarterly": p["quarterly_eps"]})
    state.data["psx_known"] = sorted(known)
    if not fresh:
        return []
    lines, done = [], set()
    for sym, f in fresh:
        if (sym, f["title"].lower()) in done:  # same notice filed twice (e.g. daily buy-back reports)
            continue
        done.add((sym, f["title"].lower()))
        r = px.get(sym, {})
        price = ""
        if r.get("CurrentPrice") and r.get("LDCP"):
            pct = (r["CurrentPrice"] / r["LDCP"] - 1) * 100
            price = f" · Rs {r['CurrentPrice']:,.2f} ({pct:+.1f}%)"
        star = "👁️" if sym in wl else "⭐"
        title = f["title"]
        if re.search(r"disclosure of interest", title, re.I):
            title = "👤 Insider dealing disclosure (director / executive / major shareholder traded shares)"
        elif "material" in title.lower():
            title = "📢 " + title
        doc = f" · {link(f['url'], 'view filing')}" if f["url"] else ""
        detail = " · ".join(x for x in (price.lstrip(" ·"), doc.lstrip(" ·")) if x)
        lines.append(f"{star} <b>{esc(sym)}</b> — {esc(title[:140])}" + (f"\n      {detail}" if detail else ""))
    text = (header("📢", "New company filings on PSX", "Straight from the exchange") + "\n"
            + "\n".join(lines) +
            "\n\n💡 Company disclosures often reach PSX before the news media — open the filing for full details.")
    return [Alert(text, 9, "psxfilings", tags=["PSXFilings", "MaterialInformation"])]


# ---------------------------------------------------------------- unusual volume
def unusual_volume(now, state: State, cfg: dict, kse100: set[str]) -> list[Alert]:
    """Volume running at a multiple of the 10-session average — often a footprint of news or big money."""
    mh = cfg["market_hours"]
    if now.weekday() > 4 or not in_window(now, "10:30", mh["end"]):
        return []
    daily = state.data.get("daily", {})
    hist = [daily[d]["vol"] for d in sorted(daily) if daily[d].get("vol") and d < f"{now:%Y-%m-%d}"][-10:]
    if len(hist) < 5:
        return []  # needs about a week of history first
    act = scs.daily_activity()
    wl = {s.upper() for s in (cfg.get("watchlist") or {})}
    mult = cfg.get("unusual_volume_x", 3.0)
    hits = []
    for r in act:
        code, vol = r.get("company_code"), r.get("trading_vol") or 0
        if code not in kse100 and code not in wl:
            continue
        past = [h.get(code, 0) for h in hist]
        avg = sum(past) / len(past) if past else 0
        if avg < 50_000 or vol < max(mult * avg, 500_000):
            continue
        k = f"vol:{now:%Y-%m-%d}:{code}"
        if state.flag(k):
            continue
        state.set_flag(k)
        hits.append((code, vol, vol / avg, r.get("trading_close"), r.get("trading_change")))
    if not hits:
        return []
    hits.sort(key=lambda h: -h[2])
    lines = []
    for code, vol, x, close, chg in hits[:12]:
        pct = ""
        if close and chg is not None and close - chg:
            pct = f" · price {chg / (close - chg) * 100:+.1f}%"
        lines.append(f"🔎 <b>{esc(code)}</b> {vol / 1e6:,.2f}m shares — <b>{x:.1f}×</b> its 10-day average{pct}")
    text = (header("🔎", "Unusual volume", f"KSE-100 / watchlist · {now:%H:%M} PKT") + "\n" + "\n".join(lines) +
            "\n\n💡 Volume spikes often come before or with news — check filings and headlines.")
    return [Alert(text, 8, "volume", tags=["UnusualVolume", "KSE100"])]


def next_mpc(state: State, now):
    from datetime import date

    for d in (state.snap("mpc") or {}).get("dates", []):
        dd = date.fromisoformat(d)
        if dd >= now.date():
            return dd
    return None


# ---------------------------------------------------------------- global markets
GLOBAL_WHY = {
    "BZ=F": "Pakistan imports most of its oil — higher prices widen the import bill and inflation; good for E&P stocks.",
    "CL=F": "Oil price moves feed into Pakistan's import bill, inflation and E&P/OMC earnings.",
    "GC=F": "Gold moves affect local gold prices and signal global risk appetite.",
    "DX-Y.NYB": "A stronger dollar pressures emerging-market currencies including PKR.",
    "^GSPC": "Big Wall Street moves set global risk sentiment, which often spills into frontier markets.",
    "^TNX": "Higher US yields pull money out of emerging/frontier markets.",
    "^VIX": "A VIX spike signals global fear / risk-off.",
    "CNY=X": "Yuan moves matter for Pakistan's China trade and CPEC-linked financing.",
    "BTC-USD": "Large crypto swings reflect speculative risk appetite.",
}


def global_moves(mk: dict, state: State, cfg: dict) -> list[Alert]:
    alerts = []
    for sym, (name, unit, th) in cfg["global_markets"].items():
        d = mk.get(sym)
        if not d or d["pct"] is None or abs(d["pct"]) < th or abs(d["pct"]) > 30:
            continue
        k = f"g:{sym}:{d['date']}"
        if state.flag(k):
            continue
        state.set_flag(k)
        val = f"{unit}{d['last']:,.2f}" if unit == "$" else f"{d['last']:,.2f}{unit if unit == '%' else ''}"
        alerts.append(Alert(
            header("🌐", f"{name} {fmt_pct(d['pct'], 1)}", "Global market move") + "\n"
            f"{arrow(d['pct'])} {esc(name)}: <b>{val}</b> ({fmt_pct(d['pct'], 1)} today)\n\n"
            f"💡 {GLOBAL_WHY.get(sym, '')}", 8, k, tags=["GlobalMarkets"]))
    if alerts:
        log.info("global alerts: %d", len(alerts))
    return alerts
