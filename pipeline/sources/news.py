"""News collectors: RSS feeds and Google News searches."""
from __future__ import annotations

import calendar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser

from ..common import NewsItem, clean_ws, http, log, strip_source_suffix


def _entry_time(e) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        t = e.get(k)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def _fetch_feed(name: str, url: str, pk: bool, official: bool, google: bool = False) -> list[NewsItem]:
    try:
        r = http().get(url, timeout=25)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("feed %s failed: %s", name, e)
        return []
    parsed = feedparser.parse(r.content)
    items = []
    for e in parsed.entries:
        title = clean_ws(e.get("title", ""))
        if not title:
            continue
        source = name
        if google:
            title, suffix = strip_source_suffix(title)
            src = e.get("source", {})
            source = (src.get("title") if isinstance(src, dict) else None) or suffix or "Google News"
        summary = "" if google else clean_ws(e.get("summary", ""))
        if "<" in summary:  # strip HTML from summaries
            from bs4 import BeautifulSoup
            summary = clean_ws(BeautifulSoup(summary, "lxml").get_text(" "))
        items.append(NewsItem(title=title, url=e.get("link", ""), source=source,
                              published=_entry_time(e), summary=summary[:400],
                              pk=pk, official=official))
    return items


def google_news_url(query: str) -> str:
    return (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}"
            "&hl=en-PK&gl=PK&ceid=PK:en")


def collect(cfg: dict) -> list[NewsItem]:
    jobs = []
    for f in cfg.get("feeds", []):
        jobs.append((f["name"], f["url"], bool(f.get("pk")), bool(f.get("official")), False))
    for q in cfg.get("google_news", []):
        jobs.append((f"GN:{q[:30]}", google_news_url(q), False, False, True))
    out: list[NewsItem] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(lambda j: _fetch_feed(*j), jobs):
            out.extend(res)
    log.info("news: %d raw items from %d sources", len(out), len(jobs))
    return out
