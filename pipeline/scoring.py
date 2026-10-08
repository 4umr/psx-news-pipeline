"""Relevance scoring and duplicate detection for news items."""
from __future__ import annotations

import re
import time

from .common import NewsItem

_STOP = set("""a an the of to in on for and or by with at from as is are was were be been
has have had it its this that after over amid into new says said will
pakistan pakistan's pakistani""".split())

# Same event, different wording: "rise $15mn" == "gain $15 million"
_SYN = {"rise": "up", "rises": "up", "rose": "up", "gain": "up", "gains": "up", "gained": "up", "increase": "up",
        "increases": "up", "increased": "up", "climb": "up", "climbs": "up", "jump": "up", "jumps": "up",
        "surge": "up", "surges": "up", "higher": "up", "fall": "down", "falls": "down", "fell": "down",
        "drop": "down", "drops": "down", "dropped": "down", "decline": "down", "declines": "down",
        "declined": "down", "decrease": "down", "slip": "down", "slips": "down", "lower": "down",
        "plunge": "down", "plunges": "down", "sheds": "down", "shed": "down", "cut": "cut", "cuts": "cut",
        "slashes": "cut", "reduces": "cut", "pts": "points", "pt": "points", "reserve": "reserves",
        "forex": "fx", "foreign": "fx"}
_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mn|m|million|bn|b|billion|pc|percent|%)\b", re.I)

# "VLO, MPC, SUN, PSX Stocks Climb" / "(NYSE: PSX)" = US ticker stories, not Pakistan news
_TICKER_LIST = re.compile(r"\b[A-Z]{2,5}(?:,\s*(?:and\s+)?[A-Z]{2,5}){2,}\b")
_US_LISTING = re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|NYSEARCA|AMEX|OTC)\s*:", re.I)

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-]+({_MONTHS})[a-z]*\.?[\s,\-]+(20\d\d)\b", re.I)
_DATE_MDY = re.compile(rf"\b({_MONTHS})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)


def date_hint(*texts: str):
    """A full calendar date written in a headline or URL (e.g. 'PR-14-Sep-2026', 'Oct. 8, 2025'), or None."""
    from datetime import date
    for t in texts:
        for rx, order in ((_DATE_DMY, (0, 1, 2)), (_DATE_MDY, (1, 0, 2))):
            if m := rx.search(t or ""):
                g = m.groups()
                d, mon, y = g[order[0]], g[order[1]], g[order[2]]
                try:
                    return date(int(y), _MONTHS.split("|").index(mon[:3].lower()) + 1, int(d))
                except ValueError:
                    continue
    return None


def is_stale(title: str, url: str, published, now) -> bool:
    """Old items that slip through feeds: undated official items with an old date in the
    title/URL, or re-surfaced articles dated (almost exactly) a year or more ago."""
    hint = date_hint(title, url)
    if not hint:
        return False
    age = (now.date() - hint).days
    if published is None:
        return age > 3
    try:
        same_day_other_year = hint.year < now.year and abs((now.date() - hint.replace(year=now.year)).days) <= 3
    except ValueError:  # 29 Feb
        same_day_other_year = False
    return same_day_other_year


class Scorer:
    def __init__(self, cfg: dict):
        self.pk_re = re.compile(cfg["pk_context"], re.I)
        self.exclude = re.compile(cfg["exclude"], re.I) if cfg.get("exclude") else None
        et = cfg.get("exclude_titles")
        self.exclude_titles = re.compile(et, re.I) if et else None
        lp = cfg.get("low_priority_sources")
        self.low_src = re.compile(lp, re.I) if lp else None
        self.topics = []
        for t in cfg["topics"]:
            groups = [re.compile("|".join(f"(?:{p})" for p in g), re.I) for g in t["all"]]
            self.topics.append((t, groups))

    def score(self, item: NewsItem) -> NewsItem:
        text = f"{item.title}. {item.summary}"
        title = item.title
        if (self.exclude and self.exclude.search(title)) or title.count("|") >= 2 or \
                (self.exclude_titles and self.exclude_titles.search(title)):
            item.score = 0
            return item
        if not item.pk and (_TICKER_LIST.search(title) or _US_LISTING.search(title)):
            item.score = 0  # US stock-ticker story ("MPC", "PSX" are also NYSE tickers)
            return item
        has_pk = item.pk or bool(self.pk_re.search(text))
        best = None
        for t, groups in self.topics:
            if t.get("pk") and not has_pk:
                continue
            # Title must hit the first group; other groups may hit title or summary
            if not groups[0].search(title):
                continue
            scope = title if t.get("title_only") else text
            if all(g.search(scope) for g in groups[1:]):
                s = t["score"]
                if best is None or s > best[0]:
                    best = (s, t)
        if best is None:
            # Official releases are always worth a look even without a topic hit
            if item.official:
                item.score, item.topic, item.label, item.why = 5, "official", "🏛️ Official Release", ""
            return item
        s, t = best
        if item.official:
            # Boost official releases on critical topics (MPC, CPI, auctions...);
            # routine ones (schemes, circulars) go to the digest instead.
            s = s + 1 if s >= 8 and not title.startswith("SBP Circular") else min(s, 7)
        if self.low_src and self.low_src.search(item.source):
            s -= 2
        item.score, item.topic, item.label, item.why = s, t["name"], t["label"], t.get("why", "")
        return item


def title_tokens(title: str) -> list[str]:
    t = _UNIT.sub(r"\1", title.lower().replace(",", ""))
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", t)
    return sorted({_SYN.get(w, w) for w in words if w not in _STOP and (len(w) > 1 or w.isdigit())})


def dedupe(items: list[dict], key: str = "t") -> list[dict]:
    """Drop items that report the same story as an earlier (higher-ranked) one in the list."""
    out, seen = [], []
    for it in items:
        toks = title_tokens(it[key])
        if is_duplicate(toks, seen, it.get("topic", "")):
            continue
        seen.append([toks, time.time(), it.get("topic", "")])
        out.append(it)
    return out


def is_duplicate(tokens: list[str], recent: list, topic: str = "", threshold: float = 0.6) -> bool:
    """Same story from another outlet? recent = [[tokens, ts, topic], ...]."""
    if not tokens:
        return False
    a = set(tokens)
    cutoff_same_topic = time.time() - 12 * 3600
    for entry in recent:
        other, ts = entry[0], entry[1]
        other_topic = entry[2] if len(entry) > 2 else ""
        b = set(other)
        if not b:
            continue
        inter = len(a & b)
        if inter / len(a | b) >= threshold or (inter >= 4 and inter / min(len(a), len(b)) >= 0.6):
            return True
        # Same topic, recent, sharing 4+ meaningful words -> re-report of the same event
        if topic and topic == other_topic and ts >= cutoff_same_topic and inter >= 4:
            return True
    return False
