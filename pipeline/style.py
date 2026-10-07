"""Shared message styling: headers, dividers, hashtags and the branded footer.

Messages are designed to read well in Telegram AND survive copy-paste /
forwarding to WhatsApp (structure is carried by emojis and line breaks,
not only by bold text).
"""
from __future__ import annotations

from .common import esc, link

DIV = "━━━━━━━━━━━━━━━━━━"


def header(icon: str, title: str, sub: str = "") -> str:
    line = f"{icon} <b>{esc(title.upper())}</b>"
    return f"{line}\n{esc(sub)}\n{DIV}" if sub else f"{line}\n{DIV}"


def section(icon: str, title: str) -> str:
    return f"{icon} <b>{esc(title)}</b>"


def hashtags(tags: list[str]) -> str:
    seen, out = set(), []
    for t in tags:
        t = "".join(ch for ch in t if ch.isalnum())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(f"#{t}")
    return " ".join(out)


def footer(cfg: dict, tags: list[str] | None = None, compact: bool = False) -> str:
    b = cfg.get("brand", {})
    parts = []
    if tags:
        parts.append(hashtags(tags + ["PSX"]))
    parts.append(DIV)
    by = f"by {esc(b.get('author', ''))}" + (f", {esc(b.get('title'))}" if b.get("title") else "")
    brand_line = f"🇵🇰 <b>{esc(b.get('name', ''))}</b> · {by}"
    if b.get("channel_link") and not compact:
        brand_line += f" · {link(b['channel_link'], 'Join')}"
    parts.append(brand_line)
    if cfg.get("disclaimer") and (cfg.get("disclaimer_on_alerts") or not compact):
        parts.append(f"<i>ℹ️ {esc(cfg['disclaimer'])}</i>")
    return "\n" + "\n".join(parts)


def to_whatsapp(text: str, cfg: dict) -> str:
    """Convert a Telegram-HTML message to WhatsApp formatting (*bold*, _italic_).

    Links: short official links are kept; long tracking links (Google News) are
    dropped so the copy stays clean — the source name remains.
    """
    import html as _html
    import re

    def _a(m):
        url, label = m.group(1), m.group(2)
        if len(url) <= 90 and "news.google.com" not in url:
            return f"{label} ({url})" if label not in ("📄",) else url
        return label

    t = re.sub(r'<a href="([^"]+)">(.*?)</a>', _a, text, flags=re.S)
    t = re.sub(r"</?b>", "*", t)
    t = re.sub(r"</?i>", "_", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    t = t.replace("**", "")  # empty bold pairs
    t = re.sub(r"\n#[^\n]*\n", "\n", t)  # hashtags are Telegram-only
    link = cfg.get("brand", {}).get("channel_link")
    if link:
        t += f"\n👉 Join on Telegram: {link}"
    return t.strip()


def meta(cfg: dict, topic: str) -> dict:
    return (cfg.get("topic_meta") or {}).get(topic, {}) or {}
