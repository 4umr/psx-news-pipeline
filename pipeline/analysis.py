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
last_status = ""   # outcome of the latest analyse(): ok / unavailable / error / check_failed

_ADVICE = re.compile(r"\b(buy|sell|accumulate|book (profits?|gains)|target price|go long|go short|"
                     r"should (buy|sell|invest)|recommend\w*)\b", re.I)

ANALYSE = """You are a senior financial analyst writing for investors in the Pakistan Stock Exchange (PSX).
Read the ARTICLE, then judge it against the MARKET CONTEXT (today's real data). Use ONLY facts and numbers
from the article and the context. Never invent numbers, dates or quotes. If unsure, say less.

Return JSON with exactly these keys:
"relevant": true if it can affect Pakistani markets, the economy, rates, the rupee or listed companies.
"kind": "news" (new event or official data), "opinion" (editorial/column/comment), "analysis" (explainer, background, how-to, comparison such as "gold vs stocks") or "promo" (advertising/sponsored).
"confirmed": true only if the main event has actually happened or been officially announced or notified. false for forecasts, expectations, proposals, demands, claims by politicians or parties, "sources say" reports, and anything described with may, could, likely, set to or expected.
"materiality": how much a PSX investor needs this on their phone right now, 1 to 5:
  5 = moves the whole market, interest rates or the rupee today (policy rate decision, IMF approval or tranche, major new tax, KSE-100 move of 2% or more, war or major security escalation, sovereign rating change);
  4 = clearly moves a sector or a major listed company (fuel or gas or power price change actually notified, a big company result, deal or regulatory decision, official sector data, T-bill or PIB auction result);
  3 = useful background for investors but not urgent;
  2 = minor, local or ceremonial (meetings, visits, MoUs without amounts, statements of intent, speeches, protests, local shortages, political claims of credit);
  1 = not market news.
"same_event": if this article reports the same event as one of the ALREADY POSTED items (the same decision, figure or announcement, even in different words or from another outlet), copy that item's text exactly; otherwise null. A genuinely new development on the same subject is not the same event.
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

ALREADY POSTED on the channel in the last 24 hours:
{posted}

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


_AVAILABLE: list[str] | None = None
_LAST_RESEARCH = [0.0]


def _available_models() -> list[str]:
    """Models this key may call (generateContent), newest names first; [] if the list cannot be read."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            r = http().get(f"{API}/models", headers={"x-goog-api-key": _key()}, timeout=20)
            _AVAILABLE = sorted((m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                                 if "generateContent" in m.get("supportedGenerationMethods", [])), reverse=True)
        except Exception as e:  # noqa: BLE001
            log.info("Gemini model list failed (%s)", e)
            _AVAILABLE = []
    return _AVAILABLE


_DEAD: set[str] = set()   # models that answered 404 in this run: never tried again until the next run


def _backups(cfg: dict, main: str) -> list[str]:
    """Other models to fall back on, best first: the configured backup if this key lists it, then the other text
    'flash' models on the key (stable '-latest' aliases and lite models first). Each model has its own free quota.
    13 Nov 2026: 'gemini-2.5-flash-lite' answered 404 although configured, so one fixed backup is not enough."""
    want = (cfg.get("ai") or {}).get("fallback_model", "gemini-2.5-flash-lite")
    names = _available_models()
    if not names:
        return [m for m in (want,) if m not in _DEAD]
    text = [n for n in names if "flash" in n and n != main
            and not re.search(r"image|tts|audio|live|exp|preview|thinking|embedding|vision", n)]
    text.sort(key=lambda n: (n != want, "latest" not in n, "lite" not in n))
    return [n for n in text if n not in _DEAD][:3]


def _backup_model(cfg: dict, main: str) -> str | None:
    b = _backups(cfg, main)
    return b[0] if b else None


def models_for(cfg: dict, research: bool = False) -> list[str]:
    """Which Gemini models to try, in order. News: the main model, then up to three backups. Research (Research
    Lab, the annual-report reader) starts on the first backup, so the main model's free quota stays with the news."""
    main = _pick_model(cfg)
    backups = _backups(cfg, main)
    order = (backups[:1] + [main] + backups[1:]) if research else ([main] + backups)
    return [m for m in dict.fromkeys(order) if m and m not in _DEAD]


def call_parts(cfg: dict, parts: list[dict], timeout: int = 60, research: bool = False) -> dict | None:
    """Like _call, with several parts (text, images, PDFs as inline_data) in one request."""
    return _call(cfg, "", timeout, research, parts=parts)


def _call(cfg: dict, prompt: str, timeout: int = 60, research: bool = False, parts: list[dict] | None = None) -> dict | None:
    """One request; on a rate limit (429), an overloaded model (5xx), an unknown model (404) or a time-out the
    next model in models_for() is tried. Counts once against the daily budget."""
    global _disabled
    if not available(cfg) or not _budget_ok():
        return None
    if research:  # the free tier allows only a few requests a minute: research calls are paced 12 seconds apart
        import time as _t
        wait = 12 - (_t.time() - _LAST_RESEARCH[0])
        if wait > 0:
            _t.sleep(wait)
        _LAST_RESEARCH[0] = _t.time()
    body = {"contents": [{"parts": parts or [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    for model in models_for(cfg, research):
        try:
            r = http().post(f"{API}/models/{model}:generateContent", json=body, timeout=timeout,
                            headers={"x-goog-api-key": _key(), "Content-Type": "application/json"})
            if r.status_code in (401, 403):
                _disabled = True  # bad key: stop trying this run
                log.error("Gemini refused the API key (HTTP %s)", r.status_code)
                return None
            if r.status_code == 404:
                _DEAD.add(model)
                log.warning("Gemini %s is not available to this key (HTTP 404); skipped from now on", model)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                log.warning("Gemini %s busy (HTTP %s); trying the next model", model, r.status_code)
                continue
            r.raise_for_status()
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"^```(json)?|```$", "", raw.strip()))
        except (TimeoutError, OSError) as e:
            log.warning("Gemini %s request failed (%s); trying the next model", model, type(e).__name__)
            continue
        except Exception as e:  # noqa: BLE001  bad JSON etc.: the next model will not fix the prompt
            log.warning("Gemini request failed: %s", e)
            return None
    log.warning("Gemini: every model busy; using the rule-based fallback for now")
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
        main = _pick_model(cfg)
        return f"ok {main}; backups {', '.join(_backups(cfg, main)) or 'none'}"
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
            tickers: list[str] | None = None, posted: list[str] | None = None) -> dict | None:
    """Gemini's reading of the article in today's market context, checked against the sources; or None.
    `posted` = recent main-channel posts, so the AI can tell a re-report of the same event."""
    global last_status
    last_status = "unavailable"
    if not available(cfg):
        return None
    text = article_text(url) if url else ""
    if len(text) < 300:
        text = (text + "\n" + (summary or "")).strip() or "(article text not available; use the headline only)"
    tick = tickers or []
    d = _call(cfg, ANALYSE.format(sectors=", ".join(SECTORS), tickers=", ".join(tick) or "(none listed)",
                                  context=context or "(not available)", title=title, source=source, text=text,
                                  posted="\n".join(posted or []) or "(nothing yet)"))
    if not d:
        last_status = "error" if available(cfg) else "unavailable"
        return None
    article = f"{title}\n{text}"
    both = f"{article}\n{context}"
    out_summary = _checked(d.get("summary"), article)
    if not out_summary:
        log.warning("AI summary failed the number check: %s", title[:70])
        last_status = "check_failed"
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
    try:
        materiality = max(1, min(5, int(d.get("materiality", 3))))
    except (TypeError, ValueError):
        materiality = 3
    same = d.get("same_event")
    same = str(same).strip() if same and str(same).strip().lower() not in ("null", "none", "false", "") else ""
    if same and posted and not any(same[:40].lower() in p.lower() or p.lower()[6:46] in same.lower() for p in posted):
        same = ""  # must point at a real earlier post
    last_status = "ok"
    if d.get("conflicts_official") is True:
        log.warning("AI: article contradicts official data, kept off main channel: %s", title[:80])
        stale = True
    return {"kind": kind, "stale": stale, "event_date": d.get("event_date"), "relevant": bool(d.get("relevant", True)),
            "summary": out_summary, "why": _checked(d.get("why"), both), "context": _checked(d.get("context"), both),
            "watch": _checked(d.get("watch"), both),
            "facts": [f for f in (_checked(x, article) for x in (d.get("key_facts") or [])[:3]) if f],
            "level": level, "tone": tone, "pos": pos, "neg": neg, "stocks": stocks, "sectors": [],
            "read_article": len(text) >= 300, "ai": True, "materiality": materiality,
            "confirmed": d.get("confirmed") is not False, "same_event": same}


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
