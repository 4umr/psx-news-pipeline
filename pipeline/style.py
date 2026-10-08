"""Shared message styling: clean headlines, sections and a one-line branded signature.

Messages are designed to read well in Telegram AND survive copy-paste /
forwarding to WhatsApp (structure is carried by emojis and line breaks,
not only by bold text).
"""
from __future__ import annotations

from .common import esc, link

DIV = "────────────"


def header(icon: str, title: str, sub: str = "") -> str:
    """Friendly headline: icon + bold title, with an optional quiet subtitle line."""
    line = f"{icon} <b>{esc(title)}</b>"
    return f"{line}\n<i>{esc(sub)}</i>\n" if sub else f"{line}\n"


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


import re as _re

_EMOJI = _re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⌚-⏿"
                     "️‍▪▫◻-◾ℹ‼⁉]")


def tidy(text: str) -> str:
    """Clean, calm text for the channel: no emoji clutter, no long dashes.

    Applied to every channel post and caption just before sending, so it holds everywhere.
    Keeps ▲ ▼ (direction) and the · separator; turns "→"/"—" into plain words/punctuation.
    """
    if not text:
        return text
    t = _re.sub(r"^\s*🔗\s*", "Source: ", text, flags=_re.M)
    t = _re.sub(r"^\s*💡\s*", "Why it matters: ", t, flags=_re.M)
    t = _re.sub(r"^\s*🏭\s*In focus:", "Sectors in focus:", t, flags=_re.M)
    t = _re.sub(r"📄\s*", "Filing: ", t)
    t = _re.sub(r"(\d%?)\s*[➜→]\s*(\d)", r"\1 to \2", t)
    t = _re.sub(r"\s*[➜→]\s*", ": ", t)
    t = _re.sub(r"(\d)\s*–\s*(\d)", r"\1 to \2", t)
    t = t.replace("–", "-")
    t = _re.sub(r"\s*—\s*", ": ", t)
    t = _EMOJI.sub("", t)
    lines = [_re.sub(r"[ \t]{2,}", " ", ln).strip() for ln in t.split("\n")]
    lines = [ln for ln in lines if ln not in ("·", ":", "-")]
    t = "\n".join(lines)
    t = _re.sub(r"(<[bi]>)\s+", r"\1", t)
    return _re.sub(r"\n{3,}", "\n\n", t).strip()


def footer(cfg: dict, tags: list[str] | None = None, compact: bool = False) -> str:
    """One quiet signature line: brand · author · disclaimer (+ Join link). `tags` kept for compatibility."""
    b = cfg.get("brand", {})
    by = f"by {esc(b.get('author', ''))}" + (f", {esc(b.get('title'))}" if b.get("title") else "")
    line = f"{esc(b.get('name', ''))} · {by}"
    if cfg.get("disclaimer") and (cfg.get("disclaimer_on_alerts") or not compact):
        line += " · info only, not investment advice"
    out = f"\n\n<i>{line}</i>"
    if b.get("channel_link"):
        out += f"\n{link(b['channel_link'], 'Join ' + b.get('name', 'the channel'))}"
    return out


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
        t += f"\nJoin on Telegram: {link}"
    return t.strip()


def meta(cfg: dict, topic: str) -> dict:
    return (cfg.get("topic_meta") or {}).get(topic, {}) or {}
