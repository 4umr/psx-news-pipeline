"""Reads a company's latest annual report (PDF filed with PSX) for the Stock of the Day report.

Only the most relevant pages are sent (company profile, chairman's and directors' reports, plants and
capacity, subsidiaries, segments, outlook, and the balance sheet), about 40 pages instead of 300-500.
The reply is checked before use:
- every number (including years) in a sentence must appear in the report pages that were sent, otherwise
  that sentence is dropped;
- advice-like sentences are dropped; wording is plain;
- every item keeps the page it came from.
The result is cached per report, so each annual report is read once.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import analysis
from .common import ROOT, log
from .sources import reports

CACHE = ROOT / "state" / "sotd" / "reports"
BUDGET = 110_000   # characters of report text sent (about 30k tokens)

_WORDS = {
    3: ["company profile", "corporate profile", "our business", "principal activit", "nature of business",
        "directors' report", "directors report", "chairman's review", "chairman review", "ceo's message",
        "outlook", "future", "expansion", "project", "strategy", "milestone", "history", "incorporated in",
        "subsidiar", "joint venture", "associate", "segment", "capacity", "plant", "factory", "mill", "refinery",
        "branches", "network", "export", "market share", "brands", "products", "geographic", "operations"],
    1: ["risk", "opportunit", "challenge", "demand", "sales volume", "production", "dispatch", "customers",
        "province", "karachi", "lahore", "punjab", "sindh", "khyber", "balochistan", "islamabad", "mw ", "tons",
        "tonnes", "barrels", "mmcfd", "employees"],
}


def _score(text: str) -> float:
    low = text.lower()
    letters = sum(c.isascii() and c.isalpha() for c in text)
    if len(text) < 300 or letters < 0.45 * len(text):
        return -1  # empty, image or Urdu page
    digits = sum(c.isdigit() for c in text) / max(1, len(text))
    s = sum(w * min(low.count(k), 3) for w, ks in _WORDS.items() for k in ks)
    if digits > 0.18:
        s -= 8  # financial statement tables
    return s


def select(pages: list[str]) -> list[tuple[int, str]]:
    scored = sorted(((i, _score(t)) for i, t in enumerate(pages)), key=lambda x: -x[1])
    keep, size = [], 0
    for i, sc in scored:
        if sc <= 0:
            break
        if size + len(pages[i]) > BUDGET:
            continue
        keep.append(i)
        size += len(pages[i])
    # the balance sheet (first two 'statement of financial position' pages) for debt, cash and equity
    bs = [i for i, t in enumerate(pages) if "statement of financial position" in t.lower()
          and ("borrowing" in t.lower() or "financing" in t.lower() or "deposits" in t.lower())][:2]
    for i in bs:
        if i not in keep:
            keep.append(i)
    return [(i, pages[i]) for i in sorted(keep)]


PROMPT = """You are preparing the facts for a plain, neutral company profile for Pakistani retail investors.
Use ONLY the annual report pages below. Do not use outside knowledge. Do not give opinions or advice.
Write short, plain sentences (no marketing words, no adjectives like robust or remarkable).
Every item must give the page number it came from ("p"). If something is not in the pages, leave it out.
Copy numbers exactly as written in the pages (with their units).

Company: {name} ({sym}). Report: annual report for {period}.

Return JSON:
{{
 "what_they_do": {{"text": "2 or 3 sentences: what the company does and for whom", "p": 0}},
 "segments": [{{"name": "business line / product group", "detail": "one sentence", "p": 0}}],
 "locations": [{{"place": "city / district / country", "what": "plant, office, branches, field, export market", "p": 0}}],
 "capacity": [{{"text": "installed capacity or size figures, with units", "p": 0}}],
 "group": {{"parent": {{"text": "holding company / sponsor group if stated", "p": 0}},
           "subsidiaries": [{{"name": "", "what": "", "stake": "% if stated", "p": 0}}]}},
 "milestones": [{{"year": "", "event": "", "p": 0}}],
 "this_year": [{{"text": "the main facts about the year reported", "p": 0}}],
 "future": [{{"text": "plans, projects, expansions, targets and their timelines as stated by the company", "p": 0}}],
 "risks": [{{"text": "risks or challenges the company itself names", "p": 0}}],
 "balance_sheet": {{"borrowings": "total borrowings / financing as written, with unit", "cash": "cash and bank as written",
                   "equity": "total equity as written", "basis": "standalone or consolidated", "p": 0}}
}}
At most: 6 segments, 8 locations, 4 capacity, 8 subsidiaries, 6 milestones, 5 this_year, 6 future, 5 risks.

Pages:
{pages}
"""


def _years_ok(text: str, source: str) -> bool:
    return all(y in source for y in re.findall(r"\b(?:19|20)\d{2}\b", text))


def _ok(text: str, source: str) -> bool:
    return bool(text) and analysis.numbers_ok(text, source) and _years_ok(text, source)


def _clean_item(d: dict, fields: list[str], source: str) -> dict | None:
    out = {}
    for f in fields:
        v = analysis._clean(d.get(f, "")) if isinstance(d.get(f), str) else ""
        if v and not _ok(v, source):
            return None
        out[f] = v
    if not any(out.values()):
        return None
    try:
        out["p"] = int(d.get("p") or 0)
    except (TypeError, ValueError):
        out["p"] = 0
    return out


def _clean_list(items, fields, source, limit) -> list[dict]:
    out = []
    for d in items or []:
        if isinstance(d, dict) and (c := _clean_item(d, fields, source)):
            out.append(c)
        if len(out) >= limit:
            break
    return out


def validate(raw: dict, source: str) -> dict:
    """Keep only what passes the checks; report how much was dropped."""
    out = {}
    w = raw.get("what_they_do") or {}
    out["what_they_do"] = _clean_item(w, ["text"], source) if isinstance(w, dict) else None
    out["segments"] = _clean_list(raw.get("segments"), ["name", "detail"], source, 6)
    out["locations"] = _clean_list(raw.get("locations"), ["place", "what"], source, 8)
    out["capacity"] = _clean_list(raw.get("capacity"), ["text"], source, 4)
    g = raw.get("group") or {}
    out["parent"] = _clean_item(g.get("parent") or {}, ["text"], source) if isinstance(g, dict) else None
    out["subsidiaries"] = _clean_list(g.get("subsidiaries") if isinstance(g, dict) else [], ["name", "what", "stake"], source, 8)
    out["milestones"] = _clean_list(raw.get("milestones"), ["year", "event"], source, 6)
    out["this_year"] = _clean_list(raw.get("this_year"), ["text"], source, 5)
    out["future"] = _clean_list(raw.get("future"), ["text"], source, 6)
    out["risks"] = _clean_list(raw.get("risks"), ["text"], source, 5)
    b = raw.get("balance_sheet") or {}
    out["balance_sheet"] = _clean_item(b, ["borrowings", "cash", "equity", "basis"], source) if isinstance(b, dict) else None
    return out


def read(sym: str, name: str, cfg: dict, force: bool = False) -> dict | None:
    """Facts from the latest annual report, checked. None if no report or reading failed."""
    rep = reports.latest(sym, "annual")
    if not rep:
        log.warning("no annual report listed for %s", sym)
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    path: Path = CACHE / f"{sym}-{rep['id']}.json"
    if path.exists() and not force:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    pages = reports.pages(rep["id"])
    if not pages:
        return None
    chosen = select(pages)
    source = "\n".join(t for _, t in chosen)
    text = "\n\n".join(f"[Page {i + 1}]\n{t}" for i, t in chosen)
    if not analysis.available(cfg):
        log.warning("annual report for %s not read: Gemini key not available", sym)
        return None
    raw = analysis._call(cfg, PROMPT.format(name=name, sym=sym, period=rep["period"], pages=text), timeout=240)
    if not raw:
        return None
    out = validate(raw, source)
    out["report"] = {**rep, "pages": len(pages), "read_pages": [i + 1 for i, _ in chosen],
                     "url": f"{reports.BASE}lib/DownloadPDF.php?id={rep['id']}"}
    kept = sum(len(v) if isinstance(v, list) else (1 if v else 0) for k, v in out.items() if k != "report")
    sent = sum(len(v) if isinstance(v, list) else (1 if v else 0) for k, v in raw.items())
    out["checks"] = {"kept": kept, "returned": sent}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("annual report %s read: %d of about %d items passed the checks", sym, kept, sent)
    return out
