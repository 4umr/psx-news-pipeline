"""Relevance scoring and duplicate detection for news items."""
from __future__ import annotations

import re
import time

from .common import NewsItem

_STOP = set("""a an the of to in on for and or by with at from as is are was were be been
has have had it its this that after over amid into up down new says said will
pakistan pakistan's pakistani""".split())


class Scorer:
    def __init__(self, cfg: dict):
        self.pk_re = re.compile(cfg["pk_context"], re.I)
        self.exclude = re.compile(cfg["exclude"], re.I) if cfg.get("exclude") else None
        lp = cfg.get("low_priority_sources")
        self.low_src = re.compile(lp, re.I) if lp else None
        self.topics = []
        for t in cfg["topics"]:
            groups = [re.compile("|".join(f"(?:{p})" for p in g), re.I) for g in t["all"]]
            self.topics.append((t, groups))

    def score(self, item: NewsItem) -> NewsItem:
        text = f"{item.title}. {item.summary}"
        title = item.title
        if (self.exclude and self.exclude.search(title)) or title.count("|") >= 2:
            item.score = 0
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
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", title.lower())
    return sorted({w for w in words if w not in _STOP and len(w) > 1})


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
