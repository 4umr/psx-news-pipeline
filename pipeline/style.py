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


def meta(cfg: dict, topic: str) -> dict:
    return (cfg.get("topic_meta") or {}).get(topic, {}) or {}
