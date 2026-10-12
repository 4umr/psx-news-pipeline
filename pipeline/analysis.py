"""Read the actual article and analyse it with Google Gemini (free tier).

For important news the headline alone is not enough: this module opens the article,
extracts its text and asks Gemini for a factual summary, the key numbers, the impact
on Pakistani markets and the sectors affected, using ONLY what the article says.

Needs the GEMINI_API_KEY secret. Without it (or if Gemini fails) the caller falls
back to the rule-based impact engine (impact.py), so posting never stops.
"""
from __future__ import annotations

import json
import os
import re

from .common import http, log

SECTORS = ["Banks", "E&P", "OMCs", "Refineries", "Power", "Cement", "Fertilizer", "Autos", "Steel", "Textile",
           "IT", "Pharma", "FMCG", "Chemicals", "Insurance", "Broad market"]
API = "https://generativelanguage.googleapis.com/v1beta"
_model: str | None = None
_disabled = False

PROMPT = """You are a financial news analyst writing for investors in the Pakistan Stock Exchange (PSX).
Read the article below and answer ONLY from what it actually says. Do not guess, do not add outside facts,
do not invent numbers. If the article text is missing or thin, say less rather than more.

Return JSON with exactly these keys:
"relevant": true if this news can affect Pakistani markets, the economy, rates, the rupee or listed companies; otherwise false.
"summary": two short, simple sentences explaining what happened, including the key numbers from the article.
"why": one short sentence on why it matters for Pakistani investors.
"key_facts": up to 3 short factual points with numbers, taken from the article.
"impact_level": "High" if it directly moves PSX, interest rates or the rupee now; "Medium" if it matters for a sector or the outlook; "Low" otherwise.
"tone": "Positive", "Negative", "Mixed" or "Neutral" for Pakistani equities.
"positive_sectors": sectors likely to benefit, chosen only from: {sectors}.
"pressured_sectors": sectors likely to come under pressure, chosen only from the same list.

Style: plain, professional English. No emojis, no dashes, no hype, no exclamation marks.

Headline: {title}
Source: {source}
Article text:
{text}
"""


def _key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def available(cfg: dict) -> bool:
    return bool(_key()) and (cfg.get("ai") or {}).get("enabled", True) and not _disabled


def article_text(url: str, max_chars: int = 6000) -> str:
    """Main text of a news article (paragraphs), or '' if it can't be read."""
    from bs4 import BeautifulSoup

    from .links import resolve_google
    try:
        r = http().get(resolve_google(url), timeout=20)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.info("article fetch failed: %s", e)
        return ""
    if r.content[:4] == b"%PDF" or "pdf" in r.headers.get("Content-Type", ""):
        try:  # official releases (SBP, PBS) are often PDFs
            import io

            from pypdf import PdfReader
            pages = PdfReader(io.BytesIO(r.content)).pages[:4]
            return re.sub(r"[ \t]+", " ", "\n".join(p.extract_text() or "" for p in pages)).strip()[:max_chars]
        except Exception as e:  # noqa: BLE001
            log.info("PDF text extraction failed: %s", e)
            return ""
    soup = BeautifulSoup(r.content, "lxml")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        t.decompose()
    root = soup.find("article") or soup
    paras = [re.sub(r"\s+", " ", p.get_text(" ")).strip() for p in root.find_all("p")]
    paras = [p for p in paras if len(p) > 60 and not re.search(r"cookie|subscribe|newsletter|all rights reserved", p, re.I)]
    return "\n".join(paras)[:max_chars]


def _pick_model(cfg: dict) -> str:
    """Configured model, or the newest available 'flash' model if that name is not served any more."""
    global _model
    if _model:
        return _model
    want = (cfg.get("ai") or {}).get("model", "gemini-2.5-flash")
    try:
        r = http().get(f"{API}/models", headers={"x-goog-api-key": _key()}, timeout=20)
        names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        if want in names:
            _model = want
        else:
            flash = sorted((n for n in names if "flash" in n and not re.search(r"lite|image|tts|audio|live|exp", n)),
                           reverse=True)
            _model = (flash or names or [want])[0]
            log.info("Gemini model '%s' not available, using '%s'", want, _model)
    except Exception as e:  # noqa: BLE001
        log.info("Gemini model list failed (%s), using %s", e, want)
        _model = want
    return _model


def _clean(s) -> str:
    from .style import tidy
    return tidy(str(s or "")).replace("!", ".").strip()


def analyse(title: str, url: str, source: str, cfg: dict, summary: str = "") -> dict | None:
    """Gemini's reading of the article: {relevant, summary, why, key_facts, level, tone, pos, neg} or None."""
    global _disabled
    if not available(cfg):
        return None
    text = article_text(url) if url else ""
    if len(text) < 300:
        text = (text + "\n" + (summary or "")).strip() or "(article text not available; use the headline only)"
    model = _pick_model(cfg)
    body = {"contents": [{"parts": [{"text": PROMPT.format(sectors=", ".join(SECTORS), title=title,
                                                            source=source, text=text)}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    try:
        r = http().post(f"{API}/models/{model}:generateContent", json=body, timeout=45,
                        headers={"x-goog-api-key": _key(), "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            _disabled = True  # bad key: stop trying this run
            log.error("Gemini refused the API key (HTTP %s)", r.status_code)
            return None
        if r.status_code == 429:
            log.warning("Gemini rate limit reached; using rule-based impact for now")
            return None
        r.raise_for_status()
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        d = json.loads(re.sub(r"^```(json)?|```$", "", raw.strip()))
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini analysis failed: %s", e)
        return None
    level = d.get("impact_level") if d.get("impact_level") in ("High", "Medium", "Low") else "Medium"
    tone = d.get("tone") if d.get("tone") in ("Positive", "Negative", "Mixed", "Neutral") else "Neutral"
    pos = [s for s in d.get("positive_sectors") or [] if s in SECTORS][:4]
    neg = [s for s in d.get("pressured_sectors") or [] if s in SECTORS and s not in pos][:4]
    out = {"relevant": bool(d.get("relevant", True)), "summary": _clean(d.get("summary")), "why": _clean(d.get("why")),
           "facts": [_clean(f) for f in (d.get("key_facts") or [])][:3], "level": level, "tone": tone,
           "pos": pos, "neg": neg, "sectors": [], "read_article": len(text) >= 300, "ai": True}
    if not out["summary"]:
        return None
    return out
