"""One pipeline pass: collect -> detect changes -> score news -> send."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import briefs, watchers
from .common import Alert, NewsItem, esc, hours_ago, in_window, link, log, now_pkt, to_pkt
from .scoring import Scorer, is_duplicate, title_tokens
from .sources import markets, news, sbp, scs
from .state import State
from .style import DIV, footer, header, meta
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


def _with_footer(a: Alert, cfg: dict) -> str:
    return a.text + footer(cfg, a.tags, compact=True)


# ---------------------------------------------------------------- digest batching
def _digest_due(cfg: dict, state: State, now: datetime) -> bool:
    pending = state.data.setdefault("pending", [])
    if not pending:
        return False
    q = cfg.get("digest_quiet_hours")
    if q:
        start, end = q
        if start > end:  # window crosses midnight, e.g. 23:30 -> 07:30
            quiet = in_window(now, start, "23:59") or in_window(now, "00:00", end)
        else:
            quiet = in_window(now, start, end)
        if quiet:
            return False
    last = state.data.get("last_digest", 0)
    return time.time() - last >= cfg.get("digest_every_minutes", 30) * 60


def _queue_digest(items: list[NewsItem], state: State) -> None:
    pending = state.data.setdefault("pending", [])
    for it in items:
        pending.append({"t": it.title, "u": it.url, "s": it.source, "sc": it.score,
                        "l": it.label, "topic": it.topic, "ts": int(time.time())})


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
def _send_brief(name: str, cfg: dict, state: State, mk: dict, view, sender: Sender, **kw) -> None:
    text, card, caption = briefs.BUILDERS[name](cfg, state, mk, view, **kw)
    if card:
        sender.send_photo(card, caption, name=name)
    sender.send(text)


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
        b = cfg.get("brand", {})
        ok = sender.send(
            header("✅", f"{b.get('name', 'PSX')} news service is live") + "\n"
            "Your PSX market intelligence feed — automatic, 24/7, with sources.\n\n"
            "🚨 <b>Instant alerts</b> — SBP policy rate, T-bill/PIB auctions, reserves, CPI/SPI, IMF, "
            "fuel prices, budget/tax, ratings, geopolitics, KSE-100 big moves, oil/gold/dollar shocks\n"
            "📊 <b>Corporate</b> — results, dividends, bonus shares with PSX filings\n"
            "🌍 <b>Investor flows</b> — daily foreign/local (FIPI/LIPI)\n"
            "📰 <b>News wrap</b> — every 30 min when there's news\n"
            "☀️ <b>Morning brief</b> 08:45 · 🔔 <b>Closing wrap</b> 17:15 · 📅 <b>Week ahead</b> Sun 19:00\n\n"
            "Current market snapshot below 👇" + footer(cfg, ["PSX", "PakistanInvestors"]))
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

    alerts.sort(key=lambda a: -a.priority)
    for a in alerts:
        sender.send(_with_footer(a, cfg), preview=a.preview)
    for it in instant:
        sender.send(_fmt_instant(it, cfg))
    _queue_digest(digest, state)
    n_digest = _flush_digest(cfg, state, sender) if _digest_due(cfg, state, now) else 0

    names = [force_brief] if force_brief else briefs.due(cfg, state, now)
    for name in names:
        _send_brief(name, cfg, state, mk, view, sender)
        state.data["briefs"][name] = f"{now:%Y-%m-%d}"

    state.save()
    log.info("run done in %.1fs: %d alerts, %d instant news, %d queued, %d in digest, briefs=%s, messages=%d",
             time.time() - t0, len(alerts), len(instant), len(digest), n_digest, names, sender.sent)
