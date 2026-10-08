"""Shared helpers: config, HTTP session, time, text utilities."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("pipeline")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


_session: requests.Session | None = None


def http() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        retry = Retry(total=2, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504, 520, 521, 522, 523, 524],
                      allowed_methods=["GET", "POST"])
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=20))
        s.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=20))
        _session = s
    return _session


PKT = ZoneInfo("Asia/Karachi")


def now_pkt() -> datetime:
    return datetime.now(PKT)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def esc(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(text or "", quote=False)


def link(url: str, label: str) -> str:
    """Source name + the real, visible URL.

    Hidden hyperlinks (<a href>) lose their address when a post is copied from
    Telegram into WhatsApp, so the URL is always shown as plain text — Telegram and
    WhatsApp both turn it into a clickable link. Google News redirects are resolved
    to the publisher's article and long URLs are trimmed (see links.py).
    """
    if not url:
        return esc(label)
    from .links import short
    u = esc(short(url))
    if label in ("📄", "📄 filing", "view filing"):
        return f"📄 {u}"
    return f"{esc(label)} · {u}"


def clean_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fmt_num(x: float | None, dec: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{x:,.{dec}f}"


def fmt_pct(x: float | None, dec: int = 2) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{dec}f}%"


def arrow(x: float | None) -> str:
    if x is None:
        return "▫️"
    return "🟢" if x > 0 else ("🔴" if x < 0 else "⚪")


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: datetime | None = None
    summary: str = ""
    pk: bool = False          # source is Pakistan-only
    official: bool = False    # official/government source
    # filled by scoring
    score: int = 0
    topic: str = ""
    label: str = ""
    why: str = ""

    @property
    def uid(self) -> str:
        return self.url or self.title


@dataclass
class Alert:
    """A ready-to-send message produced by a data watcher (not a news item)."""
    text: str
    priority: int = 9            # >= min_score_instant -> sent standalone
    key: str = ""                # dedupe key
    preview: bool = False        # Telegram link preview
    tags: list = field(default_factory=list)
    admin: bool = False          # route to the owner's private chat only (e.g. data-quality warning)
    extra: dict = field(default_factory=dict)


def strip_source_suffix(title: str) -> tuple[str, str | None]:
    """Google News titles look like 'Headline - Source'. Split them."""
    m = re.match(r"^(.*\S)\s+[-–|]\s+([^-–|]{2,60})$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return title.strip(), None


def to_pkt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PKT)


def hours_ago(dt: datetime | None) -> float:
    if dt is None:
        return 0.0
    return (now_utc() - dt).total_seconds() / 3600


def parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def in_window(t: datetime, start: str, end: str) -> bool:
    sh, sm = parse_hhmm(start)
    eh, em = parse_hhmm(end)
    return (sh, sm) <= (t.hour, t.minute) <= (eh, em)


def date_from_ms(s: str) -> datetime | None:
    """Parse ASP.NET '/Date(1791313200000)/' into a PKT datetime."""
    m = re.search(r"Date\((-?\d+)", s or "")
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).astimezone(PKT)


def week_range(t: datetime, days: int = 7) -> tuple[datetime, datetime]:
    start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=days)
