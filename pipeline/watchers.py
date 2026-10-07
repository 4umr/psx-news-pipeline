"""Data watchers: turn changes in official numbers into instant alerts."""
from __future__ import annotations

from datetime import timedelta

from .common import Alert, arrow, esc, fmt_num, fmt_pct, in_window, link, log, now_pkt
from .sources import scs
from .state import State
from .style import header

SBP_URL = "https://www.sbp.org.pk/"
SCS_URL = "https://www.scstrade.com/"


# ---------------------------------------------------------------- formatting
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
def sbp_changes(new: dict, state: State) -> list[Alert]:
    if not new:
        return []
    old = state.snap("sbp") or {}
    alerts: list[Alert] = []
    if old:
        pr_new, pr_old = new.get("policy_rate"), old.get("policy_rate")
        if pr_new is not None and pr_old is not None and pr_new != pr_old:
            move = "CUT" if pr_new < pr_old else "HIKE"
            alerts.append(Alert(
                header("🚨", f"SBP Policy Rate {move}", "Breaking · State Bank of Pakistan") + "\n"
                f"🏦 Policy rate: <b>{pr_old:.2f}% ➜ {pr_new:.2f}%</b> {bps(pr_new, pr_old)}\n\n"
                f"📌 <b>Why it matters:</b> The policy rate sets bank lending/deposit rates and T-bill "
                f"yields; cuts usually support equity valuations, hikes weigh on them.\n"
                f"🏭 <b>Sectors in focus:</b> Banks · Cement · Autos · Steel · Fertilizer\n"
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
                f"📌 <b>Why it matters:</b> Rising reserves support the rupee and investor confidence; "
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
                    f"📌 <b>Why it matters:</b> Cut-off yields show where the market expects rates "
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
        lines.append(f"{star}<b>{esc(code)}</b> {period}: {' · '.join(bits) or 'see filing'} {doc}".rstrip())
    more = f"\n…and {len(fresh) - limit} more" if len(fresh) > limit else ""
    has_major = any(r.get("company_code") in kse100 for r in fresh)
    text = (header("📊", "Corporate Results & Payouts", f"{len(fresh)} new announcement(s)") + "\n"
            f"⭐ KSE-100 company · EPS in Rs · 📄 PSX filing\n\n" + "\n".join(lines) + more +
            f"\n\n🔗 {link(SCS_URL + 'MarketStatistics/MS_Announcements.aspx', 'SCS Trade')} · filings from PSX")
    return [Alert(text, 8 if has_major else 7, "results", tags=["Results", "Dividends", "Corporate"])]


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
    rate = ((state.snap("sbp") or {}).get("usdpkr") or {}).get("m2m") or 280.0
    text = (header("🌍", "Investor Flows · FIPI / LIPI", f"{now:%A %d %b %Y}") + "\n" + fipi_block(data, rate) +
            f"\n\n📌 <b>Why it matters:</b> Sustained foreign buying or selling is a key driver of PSX direction.\n"
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
    hit = None
    for th in sorted(cfg["kse100_move_alerts_pct"]):
        k = f"kse:{now:%Y-%m-%d}:{'up' if pct > 0 else 'dn'}:{th}"
        if abs(pct) >= th and not state.flag(k):
            state.set_flag(k)
            hit = th
    if hit is None:
        return [], view
    pos, neg = contributors(view)
    icon = "🚀" if pct > 0 else "🚨"
    direction = "UP" if pct > 0 else "DOWN"
    text = (header(icon, f"KSE-100 {direction} {abs(pct):.2f}% intraday", f"Market alert · {now:%H:%M} PKT") + "\n"
            f"📈 KSE-100: <b>{cur:,.0f}</b> ({cur - pre:+,.0f} pts)\n\n"
            f"🟢 <b>Lifting:</b> {contrib_line(pos)}\n"
            f"🔴 <b>Dragging:</b> {contrib_line(neg)}\n\n"
            f"📌 Check the news feed for the trigger before reacting.\n"
            f"🔗 {link(SCS_URL + 'MarketStatistics/MS_IndexView.aspx', 'SCS Trade index view')}")
    return [Alert(text, 10 if abs(pct) >= 3 else 9, f"kse:{hit}", tags=["KSE100", "MarketAlert"])], view


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
    state.set_snap("kse_last_close", close)
    return idx


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
        if not d or d["pct"] is None or abs(d["pct"]) < th:
            continue
        k = f"g:{sym}:{d['date']}"
        if state.flag(k):
            continue
        state.set_flag(k)
        val = f"{unit}{d['last']:,.2f}" if unit == "$" else f"{d['last']:,.2f}{unit if unit == '%' else ''}"
        alerts.append(Alert(
            header("🌐", f"{name} {fmt_pct(d['pct'], 1)}", "Global market move") + "\n"
            f"{arrow(d['pct'])} {esc(name)}: <b>{val}</b> ({fmt_pct(d['pct'], 1)} today)\n\n"
            f"📌 <b>Why it matters:</b> {GLOBAL_WHY.get(sym, '')}", 8, k, tags=["GlobalMarkets"]))
    if alerts:
        log.info("global alerts: %d", len(alerts))
    return alerts
