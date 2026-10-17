"""AI layer (Google Gemini, free tier): reads the actual article, sees the whole market, explains.

1. analyse(): for important news, opens the article (HTML or PDF), gives Gemini the article
   AND the market context pack (rates, CPI, flows, index trend, oil, dollar, other news,
   broker views) and asks for a factual summary, impact, sectors, stocks in view, how it
   fits the backdrop and what to watch next.
2. narrate(): short market commentary for briefs ("What to watch today", "Why the market
   moved", "The big picture") written only from the numbers we give it.

Guardrails, so mistakes never reach the channel:
- Gemini may only use numbers that appear in the article or the data we gave it. Every
  number in its answer is checked; text with an unverifiable number is dropped (and the
  rule-based fallback is used).
- Sectors from a fixed list; stocks must be real KSE-100 tickers.
- Sentences with buy/sell style advice are removed.
- A daily request budget keeps us inside the free tier.
Needs the GEMINI_API_KEY secret; without it everything falls back to the rule engine.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date

from .common import http, log

SECTORS = ["Banks", "E&P", "OMCs", "Refineries", "Power", "Cement", "Fertilizer", "Autos", "Steel", "Textile",
           "IT", "Pharma", "FMCG", "Chemicals", "Insurance", "Broad market"]
API = "https://generativelanguage.googleapis.com/v1beta"
_model: str | None = None
_disabled = False
_state = None
_limit = 200

_ADVICE = re.compile(r"\b(buy|sell|accumulate|book (profits?|gains)|target price|go long|go short|"
                     r"should (buy|sell|invest)|recommend\w*)\b", re.I)

ANALYSE = """You are a senior financial analyst writing for investors in the Pakistan Stock Exchange (PSX).
Read the ARTICLE, then judge it against the MARKET CONTEXT (today's real data). Use ONLY facts and numbers
from the article and the context. Never invent numbers, dates or quotes. If unsure, say less.

Return JSON with exactly these keys:
"relevant": true if it can affect Pakistani markets, the economy, rates, the rupee or listed companies.
"kind": "news" (new event or official data), "opinion" (editorial/column/comment), "analysis" (explainer/background) or "promo" (advertising/sponsored).
"event_date": date the main event happened or was announced, YYYY-MM-DD from the article, or null.
"conflicts_official": true if the article's main claim contradicts official data in the MARKET CONTEXT (for example it reports a policy rate, CPI or reserves figure different from the official one there), otherwise false. Official data always wins over headlines.
"summary": two short, simple sentences on what happened, with the key numbers from the article.
"why": one short sentence on why it matters for Pakistani investors.
"context": one short sentence linking it to the current backdrop in the MARKET CONTEXT (for example rates, inflation, flows, oil or the index trend). Use only numbers from the context.
"watch": one short sentence on what to watch next (a date, a data release, a decision) if the article or context gives one; otherwise "".
"key_facts": up to 3 short factual points with numbers from the article.
"impact_level": "High" if it directly moves PSX, interest rates or the rupee now; "Medium" if it matters for a sector or the outlook; "Low" otherwise.
"tone": "Positive", "Negative", "Mixed" or "Neutral" for Pakistani equities.
"positive_sectors" and "pressured_sectors": chosen only from: {sectors}.
"stocks_in_view": up to 4 KSE-100 tickers directly named in or clearly affected by the article, chosen only from: {tickers}.

Style: plain, professional English for ordinary investors. No emojis, no dashes, no hype, no exclamation marks.
Never give advice to buy or sell.

MARKET CONTEXT:
{context}

HEADLINE: {title}
SOURCE: {source}
ARTICLE:
{text}
"""

NARRATE = """You are a senior financial analyst writing a short market commentary for PSX investors.
Write {what}. Use ONLY the facts and numbers in the DATA below. Never invent or estimate numbers.
Return JSON: {{"points": [3 or 4 short sentences, each one clear idea, most important first]}}.
Style: plain, professional English, no emojis, no dashes, no hype, no advice to buy or sell.

DATA:
{data}
"""


def _key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def init(state, cfg: dict) -> None:
    """Called once per run: remembers the state for the daily request budget."""
    global _state, _limit
    _state, _limit = state, int((cfg.get("ai") or {}).get("daily_limit", 200))


def available(cfg: dict) -> bool:
    return bool(_key()) and (cfg.get("ai") or {}).get("enabled", True) and not _disabled


def _budget_ok() -> bool:
    if _state is None:
        return True
    use = _state.data.setdefault("ai_usage", {})
    today = f"{date.today()}"
    for d in [d for d in use if d != today]:
        del use[d]
    if use.get(today, 0) >= _limit:
        log.warning("AI daily budget (%d requests) used up; rule-based fallback until tomorrow", _limit)
        return False
    use[today] = use.get(today, 0) + 1
    return True


def article_text(url: str, max_chars: int = 6000) -> str:
    """Main text of a news article or PDF release, or '' if it can't be read."""
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


def _call(cfg: dict, prompt: str, timeout: int = 60) -> dict | None:
    global _disabled
    if not available(cfg) or not _budget_ok():
        return None
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    try:
        r = http().post(f"{API}/models/{_pick_model(cfg)}:generateContent", json=body, timeout=timeout,
                        headers={"x-goog-api-key": _key(), "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            _disabled = True  # bad key: stop trying this run
            log.error("Gemini refused the API key (HTTP %s)", r.status_code)
            return None
        if r.status_code == 429:
            log.warning("Gemini rate limit reached; using rule-based fallback for now")
            return None
        r.raise_for_status()
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(re.sub(r"^```(json)?|```$", "", raw.strip()))
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini request failed: %s", e)
        return None


def health(cfg: dict) -> str:
    """Daily check: 'ok <model>' / 'no key' / 'refused' / 'error ...' (lists models; no generation quota used)."""
    global _disabled
    if not _key():
        return "no key"
    try:
        r = http().get(f"{API}/models", headers={"x-goog-api-key": _key()}, timeout=20)
        if r.status_code in (400, 401, 403):
            _disabled = True
            return f"refused (HTTP {r.status_code})"
        r.raise_for_status()
        return f"ok {_pick_model(cfg)}"
    except Exception as e:  # noqa: BLE001
        return f"error {type(e).__name__}"


# ---------------------------------------------------------------- guardrails
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _nums(text: str) -> set[float]:
    out = set()
    for m in _NUM.findall(text or ""):
        try:
            out.add(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def numbers_ok(text: str, sources: str) -> bool:
    """Every number in `text` must appear in `sources` (allowing rounding, thousands and bn/m/% forms)."""
    have = _nums(sources)
    for n in _nums(text):
        if n < 10 and n == int(n):
            continue  # small counts ("3 sessions", "2 sentences") and list numbering
        if any(abs(n - h) <= max(0.051, abs(h) * 0.001) or int(h) == n or abs(n * 1000 - h) < 1 or abs(n - h * 1000) < 1 or
               abs(n * 1e6 - h) < 1e3 or abs(round(h) - n) < 0.5 or abs(n - h / 1000) < 0.006 for h in have):
            continue
        if 1990 <= n <= 2100:  # years
            continue
        return False
    return True


def _clean(s) -> str:
    from .style import tidy
    t = tidy(str(s or "")).replace("!", ".").strip()
    # drop any sentence that reads like investment advice
    return " ".join(x for x in re.split(r"(?<=[.])\s+", t) if x and not _ADVICE.search(x)).strip()


def _checked(s, sources: str) -> str:
    t = _clean(s)
    return t if t and numbers_ok(t, sources) else ""


# ---------------------------------------------------------------- news analysis
def analyse(title: str, url: str, source: str, cfg: dict, summary: str = "", context: str = "",
            tickers: list[str] | None = None) -> dict | None:
    """Gemini's reading of the article in today's market context, checked against the sources; or None."""
    if not available(cfg):
        return None
    text = article_text(url) if url else ""
    if len(text) < 300:
        text = (text + "\n" + (summary or "")).strip() or "(article text not available; use the headline only)"
    tick = tickers or []
    d = _call(cfg, ANALYSE.format(sectors=", ".join(SECTORS), tickers=", ".join(tick) or "(none listed)",
                                  context=context or "(not available)", title=title, source=source, text=text))
    if not d:
        return None
    article = f"{title}\n{text}"
    both = f"{article}\n{context}"
    out_summary = _checked(d.get("summary"), article)
    if not out_summary:
        log.warning("AI summary failed the number check, using rule-based text: %s", title[:70])
        return None
    level = d.get("impact_level") if d.get("impact_level") in ("High", "Medium", "Low") else "Medium"
    tone = d.get("tone") if d.get("tone") in ("Positive", "Negative", "Mixed", "Neutral") else "Neutral"
    pos = [s for s in d.get("positive_sectors") or [] if s in SECTORS][:4]
    neg = [s for s in d.get("pressured_sectors") or [] if s in SECTORS and s not in pos][:4]
    stale = False
    if ev := d.get("event_date"):
        try:
            stale = (date.today() - date.fromisoformat(str(ev)[:10])).days > 3
        except ValueError:
            stale = False
    kind = d.get("kind") if d.get("kind") in ("news", "opinion", "analysis", "promo") else "news"
    stocks = [t for t in (d.get("stocks_in_view") or []) if t in tick][:4]
    if d.get("conflicts_official") is True:
        log.warning("AI: article contradicts official data, kept off main channel: %s", title[:80])
        stale = True
    return {"kind": kind, "stale": stale, "event_date": d.get("event_date"), "relevant": bool(d.get("relevant", True)),
            "summary": out_summary, "why": _checked(d.get("why"), both), "context": _checked(d.get("context"), both),
            "watch": _checked(d.get("watch"), both),
            "facts": [f for f in (_checked(x, article) for x in (d.get("key_facts") or [])[:3]) if f],
            "level": level, "tone": tone, "pos": pos, "neg": neg, "stocks": stocks, "sectors": [],
            "read_article": len(text) >= 300, "ai": True}


# ---------------------------------------------------------------- brief commentary
def narrate(cfg: dict, what: str, data: str) -> list[str]:
    """3-4 short commentary points written only from `data`; [] if unavailable or unverifiable."""
    if not data.strip():
        return []
    d = _call(cfg, NARRATE.format(what=what, data=data))
    if not d:
        return []
    pts = [p for p in (_checked(x, data) for x in (d.get("points") or [])[:4]) if p]
    if len(pts) < 2:
        log.warning("AI commentary dropped (failed the number check): %s", what)
        return []
    return pts
