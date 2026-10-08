"""One pipeline pass: collect -> detect changes -> score news -> send."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import briefs, street, watchers
from .common import Alert, NewsItem, esc, hours_ago, in_window, link, log, now_pkt, to_pkt
from .links import short
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
                            "l": it.label, "topic": it.topic, "ts": int(time.time())})
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
def _split_label(label: str) -> tuple[str, str]:
    """'⛽ Fuel / Energy Prices' -> ('⛽', 'Fuel / Energy Prices')"""
    parts = label.split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 and not parts[0].isalnum() else ("📰", label)


def _fmt_instant(it: NewsItem, cfg: dict) -> str:
    when = to_pkt(it.published) or now_pkt()
    icon, topic = _split_label(it.label)
    tag = "🚨 <b>Breaking</b>" if it.score >= 10 else "🔴 <b>Important</b>"
    m = meta(cfg, it.topic)
    lines = [f"{tag} · {icon} {esc(topic)}", "", f"<b>{esc(it.title)}</b>", ""]
    if it.why:
        lines.append(f"💡 {esc(it.why)}")
    if m.get("sectors"):
        lines.append(f"🏭 In focus: {esc(' · '.join(m['sectors']))}")
    lines.append(f"🔗 {when:%H:%M} PKT · {link(it.url, it.source)}")
    return "\n".join(lines) + footer(cfg, compact=True)


def _fmt_digest(items: list[dict], cfg: dict) -> str:
    # group by topic label, groups ordered by their best score
    groups: dict[str, list[dict]] = {}
    per_topic = cfg.get("max_per_topic_in_digest", 3)
    for it in sorted(items, key=lambda i: -i["sc"]):
        g = groups.setdefault(it["l"], [])
        if len(g) < per_topic:
            g.append(it)
    items = [i for g in groups.values() for i in g]
    blocks = []
    for label, its in groups.items():
        rows = [f"• {esc(it['t'])} — {link(it['u'], it['s'])}" for it in its]
        blocks.append(f"<b>{esc(label)}</b>\n" + "\n".join(rows))
    return (header("📰", "News wrap", f"{now_pkt():%a %d %b · %H:%M} PKT · {len(items)} stories") + "\n"
            + "\n\n".join(blocks) + footer(cfg, compact=True))


def _fmt_compact(d: dict, cfg: dict) -> str:
    """One headline, posted the moment it's found (instant news mode)."""
    when = datetime.fromtimestamp(d.get("pub") or d["ts"], tz=now_pkt().tzinfo)
    icon, topic = _split_label(d["l"])
    return (f"{icon} <b>{esc(d['t'])}</b>\n"
            f"<i>{esc(topic)} · {when:%H:%M}</i> · {link(d['u'], d['s'])}")


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
def _infographic(cfg: dict) -> bool:
    return cfg.get("brand", {}).get("infographic", True)


def _send_brief(name: str, cfg: dict, state: State, mk: dict, view, sender: Sender, **kw) -> str:
    text, cards, caption = briefs.BUILDERS[name](cfg, state, mk, view, **kw)
    if not text:
        return ""  # e.g. midday brief on a market holiday
    if isinstance(cards, bytes):
        cards = [cards]
    if cards:
        sender.send_album(cards[:10], caption, name=name)
        if _infographic(cfg):
            return text  # infographic mode: the album IS the brief (text kept only for the WhatsApp copy)
    sender.send(text)  # no cards (e.g. holiday notice) or text mode
    return text


# Text mode only: long list-style alerts read better as text than squeezed into an image
NO_CARD = {"results", "psxfilings", "stocks", "volume"}

# News topic -> card colour theme (brand category colours)
TOPIC_THEME = {"monetary_policy": "alert", "market_shock": "alert", "geo_escalation": "alert", "rupee_shock": "alert",
               "global_shock": "alert", "middle_east": "alert", "geopolitics": "alert",
               "govt_securities": "fixed", "fuel_prices": "commodity", "energy_sector": "commodity",
               "global_oil": "commodity", "fed_decision": "us", "global_rates": "us",
               "psx_corporate": "psx", "psx_market": "psx", "street_view": "psx", "sector_data": "psx"}


def _market_tiles(state: State, view: list[dict]) -> list:
    """KSE-100 / policy rate / USD-PKR right now, for the strip on alert cards."""
    tiles = []
    if view and view[0].get("CurrentIndex") and view[0].get("PreIndex"):
        cur, pre = view[0]["CurrentIndex"], view[0]["PreIndex"]
        tiles.append(("KSE-100", f"{cur:,.0f}", (cur / pre - 1) * 100))
    s = state.snap("sbp") or {}
    if s.get("policy_rate") is not None:
        tiles.append(("SBP policy rate", f"{s['policy_rate']:.2f}%", None))
    if u := s.get("usdpkr"):
        tiles.append(("USD/PKR", f"{u['m2m']:.2f}", None))
    return tiles


def _caption(cfg: dict, head: str, links: list[str]) -> str:
    """Short caption under an infographic: the title + visible source links (links can't live in an image)."""
    for k in range(len(links), -1, -1):
        cap = head + ("\n" + "\n".join(links[:k]) if k else "") + footer(cfg, compact=True)
        if Sender.fits_caption(cap):
            return cap
    return head


def _post_with_card(sender: Sender, cfg: dict, msg: str, spec: dict, when: datetime, name: str,
                    tiles: list | None = None, caption: str | None = None) -> None:
    """Infographic card + short caption (infographic mode) or + the full message (text mode)."""
    if not cfg.get("brand", {}).get("alert_cards", True):
        sender.send(msg)
        return
    from .cards import alert_card
    try:
        png = alert_card(cfg, spec, when, tiles=tiles)
    except Exception:  # noqa: BLE001 — never lose the message because an image failed
        log.exception("alert card failed")
        sender.send(msg)
        return
    if _infographic(cfg) and caption:
        sender.send_photo(png, caption, name=name)
    elif sender.fits_caption(msg):
        sender.send_photo(png, msg, name=name)
    else:
        sender.send_photo(png, "", name=name)
        sender.send(msg)


def _card_spec_from_alert(a: Alert) -> dict:
    """Fallback card for an alert without a structured spec: title, body lines and source from its text."""
    lines = [ln for ln in a.text.split("\n")]
    title = lines[0] if lines else ""
    sub = lines[1] if len(lines) > 1 and lines[1].startswith("<i>") else ""
    rest = lines[2:] if sub else lines[1:]
    source = next((ln for ln in rest if ln.startswith("🔗")), "")
    body = [ln for ln in rest if ln.strip() and not ln.startswith(("🔗", "💡"))]
    why = next((ln for ln in rest if ln.startswith("💡")), "")
    kicker = "Breaking" if a.priority >= 10 else "Market update"
    return {"theme": "alert" if a.priority >= 10 else "economy", "kicker": kicker + (f" · {sub}" if sub else ""),
            "title": title, "rows_title": "Details", "rows": [("", ln, "", None) for ln in body[:12]],
            "why": why, "source": re.sub(r"https?://\S+", "", source).strip(" ·🔗")}


def _alert_caption(cfg: dict, a: Alert, spec: dict) -> str:
    head = a.text.split("\n", 1)[0]
    links = [ln for ln in a.text.split("\n") if ln.startswith("🔗")]
    links += [f"📄 {esc(sym)} · {esc(short(u))}" for sym, u in spec.get("links", [])]
    return _caption(cfg, head, links)


def about_text(cfg: dict) -> str:
    b = cfg.get("brand", {})
    sched = cfg.get("briefs", {})
    t = lambda n, d: sched.get(n, {}).get("time", d)  # noqa: E731
    return (header("🇵🇰", f"Welcome to {b.get('name', 'PSX')}",
                   f"Curated by {b.get('author', '')}" + (f", {b['title']}" if b.get("title") else "")) + "\n"
            "Pakistan Stock Exchange news, data and macro indicators — automatic, 24/7, always with sources. "
            "Updates come as shareable infographics.\n\n"
            "<b>⏰ What you get & when (PKT)</b>\n"
            f"☀️ <b>{t('morning', '08:45')}</b> Market open: PSX, US market close, commodity update, top stories (Mon–Fri)\n"
            f"🕐 <b>{t('midday', '12:30')}</b> Midday pulse: intraday chart, movers, sectors (Mon–Fri)\n"
            f"🔔 <b>{t('close', '17:15')}</b> Market close: gainers/losers, sectors, highlights (Mon–Fri)\n"
            "🌍 <b>Evening</b> Foreign / local investor flows (FIPI/LIPI)\n"
            f"📅 <b>Sun {t('week_ahead', '19:00')}</b> Week in review & the week ahead\n"
            "📰 <b>As it happens</b> Relevant headlines, posted the moment they're found\n\n"
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
            "Dawn · Express Tribune · The News · ProPakistani · Reuters/Bloomberg & others via Google News"
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


def _safe(state: State, errors: list, name: str, fn, *args, default=None):
    """Run one component; if it crashes, log it, remember it, and let the rest of the run continue."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        log.exception("component failed: %s", name)
        errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        return default


def _component_health(state: State, sender: Sender, errors: list[str]) -> None:
    """Private warning when the same component crashes 3 runs in a row (code/site changed)."""
    h = state.data.setdefault("comp_errors", {})
    failed = {e.split(":", 1)[0]: e for e in errors}
    for name in list(h):
        if name not in failed:
            if h[name] >= 3:
                sender.send_admin(f"✅ <b>Recovered:</b> {esc(name)} is working again.")
            del h[name]
    for name, err in failed.items():
        h[name] = h.get(name, 0) + 1
        if h[name] == 3:
            sender.send_admin(f"⚠️ <b>Component error</b> (3 runs in a row): {esc(err)}\n"
                              "Everything else keeps running. This usually means a website changed its layout.")


def _stats(state: State, now: datetime, sender: Sender, errors: list[str]) -> None:
    st = state.data.setdefault("stats", {})
    day = st.setdefault(f"{now:%Y-%m-%d}", {"runs": 0, "msgs": 0, "errors": 0})
    day["runs"] += 1
    day["msgs"] += sender.sent
    day["errors"] += len(errors)
    for d in sorted(st)[:-10]:
        del st[d]


def _daily_report(cfg: dict, state: State, sender: Sender, now: datetime) -> None:
    """23:45 PKT private 'system is alive' report with today's numbers and source health."""
    if not sender.admin or (now.hour, now.minute) < (23, 45) or state.flag(f"report:{now:%Y-%m-%d}"):
        return
    state.set_flag(f"report:{now:%Y-%m-%d}")
    day = state.data.get("stats", {}).get(f"{now:%Y-%m-%d}", {})
    bad = [k for k, v in state.data.get("health", {}).items() if v >= 3]
    comp = list(state.data.get("comp_errors", {}))
    outbox = len(state.data.get("outbox", []))
    sbp_s = state.snap("sbp") or {}
    lines = [f"🩺 <b>Daily system report</b> · {now:%a %d %b}",
             f"Runs today: <b>{day.get('runs', 0)}</b> · Messages sent: <b>{day.get('msgs', 0)}</b> · "
             f"Component errors: {day.get('errors', 0)}",
             f"Sources: {'✅ all healthy' if not bad else '⚠️ down: ' + esc(', '.join(bad))}",
             f"Components: {'✅ OK' if not comp else '⚠️ failing: ' + esc(', '.join(comp))}",
             f"Undelivered (retrying): {outbox}",
             f"Last SBP data: policy {sbp_s.get('policy_rate', 'n/a')}% · reserves as on "
             f"{(sbp_s.get('reserves') or {}).get('as_on', 'n/a')}",
             f"Street forecasts tracked: {len(state.data.get('street', {}).get('forecasts', []))}"]
    if day.get("runs", 0) < 30:
        lines.append("ℹ️ Fewer runs than expected — GitHub's free scheduler may be slow today; "
                     "the 2-minute cron-job.org trigger fixes this.")
    sender.send_admin("\n".join(lines))


def run_once(cfg: dict, dry_run: bool = False, force_brief: str | None = None) -> None:
    t0 = time.time()
    state = State()
    sender = Sender(dry_run)
    now = now_pkt()
    errors: list[str] = []
    try:
        _run(cfg, state, sender, now, errors, force_brief, t0)
    except Exception as e:  # noqa: BLE001 — last line of defence: never lose progress
        log.exception("run crashed")
        errors.append(f"run: {type(e).__name__}: {str(e)[:120]}")
    finally:
        if not state.data.pop("_bootstrap_failed", False):
            _safe(state, errors, "health", _component_health, state, sender, errors)
            _stats(state, now, sender, errors)
            _safe(state, errors, "daily report", _daily_report, cfg, state, sender, now)
            if sender.failed:
                state.data["outbox"] = (state.data.get("outbox", []) + sender.failed)[-20:]
            state.save()
        log.info("run finished in %.1fs, messages=%d, component errors=%d",
                 time.time() - t0, sender.sent, len(errors))


def _run(cfg: dict, state: State, sender: Sender, now: datetime, errors: list, force_brief, t0: float) -> None:
    bootstrap = state.is_new
    if bootstrap:
        log.info("First run: recording current state silently (no flood of old news)")

    def S(name, fn, *a, default=None):
        return _safe(state, errors, name, fn, *a, default=default)

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_news = ex.submit(S, "news feeds", news.collect, cfg, default=[])
        f_sbp = ex.submit(S, "SBP", sbp.fetch, default=({}, []))
        f_res = ex.submit(S, "SCS results", scs.results, default=[])
        f_mk = ex.submit(S, "global markets", markets.snapshot, list(cfg["global_markets"]), default={})
        f_view = ex.submit(S, "KSE-100 view", scs.kse100_view, default=[])
        sbp_snap, sbp_items = f_sbp.result()
        mk = f_mk.result()
        view_now = f_view.result()
        kse_symbols = {r.get("company_code") for r in view_now}
        news_items = f_news.result()

    _health(cfg, state, sender, {
        "SBP website": bool(sbp_snap.get("policy_rate")),
        "PSX data (SCS Trade)": bool(kse_symbols),
        "News feeds": len(news_items) >= 100,
        "Global markets": len(mk) >= 5,
        "Telegram channel delivery": not state.data.get("channel_fail", False),
    })

    S("street capture", street.capture, sbp_items + news_items, state)

    alerts: list[Alert] = []
    alerts += S("PBS releases", watchers.pbs_releases, state, default=[])  # before news: marks PBS posts handled
    alerts += S("MPC calendar", watchers.mpc_watch, now, state, default=[])
    S("MPC hold check", watchers.mpc_hold_check, now, state)
    alerts += S("PSX filings", watchers.psx_filings, now, state, cfg, view_now, default=[])
    alerts += S("unusual volume", watchers.unusual_volume, now, state, cfg, kse_symbols, default=[])
    alerts += S("SBP changes", watchers.sbp_changes, sbp_snap, state, default=[])
    alerts += S("corporate results", watchers.corporate_results, f_res.result(), state, kse_symbols, default=[])
    alerts += S("FIPI", watchers.fipi_alert, now, state, default=[])
    kse_alerts, view = S("KSE moves", watchers.kse_moves, now, state, cfg, default=([], []))
    alerts += kse_alerts
    view = view or view_now
    alerts += S("global moves", watchers.global_moves, mk, state, cfg, default=[])
    S("record close", watchers.record_close, now, state)

    # official items first so their links win over re-reports
    instant, digest = S("news scoring", _news_alerts, sbp_items + news_items, state, cfg, default=([], []))

    if bootstrap:
        for name in briefs.due(cfg, state, now, grace_hours=24):
            state.data["briefs"][name] = f"{now:%Y-%m-%d}"
        ok = sender.send(about_text(cfg))
        if not ok:
            # Telegram not reachable (wrong chat id / bot not admin): don't record
            # the first run, so the welcome + snapshot are retried next time.
            log.error("Telegram delivery failed — first run will be retried next time")
            state.data["_bootstrap_failed"] = True
            return
        S("snapshot brief", lambda: _send_brief("morning", cfg, state, mk, view, sender, title="Market Snapshot"))
        state.data["last_digest"] = int(time.time())
        log.info("bootstrap done in %.1fs", time.time() - t0)
        return

    S("outbox retry", sender.retry_outbox, state.data.pop("outbox", []))

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
        spec = a.extra.get("card")
        if _infographic(cfg):
            spec = spec or _card_spec_from_alert(a)
            tiles = _market_tiles(state, view_now) if not spec.get("rows") or len(spec["rows"]) <= 6 else None
            _post_with_card(sender, cfg, msg, spec, now, "alert", tiles, _alert_caption(cfg, a, spec))
        elif a.priority >= cfg.get("brand", {}).get("alert_card_min_priority", 9) and a.key not in NO_CARD:
            _post_with_card(sender, cfg, msg, spec or _card_spec_from_alert(a), now, "alert",
                            _market_tiles(state, view_now))
        else:
            sender.send(msg, preview=a.preview)
        S("whatsapp copy", wa, msg, a.priority >= 9)
    for it in instant:
        msg = _fmt_instant(it, cfg)
        icon, topic = _split_label(it.label)
        m = meta(cfg, it.topic)
        when = to_pkt(it.published) or now
        breaking = it.score >= 10
        spec = {"theme": "alert" if breaking else TOPIC_THEME.get(it.topic, "economy"),
                "kicker": f"{'Breaking' if breaking else 'Important'} · {topic}", "title": it.title,
                "why": it.why, "sectors": m.get("sectors") or [], "source": f"{it.source} · {when:%H:%M} PKT"}
        cap = _caption(cfg, f"{'🚨' if breaking else '🔴'} <b>{esc(it.title)}</b>", [f"🔗 {link(it.url, it.source)}"])
        _post_with_card(sender, cfg, msg, spec, when, "news", _market_tiles(state, view_now), cap)
        S("whatsapp copy", wa, msg, it.score >= 10)
    _queue_digest(digest, state)
    if cfg.get("news_mode", "instant") == "instant":
        S("news posts", _flush_instant, cfg, state, sender, now)
    elif _digest_due(cfg, state, now):
        S("news digest", _flush_digest, cfg, state, sender)

    names = [force_brief] if force_brief else briefs.due(cfg, state, now)
    for name in names:
        text = S(f"{name} brief", _send_brief, name, cfg, state, mk, view, sender)
        if text:
            S("whatsapp copy", wa, text, True)
            state.data["briefs"][name] = f"{now:%Y-%m-%d}"  # only mark sent if it actually built

    # remember whether the channel accepted messages (bot removed / token revoked -> admin warning)
    if sender.channel_attempts:
        state.data["channel_fail"] = not sender.channel_ok
    log.info("run: %d alerts, %d instant news, %d queued, briefs=%s", len(alerts), len(instant), len(digest), names)
