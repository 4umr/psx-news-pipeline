"""One pipeline pass: collect -> detect changes -> score news -> send."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import briefs, street, watchers
from .common import Alert, NewsItem, esc, hours_ago, in_window, link, log, now_pkt, to_pkt
from .scoring import Scorer, is_duplicate, title_tokens
from .sources import markets, news, sbp, scs
from .state import State
from .style import DIV, footer, header, meta, to_whatsapp
from .telegram import Sender


def _news_alerts(items: list[NewsItem], state: State, cfg: dict) -> tuple[list[NewsItem], list[NewsItem]]:
    scorer = Scorer(cfg)
    picked: list[NewsItem] = []
    for it in items:
        if not it.title or state.seen(it.uid):
            continue
        state.mark(it.uid)
        if it.published and hours_ago(it.published) > cfg["max_item_age_hours"]:
            continue
        scorer.score(it)
        if it.score < cfg["min_score_digest"]:
            continue
        toks = title_tokens(it.title)
        if is_duplicate(toks, state.titles, it.topic):
            continue
        state.add_title(toks, it.topic)
        state.add_headline({"t": it.title, "u": it.url, "s": it.source, "sc": it.score,
                            "l": it.label, "ts": int(time.time())})
        picked.append(it)
    picked.sort(key=lambda i: -i.score)
    instant = [i for i in picked if i.score >= cfg["min_score_instant"]][: cfg["max_instant_per_run"]]
    global_topics = {t["name"] for t in cfg["topics"] if not t.get("pk")}
    digest, n_global = [], 0
    for i in picked:
        if i in instant:
            continue
        if i.topic in global_topics:
            if n_global >= cfg.get("max_global_in_digest", 4):
                continue
            n_global += 1
        digest.append(i)
    if cfg.get("news_mode", "instant") == "instant":
        return instant, digest[:60]  # overflow is queued and posted over the next runs
    return instant, digest[: cfg["max_digest_items"]]


# ---------------------------------------------------------------- formatting
def _fmt_instant(it: NewsItem, cfg: dict) -> str:
    when = to_pkt(it.published) or now_pkt()
    kicker = "BREAKING" if it.score >= 10 else "IMPORTANT"
    icon = "🚨" if it.score >= 10 else "🔴"
    m = meta(cfg, it.topic)
    lines = [header(icon, f"{kicker} · {it.label.split(' ', 1)[-1]}", f"{when:%a %d %b · %H:%M} PKT"),
             f"<b>{esc(it.title)}</b>", ""]
    if it.why:
        lines.append(f"📌 <b>Why it matters:</b> {esc(it.why)}")
    if m.get("sectors"):
        lines.append(f"🏭 <b>Sectors in focus:</b> {esc(' · '.join(m['sectors']))}")
    lines.append(f"🔗 Source: {link(it.url, it.source)}")
    return "\n".join(lines) + footer(cfg, m.get("tags", []), compact=True)


def _fmt_digest(items: list[dict], cfg: dict) -> str:
    # group by topic label, groups ordered by their best score
    groups: dict[str, list[dict]] = {}
    per_topic = cfg.get("max_per_topic_in_digest", 3)
    for it in sorted(items, key=lambda i: -i["sc"]):
        g = groups.setdefault(it["l"], [])
        if len(g) < per_topic:
            g.append(it)
    items = [i for g in groups.values() for i in g]
    blocks, tags = [], []
    for label, its in groups.items():
        rows = []
        for it in its:
            dot = "🟠" if it["sc"] >= 6 else "🟡"
            rows.append(f"{dot} {esc(it['t'])} — {link(it['u'], it['s'])}")
        blocks.append(f"<b>{esc(label)}</b>\n" + "\n".join(rows))
        tags += meta(cfg, its[0].get("topic", "")).get("tags", [])[:1]
    return (header("📰", "PSX News Wrap", f"{now_pkt():%a %d %b · %H:%M} PKT · {len(items)} stories") + "\n"
            + "\n\n".join(blocks) + footer(cfg, tags[:5], compact=True))


def _fmt_compact(d: dict, cfg: dict) -> str:
    """One headline, posted the moment it's found (instant news mode)."""
    dot = "🟠" if d["sc"] >= 6 else "🟡"
    when = datetime.fromtimestamp(d.get("pub") or d["ts"], tz=now_pkt().tzinfo)
    tags = meta(cfg, d.get("topic", "")).get("tags", [])[:2]
    b = cfg.get("brand", {})
    tag_line = " ".join(f"#{t}" for t in tags + ["PSX"])
    return (f"{dot} <i>{esc(d['l'])}</i>\n<b>{esc(d['t'])}</b>\n"
            f"🔗 {link(d['u'], d['s'])} · {when:%H:%M} PKT\n"
            f"{tag_line}\n🇵🇰 <b>{esc(b.get('name', ''))}</b> · <i>info only, not advice</i>")


def _with_footer(a: Alert, cfg: dict) -> str:
    return a.text + footer(cfg, a.tags, compact=True)


# ---------------------------------------------------------------- digest batching
def _digest_due(cfg: dict, state: State, now: datetime) -> bool:
    pending = state.data.setdefault("pending", [])
    if not pending or _in_quiet_hours(cfg, now):
        return False
    last = state.data.get("last_digest", 0)
    return time.time() - last >= cfg.get("digest_every_minutes", 30) * 60


def _queue_digest(items: list[NewsItem], state: State) -> None:
    pending = state.data.setdefault("pending", [])
    for it in items:
        pending.append({"t": it.title, "u": it.url, "s": it.source, "sc": it.score,
                        "l": it.label, "topic": it.topic, "ts": int(time.time()),
                        "pub": int(it.published.timestamp()) if it.published else None})


def _in_quiet_hours(cfg: dict, now: datetime) -> bool:
    q = cfg.get("digest_quiet_hours")
    if not q:
        return False
    start, end = q
    if start > end:  # crosses midnight
        return in_window(now, start, "23:59") or in_window(now, "00:00", end)
    return in_window(now, start, end)


def _flush_instant(cfg: dict, state: State, sender: Sender, now: datetime) -> int:
    """Instant mode: post queued headlines one by one, best first, within the per-run budget."""
    if _in_quiet_hours(cfg, now):
        return 0
    pending = [p for p in state.data.get("pending", []) if time.time() - p["ts"] < 12 * 3600]
    pending.sort(key=lambda p: (-p["sc"], p["ts"]))
    budget = cfg.get("max_news_posts_per_run", 15)
    send, keep = pending[:budget], pending[budget:]
    for d in send:
        sender.send(_fmt_compact(d, cfg))
    state.data["pending"] = keep
    return len(send)


def _flush_digest(cfg: dict, state: State, sender: Sender) -> int:
    pending = state.data.get("pending", [])
    # drop anything that waited more than 12h (e.g. long outage) and keep the best
    fresh = [p for p in pending if time.time() - p["ts"] < 12 * 3600]
    fresh.sort(key=lambda p: -p["sc"])
    batch = fresh[: cfg.get("max_digest_items", 15) + 5]
    state.data["pending"] = []
    state.data["last_digest"] = int(time.time())
    if batch:
        sender.send(_fmt_digest(batch, cfg))
    return len(batch)


# ---------------------------------------------------------------- briefs
def _send_brief(name: str, cfg: dict, state: State, mk: dict, view, sender: Sender, **kw) -> str:
    text, card, caption = briefs.BUILDERS[name](cfg, state, mk, view, **kw)
    if card:
        sender.send_photo(card, caption, name=name)
    sender.send(text)
    return text


def about_text(cfg: dict) -> str:
    b = cfg.get("brand", {})
    sched = cfg.get("briefs", {})
    t = lambda n, d: sched.get(n, {}).get("time", d)  # noqa: E731
    return (header("🇵🇰", f"Welcome to {b.get('name', 'PSX')}",
                   f"Curated by {b.get('author', '')}" + (f", {b['title']}" if b.get("title") else "")) + "\n"
            "Pakistan Stock Exchange news, data and macro indicators — automatic, 24/7, always with sources.\n\n"
            "<b>⏰ What you get & when (PKT)</b>\n"
            f"☀️ <b>{t('morning', '08:45')}</b> Morning brief + market card (Mon–Fri)\n"
            f"🔔 <b>{t('close', '17:15')}</b> Closing wrap + market card (Mon–Fri)\n"
            "🌍 <b>Evening</b> Foreign / local investor flows (FIPI/LIPI)\n"
            f"📅 <b>Sun {t('week_ahead', '19:00')}</b> Week ahead: results calendar, payouts, auctions\n"
            "📰 <b>Every 30 min</b> News wrap when there's news (paused 23:30–07:30)\n\n"
            "<b>🚨 Instant alerts, any time</b>\n"
            "• SBP policy rate decisions & MPC reminders\n"
            "• T-bill / PIB auction cut-offs (with change in bps)\n"
            "• SBP reserves (weekly), CPI & SPI inflation (actual figures)\n"
            "• IMF, budget/tax, fuel & power prices, credit ratings\n"
            "• KSE-100 moves of ±1.5% / 3% / 5% and big single-stock moves\n"
            "• Company results, dividends & bonus shares — with year-on-year EPS growth\n"
            "• 📢 PSX company filings & material information, straight from PSX\n"
            "• 🔎 Unusual volume in KSE-100 stocks · ⚡ big single-stock moves\n"
            "• 🧠 Street View: brokerage forecasts, scored against actual results\n"
            "• Oil, gold, dollar & global market shocks · security escalations\n\n"
            "<b>📚 Sources</b>: SBP · PBS · PSX (via SCS Trade) · NCCPL · forex.pk · Business Recorder · "
            "Dawn · Express Tribune · The News · ProPakistani · Reuters/Bloomberg & others via Google News\n\n"
            "Search past posts with hashtags like #SBP #KSE100 #CPI #Results"
            + footer(cfg, ["PakistanInvestors"]))


HEALTH_CHECKS = ("SBP website", "PSX data (SCS Trade)", "News feeds", "Global markets")


def _health(cfg: dict, state: State, sender: Sender, ok: dict[str, bool]) -> None:
    """Warn the owner privately when a source fails 3 runs in a row, and when it recovers."""
    h = state.data.setdefault("health", {})
    for name, good in ok.items():
        n = h.get(name, 0)
        if good:
            if n >= 3:
                sender.send_admin(f"✅ <b>Recovered:</b> {esc(name)} is working again.")
            h[name] = 0
        else:
            h[name] = n + 1
            if h[name] == 3:
                sender.send_admin(f"⚠️ <b>Source problem:</b> {esc(name)} has failed 3 runs in a row. "
                                  "Messages that depend on it are paused; everything else continues. "
                                  "If this lasts more than a day, the website may have changed.")


def run_once(cfg: dict, dry_run: bool = False, force_brief: str | None = None) -> None:
    t0 = time.time()
    state = State()
    sender = Sender(dry_run)
    bootstrap = state.is_new
    now = now_pkt()
    if bootstrap:
        log.info("First run: recording current state silently (no flood of old news)")

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_news = ex.submit(news.collect, cfg)
        f_sbp = ex.submit(sbp.fetch)
        f_res = ex.submit(scs.results)
        f_mk = ex.submit(markets.snapshot, list(cfg["global_markets"]))
        f_view = ex.submit(scs.kse100_view)
        sbp_snap, sbp_items = f_sbp.result()
        mk = f_mk.result()
        kse_symbols = {r.get("company_code") for r in f_view.result()}
        news_items = f_news.result()

    _health(cfg, state, sender, {
        "SBP website": bool(sbp_snap.get("policy_rate")),
        "PSX data (SCS Trade)": bool(kse_symbols),
        "News feeds": len(news_items) >= 100,
        "Global markets": len(mk) >= 5,
    })

    street.capture(sbp_items + news_items, state)   # brokerage forecasts for the accuracy scoreboard

    alerts: list[Alert] = []
    alerts += watchers.pbs_releases(state)     # before news: marks the PBS posts as handled
    alerts += watchers.mpc_watch(now, state)
    watchers.mpc_hold_check(now, state)
    view_now = f_view.result()
    alerts += watchers.psx_filings(now, state, cfg, view_now)
    alerts += watchers.unusual_volume(now, state, cfg, kse_symbols)
    alerts += watchers.sbp_changes(sbp_snap, state)
    alerts += watchers.corporate_results(f_res.result(), state, kse_symbols)
    alerts += watchers.fipi_alert(now, state)
    kse_alerts, view = watchers.kse_moves(now, state, cfg)
    alerts += kse_alerts
    alerts += watchers.global_moves(mk, state, cfg)
    watchers.record_close(now, state)

    # official items first so their links win over re-reports
    instant, digest = _news_alerts(sbp_items + news_items, state, cfg)

    if bootstrap:
        for name in briefs.due(cfg, state, now, grace_hours=24):
            state.data["briefs"][name] = f"{now:%Y-%m-%d}"
        ok = sender.send(about_text(cfg))
        if not ok:
            # Telegram not reachable (wrong chat id / bot not admin): don't record
            # the first run, so the welcome + snapshot are retried next time.
            log.error("Telegram delivery failed — first run will be retried next time")
            return
        _send_brief("morning", cfg, state, mk, view, sender, title="Market Snapshot")
        state.data["last_digest"] = int(time.time())
        state.save()
        log.info("bootstrap done in %.1fs", time.time() - t0)
        return

    sender.retry_outbox(state.data.pop("outbox", []))

    wa_mode = cfg.get("brand", {}).get("whatsapp_copy", "important")

    def wa(text: str, important: bool) -> None:
        if sender.admin and (wa_mode == "all" or (wa_mode == "important" and important)):
            sender.send_admin_plain("📲 WhatsApp-ready copy (long-press → copy → paste):\n\n" + to_whatsapp(text, cfg))

    alerts.sort(key=lambda a: -a.priority)
    for a in alerts:
        if a.admin:
            sender.send_admin(a.text)
            continue
        msg = _with_footer(a, cfg)
        sender.send(msg, preview=a.preview)
        wa(msg, a.priority >= 9)
    for it in instant:
        msg = _fmt_instant(it, cfg)
        sender.send(msg)
        wa(msg, it.score >= 10)
    _queue_digest(digest, state)
    if cfg.get("news_mode", "instant") == "instant":
        n_digest = _flush_instant(cfg, state, sender, now)
    else:
        n_digest = _flush_digest(cfg, state, sender) if _digest_due(cfg, state, now) else 0

    names = [force_brief] if force_brief else briefs.due(cfg, state, now)
    for name in names:
        text = _send_brief(name, cfg, state, mk, view, sender)
        if text:
            wa(text, True)
        state.data["briefs"][name] = f"{now:%Y-%m-%d}"

    if sender.failed:
        state.data["outbox"] = sender.failed[-20:]
    state.save()
    log.info("run done in %.1fs: %d alerts, %d instant news, %d queued, %d in digest, briefs=%s, messages=%d",
             time.time() - t0, len(alerts), len(instant), len(digest), n_digest, names, sender.sent)
