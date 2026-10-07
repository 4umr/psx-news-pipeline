"""One pipeline pass: collect -> detect changes -> score news -> send."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from . import briefs, watchers
from .common import Alert, NewsItem, esc, hours_ago, link, log, now_pkt, to_pkt
from .scoring import Scorer, is_duplicate, title_tokens
from .sources import markets, news, sbp, scs
from .state import State
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
    return instant, digest[: cfg["max_digest_items"]]


def _fmt_instant(it: NewsItem, cfg: dict) -> str:
    icon = "🚨" if it.score >= 10 else "🔴"
    when = to_pkt(it.published) or now_pkt()
    why = f"\n💡 {esc(it.why)}" if it.why else ""
    disc = f"\n<i>{esc(cfg['disclaimer'])}</i>" if cfg.get("disclaimer_on_alerts") and cfg.get("disclaimer") else ""
    return (f"{icon} <b>{esc(it.label)}</b>\n<b>{esc(it.title)}</b>{why}\n"
            f"🔗 {link(it.url, it.source)} · {when:%H:%M} PKT{disc}")


def _fmt_digest(items: list[NewsItem], cfg: dict) -> str:
    lines = []
    for it in items:
        dot = "🟠" if it.score >= 6 else "🟡"
        lines.append(f"{dot} <i>{esc(it.label)}</i>\n<b>{esc(it.title)}</b> — {link(it.url, it.source)}")
    disc = f"\n\n<i>{esc(cfg['disclaimer'])}</i>" if cfg.get("disclaimer_on_alerts") and cfg.get("disclaimer") else ""
    return f"📰 <b>PSX News Update</b> · {now_pkt():%H:%M} PKT\n\n" + "\n\n".join(lines) + disc


def _with_disclaimer(text: str, cfg: dict) -> str:
    if cfg.get("disclaimer_on_alerts") and cfg.get("disclaimer"):
        return f"{text}\n<i>{esc(cfg['disclaimer'])}</i>"
    return text


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

    alerts: list[Alert] = []
    alerts += watchers.sbp_changes(sbp_snap, state)
    alerts += watchers.corporate_results(f_res.result(), state, kse_symbols)
    alerts += watchers.fipi_alert(now, state)
    kse_alerts, view = watchers.kse_moves(now, state, cfg)
    alerts += kse_alerts
    alerts += watchers.global_moves(mk, state, cfg)
    watchers.record_close(now, state)

    # official items first so their links win over re-reports
    instant, digest = _news_alerts(sbp_items + f_news.result(), state, cfg)

    if bootstrap:
        for name in briefs.due(cfg, state, now, grace_hours=24):
            state.data["briefs"][name] = f"{now:%Y-%m-%d}"
        ok = sender.send("✅ <b>PSX News Pipeline is live</b>\n\n"
                    "You will receive:\n"
                    "🚨 Instant alerts — SBP policy rate, T-bill/PIB auction results, reserves, CPI/SPI, "
                    "IMF, fuel prices, budget/tax, geopolitics, KSE-100 big moves, oil/gold/dollar shocks\n"
                    "📊 Corporate results & payouts (EPS, dividends, bonus) with PSX filing PDFs\n"
                    "🌍 Daily foreign/local investor flows (FIPI/LIPI)\n"
                    "📰 News updates from Business Recorder, Dawn, Tribune, The News, ProPakistani, "
                    "Google News (Reuters, Bloomberg, Mettis, Profit…)\n"
                    "☀️ Morning brief 08:45 · 🔔 Closing wrap 17:15 · 📅 Week ahead Sunday 19:00\n\n"
                    "Here is the current snapshot 👇")
        if not ok:
            # Telegram not reachable (wrong chat id / bot not admin): don't record
            # the first run, so the welcome + snapshot are retried next time.
            log.error("Telegram delivery failed — first run will be retried next time")
            return
        sender.send(briefs.morning(cfg, state, mk, title="📸 <b>MARKET SNAPSHOT</b>"))
        state.save()
        log.info("bootstrap done in %.1fs", time.time() - t0)
        return

    alerts.sort(key=lambda a: -a.priority)
    for a in alerts:
        sender.send(_with_disclaimer(a.text, cfg), preview=a.preview)
    for it in instant:
        sender.send(_fmt_instant(it, cfg))
    if digest:
        sender.send(_fmt_digest(digest, cfg))

    names = [force_brief] if force_brief else briefs.due(cfg, state, now)
    for name in names:
        builder = briefs.BUILDERS[name]
        text = builder(cfg, state, mk, view) if name == "close" else builder(cfg, state, mk)
        sender.send(text)
        state.data["briefs"][name] = f"{now:%Y-%m-%d}"

    state.save()
    log.info("run done in %.1fs: %d alerts, %d instant news, %d digest, briefs=%s, messages=%d",
             time.time() - t0, len(alerts), len(instant), len(digest), names, sender.sent)
