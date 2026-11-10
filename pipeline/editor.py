"""The editor in front of the main channel: which important news is worth an infographic post.

Evidence from the first live day (10 Nov 2026): 14 news posts reached the channel and only about 4 were
worth it. The rest were the same event from a second outlet, weak websites (blogs, Indian sites, daily
price pages), speculation ("petrol to drop tonight"), ceremonial statements, and an index level from an
old article. So every candidate now passes these gates, in order:

  1. source      trusted outlet or official release (config editor.trusted_sources)
  2. facts       a KSE-100 level in the headline must be close to the real index
  3. topic cap   at most editor.max_per_topic_day posts on one topic per day
  4. AI editor   materiality 1-5, confirmed or not, same event as an earlier post (analysis.analyse)
  5. no AI       if Gemini is unavailable: only official items or trusted score-10 news, and no second
                 post on a topic within editor.topic_gap_hours

Everything held back goes to the admin chat with the reason, and every decision is logged in
state.data["news_log"] (public state branch) so the rules can be reviewed with real data.
"""
from __future__ import annotations

import re
import time
from collections import Counter

from .common import NewsItem, esc, log

_clock = time.time  # the replay test (review.py) moves the clock to each historical item

# data alerts -> topic, so news about an event already shown as a data card is recognised
ALERT_TOPIC = {"sbp:pr": "monetary_policy", "sbp:res": "reserves_external", "sbp:mtb": "govt_securities",
               "sbp:pib": "govt_securities", "cpi": "inflation", "spi": "inflation", "fuel": "fuel_prices",
               "remit": "reserves_external", "bop": "external_trade", "autos": "sector_data",
               "kse": "market_shock", "results": "psx_corporate", "mpc": "monetary_policy"}
_INDEX_WORDS = re.compile(r"\b(kse[- ]?100|kse|psx|index|benchmark|bourse|stock exchange)\b", re.I)
_FORECAST = re.compile(r"\b(target|could|may|might|expected|expects?|forecast|projected?|projection|estimate[sd]?|"
                       r"by (june|december|year[- ]end)|year[- ]end|outlook|eyes|aims?)\b", re.I)
_LEVEL = re.compile(r"(?<![\d.])(\d{2,3},\d{3}|\d{5,6})(?![\d.,])")


def _cfg(cfg: dict) -> dict:
    return cfg.get("editor") or {}


def trusted(it: NewsItem, cfg: dict) -> bool:
    if it.official:
        return True
    pat = _cfg(cfg).get("trusted_sources")
    return bool(pat and re.search(pat, it.source or "", re.I))


def index_conflict(it: NewsItem, levels: list[float], tol: float) -> str:
    """'' or a reason: the headline gives a KSE-100 level far from today's real index (old article resurfacing)."""
    text = it.title
    if not levels or not _INDEX_WORDS.search(text) or _FORECAST.search(text):
        return ""
    nums = [float(m.replace(",", "")) for m in _LEVEL.findall(text)]
    nums = [n for n in nums if 20_000 <= n <= 1_000_000]
    if not nums:
        return ""
    if any(min(abs(n / lv - 1) for lv in levels) <= tol for n in nums):
        return ""
    return f"index level {nums[0]:,.0f} in the headline is far from the real KSE-100 ({levels[0]:,.0f})"


def index_levels(state, view: list[dict]) -> list[float]:
    out = []
    if view and view[0].get("CurrentIndex"):
        out += [view[0]["CurrentIndex"], view[0].get("PreIndex") or view[0]["CurrentIndex"]]
    closes = list((state.data.get("kse_closes") or {}).values())
    out += closes[-5:]
    return [x for x in out if x]


# ---------------------------------------------------------------- what the main channel already carried
def recent_posts(state, hours: float = 24) -> list[dict]:
    cut = _clock() - hours * 3600
    return [p for p in state.data.get("posted", []) if p["ts"] >= cut]


def record_post(state, title: str, topic: str, kind: str = "news") -> None:
    posted = state.data.setdefault("posted", [])
    posted.append({"ts": int(_clock()), "t": re.sub(r"<[^>]+>", "", title)[:200], "topic": topic, "kind": kind})
    cut = _clock() - 3 * 86400
    state.data["posted"] = [p for p in posted if p["ts"] >= cut][-200:]


def record_alert(state, key: str, title: str) -> None:
    prefix = re.split(r"[:]", key or "", maxsplit=1)[0]
    two = ":".join((key or "").split(":")[:2])
    topic = ALERT_TOPIC.get(two) or ALERT_TOPIC.get(prefix) or "data"
    record_post(state, title, topic, "data")


def posted_list(state, hours: float = 24, limit: int = 25) -> list[str]:
    """For the AI prompt: 'HH:MM [topic] title' of recent main-channel posts, newest last."""
    from datetime import datetime

    from .common import now_pkt
    tz = now_pkt().tzinfo
    return [f"{datetime.fromtimestamp(p['ts'], tz):%H:%M} [{p['topic']}] {p['t']}" for p in recent_posts(state, hours)[-limit:]]


def topic_count(state, topic: str, hours: float = 24) -> int:
    return sum(1 for p in recent_posts(state, hours) if p["topic"] == topic and p["kind"] == "news")


def topic_gap_ok(state, topic: str, hours: float) -> bool:
    return not any(p["topic"] == topic for p in recent_posts(state, hours))


# ---------------------------------------------------------------- gates
def before_ai(it: NewsItem, state, cfg: dict, view: list[dict]) -> str:
    """Checks that need no AI. '' = continue, else the reason to keep it off the main channel."""
    ed = _cfg(cfg)
    if not trusted(it, cfg):
        return "source not on the trusted list"
    if r := index_conflict(it, index_levels(state, view), float(ed.get("index_tolerance", 0.10))):
        return r
    cap = int(ed.get("max_per_topic_day", 3))
    if not it.official and it.score < 10 and topic_count(state, it.topic) >= cap:
        return f"already {cap} posts on this topic today"
    return ""


def after_ai(it: NewsItem, ai: dict | None, status: str, state, cfg: dict) -> str:
    """Decision once the AI has (or has not) read the article. '' = post it."""
    ed = _cfg(cfg)
    if ai is None:
        if status == "check_failed":
            return "AI could not produce a summary it could verify against the article"
        # Gemini unavailable (no key, budget, rate limit, error): strict backup rule
        if not (it.official or it.score >= 10):
            return "AI unavailable and not a top-score or official item"
        if not topic_gap_ok(state, it.topic, float(ed.get("topic_gap_hours", 3))):
            return "AI unavailable and the main channel already covered this topic recently"
        return ""
    if not ai["relevant"]:
        return "AI: not relevant to Pakistani markets"
    if ai["stale"]:
        return "AI: old event or contradicts official data"
    if ai["kind"] != "news":
        return f"AI: {ai['kind']}, not news"
    if ai.get("same_event"):
        return f"AI: same event as an earlier post ({ai['same_event'][:80]})"
    mat, need = ai.get("materiality", 3), int(ed.get("min_materiality", 4))
    if mat < need:
        return f"AI: materiality {mat} of 5 (needs {need})"
    if not ai.get("confirmed", True) and mat < 5:
        return "AI: not confirmed (claim, proposal or expectation)"
    return ""


# ---------------------------------------------------------------- decision log
def log_decision(state, it: NewsItem, decision: str, reason: str = "", ai: dict | None = None) -> None:
    lg = state.data.setdefault("news_log", [])
    row = {"ts": int(_clock()), "t": it.title[:160], "s": it.source[:40], "sc": it.score, "topic": it.topic,
           "d": decision, "why": reason[:120]}
    if ai:
        row.update(mat=ai.get("materiality"), conf=ai.get("confirmed"), kind=ai.get("kind"), lvl=ai.get("level"))
    lg.append(row)
    state.data["news_log"] = lg[-400:]
    log.info("editor: %s%s | %s | %s", decision, f" ({reason})" if reason else "", it.source[:25], it.title[:90])


def admin_note(reason: str) -> str:
    return f"<i>Kept off the main channel: {esc(reason)}</i>\n"


BUCKETS = (("source not on", "weak source"), ("index level", "wrong index level"), ("already", "topic cap"),
           ("materiality", "minor"), ("not confirmed", "unconfirmed"), ("same event", "duplicate"),
           ("not relevant", "not relevant"), ("old event", "old or wrong"), ("not news", "not news"),
           ("AI unavailable", "AI unavailable"), ("verify", "AI could not verify"), ("policy-rate", "rate rumour"))


def _bucket(reason: str) -> str:
    return next((b for k, b in BUCKETS if k in reason), reason.split("(")[0][:30].strip())


def day_summary(state, day_start: float) -> str:
    rows = [r for r in state.data.get("news_log", []) if r["ts"] >= day_start]
    if not rows:
        return ""
    posted = sum(1 for r in rows if r["d"] == "posted")
    why = Counter(_bucket(r["why"]) for r in rows if r["d"] != "posted")
    top = ", ".join(f"{k} {v}" for k, v in why.most_common(6))
    return f"Main channel news: {posted} posted, {len(rows) - posted} kept for you" + (f" ({esc(top)})" if top else "")
