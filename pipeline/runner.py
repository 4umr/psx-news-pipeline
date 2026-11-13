"""One pipeline pass: collect -> detect changes -> score news -> send."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import analysis, briefs, context, editor, expectations, impact, intraday, official, street, watchers
from .common import Alert, NewsItem, esc, hours_ago, in_window, link, log, now_pkt, to_pkt
from .links import short
from .scoring import Scorer, is_duplicate, is_stale, title_tokens
from .sources import markets, news, sbp, scs
from .state import State
from .style import footer, header, to_whatsapp
from .telegram import Sender


def _news_alerts(items: list[NewsItem], state: State, cfg: dict) -> tuple[list[NewsItem], list[NewsItem]]:
    scorer = Scorer(cfg)
    picked: list[NewsItem] = []
    now = now_pkt()
    global_topics = {t["name"] for t in cfg["topics"] if not t.get("pk")}
    cap = cfg.get("max_per_global_topic_3h", 3)
    recent_topics: dict[str, int] = {}
    for h in state.data["headlines"]:
        if h["ts"] >= time.time() - 3 * 3600:
            recent_topics[h.get("topic", "")] = recent_topics.get(h.get("topic", ""), 0) + 1
    for it in items:
        if not it.title or state.seen(it.uid):
            continue
        state.mark(it.uid)
        if it.published and hours_ago(it.published) > cfg["max_item_age_hours"]:
            continue
        if is_stale(it.title, it.url, it.published, now):
            continue
        scorer.score(it)
        if it.score < cfg["min_score_digest"]:
            continue
        # Global stories (Fed, oil...) come in waves of 30+ near-identical takes: keep the first few
        if it.topic in global_topics and it.score < cfg["min_score_instant"] and recent_topics.get(it.topic, 0) >= cap:
            continue
        toks = title_tokens(it.title)
        if is_duplicate(toks, state.titles, it.topic):
            continue
        recent_topics[it.topic] = recent_topics.get(it.topic, 0) + 1
        state.add_title(toks, it.topic)
        state.add_headline({"t": it.title, "u": it.url, "s": it.source, "sc": it.score,
                            "l": it.label, "topic": it.topic, "ts": int(time.time())})
        picked.append(it)
    picked.sort(key=lambda i: -i.score)
    # Main channel only gets FRESH important news; older items go to the admin raw feed instead
    max_h = cfg.get("max_important_age_hours", 6)
    fresh = lambda i: (hours_ago(i.published) <= max_h) if i.published else i.official  # noqa: E731
    instant = [i for i in picked if i.score >= cfg["min_score_instant"] and fresh(i)][: cfg["max_instant_per_run"]]
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


def _impact_line(imp: dict) -> str:
    return f"{imp['level']} impact" + (f", {imp['tone'].lower()}" if imp["tone"] != "Neutral" else "")


def _fmt_instant(it: NewsItem, cfg: dict, imp: dict | None = None) -> str:
    when = to_pkt(it.published) or now_pkt()
    _, topic = _split_label(it.label)
    imp = imp or impact.assess(it.title, it.topic, it.score, cfg, it.summary)
    lines = [f"<b>{'Breaking' if it.score >= 10 else 'Important'}</b> · {esc(topic)}", "",
             f"<b>{esc(it.title)}</b>", "", f"<b>{_impact_line(imp)}</b>. {esc(imp['summary'])}"]
    if sl := impact.sectors_line(imp):
        lines.append(esc(sl))
    lines.append(f"{when:%H:%M} PKT · {link(it.url, it.source)}")
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
    _, topic = _split_label(d["l"])
    imp = impact.assess(d["t"], d.get("topic", ""), d.get("sc", 0), cfg, d.get("sm", ""))
    lines = [f"<b>{esc(d['t'])}</b>",
             f"<i>{esc(topic)} · {when:%H:%M} PKT · {_impact_line(imp)}</i>",
             esc(imp["summary"])]
    if sl := impact.sectors_line(imp):
        lines.append(esc(sl))
    lines.append(link(d["u"], d["s"]))
    return "\n".join(lines)


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
                        "l": it.label, "topic": it.topic, "ts": int(time.time()), "sm": (it.summary or "")[:300],
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
    raw_admin = cfg.get("raw_news_to_admin", True)
    every = float(cfg.get("raw_news_admin_digest_minutes", 120))
    for d in send:
        if raw_admin and every > 0:  # the owner's bot chat: one headlines digest every couple of hours, not 200 messages
            state.data.setdefault("admin_feed", []).append(_fmt_compact(d, cfg))
        elif raw_admin:
            sender.send_admin(_fmt_compact(d, cfg))  # raw pipeline feed: owner's admin channel only
        else:
            sender.send(_fmt_compact(d, cfg))
    state.data["pending"] = keep
    _flush_admin_feed(cfg, state, sender, now)
    return len(send)


def _flush_admin_feed(cfg: dict, state: State, sender: Sender, now: datetime, force: bool = False) -> None:
    """Send the collected raw headlines as one digest (split under Telegram's length limit)."""
    feed = state.data.get("admin_feed") or []
    if not feed:
        return
    every = float(cfg.get("raw_news_admin_digest_minutes", 120)) * 60
    last = state.data.get("admin_feed_at", 0)
    if not force and len(feed) < 25 and time.time() - last < every:
        return
    from .lab import chunks
    since = to_pkt(datetime.fromtimestamp(last, tz=timezone.utc)) if last else None
    head = (f"<b>Headlines</b> · {len(feed)} kept for you" + (f" since {since:%H:%M}" if since else "")
            + " (each with the reason it stayed off the channel)")
    for part in chunks(head + "\n\n" + "\n\n".join(feed)):
        sender.send_admin(part)
    state.data["admin_feed"] = []
    state.data["admin_feed_at"] = int(time.time())


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
    title = re.sub(r"<[^>]+>", "", spec.get("title") or "")
    head = f"<b>{esc(title)}</b>" if title else a.text.split("\n", 1)[0]
    if spec.get("why"):
        head += f"\n\n{esc(spec['why'])}"
    links = [ln for ln in a.text.split("\n") if ln.startswith("🔗")]
    links += [f"📄 {esc(sym)} · {esc(short(u))}" for sym, u in spec.get("links", [])]
    return _caption(cfg, head, links)


def about_text(cfg: dict) -> str:
    b = cfg.get("brand", {})
    sched = cfg.get("briefs", {})
    t = lambda n, d: sched.get(n, {}).get("time", d)  # noqa: E731
    return (header("", f"Welcome to {b.get('name', 'PSX')}", "PSX news, data and analysis") + "\n"
            "Pakistan Stock Exchange news, data and macro indicators, 24/7, always with sources. "
            "Updates come as shareable infographics.\n\n"
            "<b>⏰ What you get & when (PKT)</b>\n"
            f"<b>{t('morning', '08:45')}</b> Morning brief: PSX, US market close, commodity update, top stories (Mon to Fri)\n"
            f"<b>{t('opening', '10:00')}</b> Opening bell: how the session started\n"
            f"<b>{t('midday', '12:30')}</b> Midday pulse: intraday chart, movers, sectors\n"
            f"<b>{t('close', '17:15')}</b> Market close: gainers and losers, sectors, highlights\n"
            "<b>Evening</b> Foreign and local investor flows (FIPI/LIPI)\n"
            f"<b>{t('results', '18:30')}</b> Results tracker in results season: EPS vs last year, payouts\n"
            "<b>CPI day</b> Macro dashboard: inflation, rates, reserves, rupee, market\n"
            f"<b>Sun {t('week_ahead', '19:00')}</b> Week in review, the week ahead, Street View scoreboard\n"
            "<b>As it happens</b> Relevant headlines with their likely impact and the sectors affected\n\n"
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
             f"Street forecasts tracked: {len(state.data.get('street', {}).get('forecasts', []))}",
             f"AI analysis: {state.snap('ai_status') or 'not checked'} · requests today "
             f"{(state.data.get('ai_usage') or {}).get(str(now.date()), 0)}"]
    if ed := editor.day_summary(state, now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()):
        lines.append(ed)
    if day.get("runs", 0) < 30:
        lines.append("ℹ️ Fewer runs than expected — GitHub's free scheduler may be slow today; "
                     "the 2-minute cron-job.org trigger fixes this.")
    sender.send_admin("\n".join(lines))


def run_once(cfg: dict, dry_run: bool = False, force_brief: str | None = None, light: bool = False) -> None:
    """One pass. light=True (fast loop): only the quick sources (RSS news, PSX data, results, filings of the
    companies that matter today); SBP, PBS, global markets and Google News are left to the next full pass."""
    t0 = time.time()
    state = State()
    sender = Sender(dry_run)
    now = now_pkt()
    errors: list[str] = []
    try:
        _run(cfg, state, sender, now, errors, force_brief, t0, light)
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
        log.info("%s finished in %.1fs, messages=%d, component errors=%d",
                 "light pass" if light else "run", time.time() - t0, sender.sent, len(errors))


def fast_window(cfg: dict, now: datetime | None = None) -> bool:
    """True in market hours (config `fast_loop`): the loop then runs at its fast rhythm."""
    fl = cfg.get("fast_loop") or {}
    now = now or now_pkt()
    if not fl.get("enabled", True) or now.weekday() not in fl.get("days", [0, 1, 2, 3, 4]):
        return False
    return in_window(now, fl.get("start", "08:30"), fl.get("end", "18:45"))


def set_continue(more: bool) -> None:
    """Tells the workflow whether to start the next job right away (GitHub step output `continue`)."""
    import os
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"continue={'true' if more else 'false'}\n")


def loop_wanted(cfg: dict) -> bool:
    """Should a job keep looping now? Always, when `fast_loop.always_on`; else only inside the fast window."""
    fl = cfg.get("fast_loop") or {}
    return bool(fl.get("enabled", True)) and (bool(fl.get("always_on", True)) or fast_window(cfg))


def run_loop(cfg: dict, dry_run: bool = False, minutes: int | None = None) -> bool:
    """The job that stays open, so nothing depends on GitHub's schedule (which runs hours late).

    Inside the fast window (market hours): a light pass every `every_seconds`, a full pass every
    `full_every` passes. Outside it (evenings, nights, weekends): a full pass every `idle_seconds`.
    Stops after `minutes`; the workflow then starts the next job. Returns True if another job is wanted."""
    fl = cfg.get("fast_loop") or {}
    every = int(fl.get("every_seconds", 60))
    idle = int(fl.get("idle_seconds", 600))
    full_every = max(1, int(fl.get("full_every", 5)))
    minutes = minutes or int(fl.get("chunk_minutes", 100))
    stop_at = time.time() + minutes * 60
    n = 0
    log.info("loop: every %ds in market hours (full pass every %d), every %ds otherwise, for up to %d minutes",
             every, full_every, idle, minutes)
    try:
        while loop_wanted(cfg) and time.time() < stop_at:
            t0 = time.time()
            fast = fast_window(cfg)
            run_once(cfg, dry_run, light=fast and n % full_every != 0)
            n += 1
            # next pass one interval after this one STARTED, but always at least 5 seconds of rest
            wake = min(max(t0 + (every if fast_window(cfg) else idle), time.time() + 5), stop_at)
            while time.time() < wake:
                time.sleep(min(20.0, max(0.5, wake - time.time())))
                if not fast and fast_window(cfg):
                    break  # market hours just started: switch to the fast rhythm now
    except KeyboardInterrupt:
        log.info("loop interrupted")
    more = loop_wanted(cfg)
    log.info("loop done: %d passes, next job wanted: %s", n, more)
    set_continue(more)
    return more


def _screener_probe(now: datetime, state: State, view: list[dict]) -> None:
    """Once a trading day around 11:00: is PSX's screener page live during the session? (scstrade's all-stock
    table is not: it updates once, after the close.) Compares it with the live KSE-100 view and keeps the
    answer in state snap 'screener_probe' (last 10 days) for a decision on live prices for every stock."""
    if now.weekday() > 4 or not in_window(now, "11:00", "13:00") or state.flag(f"scrprobe:{now:%Y-%m-%d}"):
        return
    state.set_flag(f"scrprobe:{now:%Y-%m-%d}")
    from .sources import psx
    scr = psx.screener()
    n = live = prev = 0
    for r in view or []:
        c, p, ld = r.get("company_code"), r.get("CurrentPrice"), r.get("LDCP")
        if not c or not p or not ld or abs(p - ld) < 0.006 or c not in scr:
            continue
        n += 1
        live += abs(scr[c][0] - p) <= max(0.011, p * 0.002)
        prev += abs(scr[c][0] - ld) < 0.006
    res = {"time": f"{now:%Y-%m-%d %H:%M}", "stocks": len(scr), "compared": n, "same_as_live": live, "same_as_prev_close": prev}
    hist = (state.snap("screener_probe") or [])[-9:] + [res]
    state.set_snap("screener_probe", hist)
    log.info("PSX screener probe: %s", res)


def _start_workflows(cfg: dict, state: State, now: datetime) -> None:
    """Start the other workflows (swing scan, Stock of the Day, weekly cards, re-test) at their proper
    time. GitHub's own schedule starts them hours late; their schedules stay as a backstop and each of
    them refuses to do the same work twice. Needs GITHUB_TOKEN (set by the workflow); skipped locally."""
    import os
    from datetime import timedelta
    from .common import http, parse_hhmm
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return
    for d in cfg.get("start_workflows") or []:
        if now.weekday() not in d.get("days", [0, 1, 2, 3, 4, 5, 6]):
            continue
        h, m = parse_hhmm(d["time"])
        sched = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"wf:{d['workflow']}:{d['command']}:{d['time']}:{now:%Y-%m-%d}"
        if not sched <= now <= sched + timedelta(minutes=d.get("grace", 90)) or state.flag(key):
            continue
        tries = state.data.setdefault("wf_tries", {})
        try:
            r = http().post(f"https://api.github.com/repos/{repo}/actions/workflows/{d['workflow']}/dispatches",
                            json={"ref": "main", "inputs": {"command": d["command"]}}, timeout=20,
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"})
            ok, why = r.status_code == 204, f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            ok, why = False, type(e).__name__
        if ok:
            state.set_flag(key)
            tries.pop(key, None)
            log.info("started %s (%s) on time", d["workflow"], d["command"])
            continue
        tries[key] = tries.get(key, 0) + 1
        log.warning("could not start %s (%s): %s, attempt %d", d["workflow"], d["command"], why, tries[key])
        if tries[key] >= 3:  # give up for today: the workflow's own (late) schedule still runs it
            state.set_flag(key)
            tries.pop(key, None)
    for k in [k for k in state.data.get("wf_tries", {}) if not k.endswith(f"{now:%Y-%m-%d}")]:
        del state.data["wf_tries"][k]


def _contradicts_official(it: NewsItem, state: State, now: datetime) -> bool:
    """A media report of a policy-rate cut/hike when SBP held no meeting today or yesterday (often an old
    article resurfacing). The SBP watcher posts real decisions straight from SBP, so these are held back."""
    from datetime import date as _date
    if it.topic != "monetary_policy" or it.official:
        return False
    t = it.title.lower()
    if not re.search(r"\b(cuts?|reduc\w*|lowers?|slash\w*|hikes?|raises?|increas\w*)\b", t):
        return False
    if re.search(r"expect|likely|\bmay\b|could|poll|forecast|seen|analyst|survey|preview|ahead|hope|demand|urge", t):
        return False
    dates = (state.snap("mpc") or {}).get("dates", [])
    return not any(0 <= (now.date() - _date.fromisoformat(d)).days <= 1 for d in dates)


def _refresh_broker_notes(state: State, now: datetime) -> None:
    """Hourly on weekdays: today's broker research headlines, for the AI context pack."""
    from .briefs import _broker_short
    from .sources import brokers
    if now.weekday() > 4 or not 8 <= now.hour <= 18 or state.flag(f"bn:{now:%Y-%m-%d-%H}"):
        return
    state.set_flag(f"bn:{now:%Y-%m-%d-%H}")
    notes = [f"{_broker_short(r['broker'])}: {r['title']}" for r in brokers.reports() if r["date"].date() == now.date()]
    if notes:
        state.set_snap("broker_notes", notes[:10])


def _run(cfg: dict, state: State, sender: Sender, now: datetime, errors: list, force_brief, t0: float,
         light: bool = False) -> None:
    analysis.init(state, cfg)
    if light and (state.is_new or force_brief or briefs.due(cfg, state, now)):
        light = False  # a brief needs the full data set (global markets, SBP)
    mpc_today = f"{now:%Y-%m-%d}" in (state.snap("mpc") or {}).get("dates", [])
    want_sbp = not light or mpc_today  # on MPC day every pass watches SBP for the decision
    if not state.flag(f"ai_check:{now:%Y-%m-%d}"):
        state.set_flag(f"ai_check:{now:%Y-%m-%d}")
        status = analysis.health(cfg)
        log.info("AI health check: %s", status)
        state.set_snap("ai_status", f"{now:%Y-%m-%d %H:%M} {status}")
    bootstrap = state.is_new
    if bootstrap:
        log.info("First run: recording current state silently (no flood of old news)")

    def S(name, fn, *a, default=None):
        return _safe(state, errors, name, fn, *a, default=default)

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_news = ex.submit(S, "news feeds", news.collect, cfg, not light, default=[])
        f_sbp = ex.submit(S, "SBP", sbp.fetch, default=({}, [])) if want_sbp else None
        f_res = ex.submit(S, "SCS results", scs.results, default=[])
        f_mk = ex.submit(S, "global markets", markets.snapshot, list(cfg["global_markets"]), default={}) if not light else None
        f_view = ex.submit(S, "KSE-100 view", scs.kse100_view, default=[])
        sbp_snap, sbp_items = f_sbp.result() if f_sbp else ({}, [])
        mk = f_mk.result() if f_mk else (state.snap("mk_last") or {})
        view_now = f_view.result()
        kse_symbols = {r.get("company_code") for r in view_now}
        news_items = f_news.result()
    if not light and mk:
        state.set_snap("mk_last", mk)  # light passes reuse the last global snapshot (card tiles, context)
    if intraday.session_open(now) and not bootstrap:
        S("intraday record", lambda: intraday.record(now, scs.live_activity(view_now)[0], view_now))
    S("intraday housekeeping", intraday.housekeeping, now)
    S("PSX screener probe", _screener_probe, now, state, view_now)
    if not bootstrap:  # after the close: were all prices posted today inside the day's real range?
        rc = S("reality check", watchers.reality_check, now, state)
        if rc:
            sender.send_admin(rc)
        if now.hour >= 22:  # whatever headlines are left go out before the night
            _flush_admin_feed(cfg, state, sender, now, force=True)
    if not bootstrap:  # private level alerts for the stocks of last night's Research Lab report
        from . import levelwatch
        msg = S("level alerts", levelwatch.check, now, state, cfg, view_now)
        if msg:
            sender.send_admin(msg)

    if not light:
        _health(cfg, state, sender, {
            "SBP website": bool(sbp_snap.get("policy_rate")),
            "PSX data (SCS Trade)": bool(kse_symbols),
            "News feeds": len(news_items) >= 100,
            "Global markets": len(mk) >= 5,
            "Telegram channel delivery": not state.data.get("channel_fail", False),
        })

    S("street capture", street.capture, sbp_items + news_items, state)
    S("EPS expectations", expectations.capture, news_items, state)

    alerts: list[Alert] = []
    if not light:
        alerts += S("PBS releases", watchers.pbs_releases, state, default=[])  # before news: marks PBS posts handled
        alerts += S("MPC calendar", watchers.mpc_watch, now, state, default=[])
        S("MPC hold check", watchers.mpc_hold_check, now, state)
    alerts += S("PSX filings", watchers.psx_filings, now, state, cfg, view_now, light, default=[])
    alerts += S("unusual volume", watchers.unusual_volume, now, state, cfg, kse_symbols, default=[])
    if want_sbp:
        alerts += S("SBP changes", watchers.sbp_changes, sbp_snap, state, default=[])
    alerts += S("corporate results", watchers.corporate_results, f_res.result(), state, kse_symbols, cfg, default=[])
    if not light:
        alerts += S("FIPI", watchers.fipi_alert, now, state, default=[])
        alerts += S("official data", official.official_data, now, state, cfg, default=[])
    kse_alerts, view = S("KSE moves", watchers.kse_moves, now, state, cfg, default=([], []))
    alerts += kse_alerts
    view = view or view_now
    if not light:
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
    S("MPC poll", watchers.mpc_poll, now, state, sender)
    if not sender.dry:
        S("workflow starter", _start_workflows, cfg, state, now)

    wa_mode = cfg.get("brand", {}).get("whatsapp_copy", "important")

    def wa(text: str, important: bool) -> None:
        if sender.admin and (wa_mode == "all" or (wa_mode == "important" and important)):
            sender.send_admin_plain("WhatsApp-ready copy (long-press, copy, paste):\n\n" + to_whatsapp(text, cfg))

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
        editor.record_alert(state, a.key, (a.extra.get("card") or {}).get("title") or a.text.split("\n", 1)[0])
        S("whatsapp copy", wa, msg, a.priority >= 9)
    ai_budget = (cfg.get("ai") or {}).get("max_per_run", 6)
    ctx_text, tickers = "", context.tickers(view_now)
    if instant and analysis.available(cfg):
        S("broker notes", _refresh_broker_notes, state, now)
        ctx_text = S("market context", context.build, state, view_now, mk, default="") or ""

    def hold(it: NewsItem, reason: str, ai: dict | None = None) -> None:
        """Not for the main channel: the owner still sees it in the admin chat, with the reason."""
        editor.log_decision(state, it, "admin", reason, ai)
        sender.send_admin(editor.admin_note(reason) + _fmt_compact(
            {"t": it.title, "u": it.url, "s": it.source, "sc": it.score, "l": it.label, "topic": it.topic,
             "ts": int(time.time())}, cfg))

    # Items whose AI reading failed on a busy Gemini (errors 429/503), or that did not fit in this pass's AI
    # budget, come back first: up to 5 tries in 60 min. Before 14 Nov 2026 an item over the per-pass budget was
    # dropped untried ("AI unavailable"), e.g. NEPRA industrial rates and SBP's Rs 500bn profit on 13 Nov.
    tries: dict[str, int] = {}
    firsts: dict[str, int] = {}
    for r in state.data.pop("ai_retry", []):
        if time.time() - r["first"] < 3600:
            back = NewsItem(title=r["t"], url=r["u"], source=r["s"], summary=r.get("sm", ""), pk=True,
                            official=r.get("off", False),
                            published=datetime.fromtimestamp(r["pub"], tz=now.tzinfo) if r.get("pub") else None)
            back.score, back.topic, back.label = r["sc"], r["topic"], r["l"]
            tries[back.title], firsts[back.title] = r["n"], r["first"]
            instant.insert(0, back)
    if instant and not ctx_text and analysis.available(cfg):
        ctx_text = S("market context", context.build, state, view_now, mk, default="") or ""

    for it in instant:
        imp = impact.assess(it.title, it.topic, it.score, cfg, it.summary)
        if _contradicts_official(it, state, now):
            hold(it, "policy-rate claim with no SBP meeting today or yesterday")
            continue
        if reason := editor.before_ai(it, state, cfg, view_now):
            hold(it, reason)
            continue
        ai, status = None, "unavailable"
        if ai_budget > 0:
            ai_budget -= 1
            ai = S("article analysis", analysis.analyse, it.title, it.url, it.source, cfg, it.summary,
                   ctx_text, tickers, editor.posted_list(state))
            status = analysis.last_status
        elif analysis.available(cfg):
            status = "error"  # not tried: this pass's AI budget is used up, so it waits for the next pass
        if status == "error" and tries.get(it.title, 0) < 4:
            first = firsts.get(it.title, int(time.time()))
            state.data.setdefault("ai_retry", []).append(
                {"t": it.title, "u": it.url, "s": it.source, "sm": (it.summary or "")[:300], "off": it.official,
                 "pub": int(it.published.timestamp()) if it.published else None, "sc": it.score, "topic": it.topic,
                 "l": it.label, "n": tries.get(it.title, 0) + 1, "first": first})
            log.info("AI busy, will try again next pass: %s", it.title[:80])
            continue
        if reason := editor.after_ai(it, ai, status, state, cfg):
            hold(it, reason, ai)
            continue
        if ai:
            imp = {**ai, "sectors": imp["sectors"] if not (ai["pos"] or ai["neg"]) else []}
        msg = _fmt_instant(it, cfg, imp)
        _, topic = _split_label(it.label)
        when = to_pkt(it.published) or now
        breaking = it.score >= 10 and imp["level"] == "High"
        spec = {"theme": "alert" if breaking else TOPIC_THEME.get(it.topic, "economy"),
                "kicker": f"{'Breaking' if breaking else 'Important'} · {topic}", "title": it.title,
                "impact": imp, "why": imp["summary"], "source": f"{it.source} · {when:%H:%M} PKT"}
        if ai and ai["facts"]:
            spec.update(rows_title="Key facts", rows=[("", f, "", None) for f in ai["facts"]])
        if ai:
            spec["why_title"] = "What happened"
        # caption: short, plain explanation under the image
        if ai and (ai.get("context") or ai.get("watch")):
            spec["extra_title"] = "The bigger picture"
            spec["extra"] = [x for x in (ai.get("context"), ("Watch: " + ai["watch"]) if ai.get("watch") else "") if x]
        if ai and ai.get("stocks"):
            spec["stocks"] = ai["stocks"]
        lines = [esc(imp["summary"])]
        if ai and ai.get("why"):
            lines.append(esc(ai["why"]))
        if ai and ai.get("context"):
            lines.append(esc(ai["context"]))
        lines.append("")
        lines.append(f"<b>{_impact_line(imp)}</b>" + (f". {esc(sl)}" if (sl := impact.sectors_line(imp)) else ""))
        if ai and ai.get("stocks"):
            lines.append("Stocks in view: " + ", ".join(ai["stocks"]))
        if ai and ai.get("watch"):
            lines.append("Watch: " + esc(ai["watch"]))
        cap = _caption(cfg, f"<b>{esc(it.title)}</b>\n\n" + "\n".join(lines), [link(it.url, it.source)])
        _post_with_card(sender, cfg, msg, spec, when, "news", _market_tiles(state, view_now), cap)
        editor.record_post(state, it.title, it.topic)
        editor.log_decision(state, it, "posted", "", ai)
        S("whatsapp copy", wa, msg, it.score >= 10)
    if analysis._disabled and not state.flag(f"gemini_bad:{now:%Y-%m-%d}"):
        state.set_flag(f"gemini_bad:{now:%Y-%m-%d}")
        sender.send_admin("<b>Gemini key problem:</b> Google refused the GEMINI_API_KEY secret, so important news "
                          "is using the backup rules today. Create a new key at aistudio.google.com/apikey and "
                          "update the secret in GitHub.")
    _queue_digest(digest, state)
    if cfg.get("news_mode", "instant") == "instant":
        S("news posts", _flush_instant, cfg, state, sender, now)
    elif _digest_due(cfg, state, now):
        S("news digest", _flush_digest, cfg, state, sender)

    names = [force_brief] if force_brief else briefs.due(cfg, state, now)
    if not force_brief and any(a.key.startswith("cpi:") and not a.admin for a in alerts):
        names.append("macro")  # monthly macro dashboard right after the CPI release
    for name in names:
        text = S(f"{name} brief", _send_brief, name, cfg, state, mk, view, sender)
        if text:
            S("whatsapp copy", wa, text, True)
            state.data["briefs"][name] = f"{now:%Y-%m-%d}"  # only mark sent if it actually built

    # remember whether the channel accepted messages (bot removed / token revoked -> admin warning)
    if sender.channel_attempts:
        state.data["channel_fail"] = not sender.channel_ok
    log.info("run: %d alerts, %d instant news, %d queued, briefs=%s", len(alerts), len(instant), len(digest), names)
