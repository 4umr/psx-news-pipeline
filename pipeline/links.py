"""Visible, copy-paste-safe article links.

Telegram hides the address behind link text, and that address is lost when a post is
copied into WhatsApp. So posts show the real URL as plain text. To keep it short:
- Google News redirect links are decoded to the publisher's own article URL;
- known Pakistani news sites are trimmed to their short form (the slug is optional);
- tracking parameters are dropped.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit, urlunsplit

from .common import http, log

_CACHE: dict[str, str] = {}

_SHORT = [
    re.compile(r"^(https://www\.dawn\.com/news/\d+)"),
    re.compile(r"^(https://www\.brecorder\.com/news/\d+)"),
    re.compile(r"^(https://tribune\.com\.pk/story/\d+)"),
    re.compile(r"^(https://www\.thenews\.com\.pk/\w+/\d+)"),
    re.compile(r"^(https://www\.arabnews\.pk/node/\d+)"),
]


def resolve_google(url: str) -> str:
    """news.google.com/rss/articles/... -> the publisher's article URL (falls back to the input)."""
    if "news.google.com" not in url:
        return url
    if url in _CACHE:
        return _CACHE[url]
    try:
        aid = url.split("/articles/")[1].split("?")[0]
        html = http().get(f"https://news.google.com/rss/articles/{aid}", timeout=15).text
        sg = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        if not (sg and ts):
            return url
        req = [["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,'
                          f'null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts.group(1)},"{sg.group(1)}"]']]
        r = http().post("https://news.google.com/_/DotsSplashUi/data/batchexecute", timeout=15,
                        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                        data=f"f.req={quote(json.dumps([req]))}")
        real = json.loads(json.loads(r.text.split("\n\n")[1])[0][2])[1]
        if isinstance(real, str) and real.startswith("http"):
            _CACHE[url] = real
            return real
    except Exception as e:  # noqa: BLE001
        log.info("could not resolve Google News link: %s", e)
    return url


def short(url: str) -> str:
    """Shortest working form of an article URL, without tracking parameters."""
    if not url:
        return url
    url = resolve_google(url)
    for rx in _SHORT:
        if m := rx.match(url):
            return m.group(1)
    p = urlsplit(url)
    query = "&".join(q for q in p.query.split("&") if q and not q.lower().startswith(("utm_", "fbclid", "gclid", "oc=")))
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") or "/", query, ""))


def visible(url: str) -> str:
    """Display form: no 'https://' or 'www.' — still recognised as a link by Telegram and WhatsApp."""
    s = short(url)
    return re.sub(r"^https?://(www\.)?", "", s)
