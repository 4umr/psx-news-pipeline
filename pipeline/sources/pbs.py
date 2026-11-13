"""Pakistan Bureau of Statistics: actual CPI and SPI numbers from the official releases.

- Monthly CPI: the release post links a "Monthly Review on Price Indices" .docx
  whose "Inflation in Brief" section states YoY / MoM for General, Urban, Rural.
- Weekly SPI: the release post itself states the index and weekly change.
"""
from __future__ import annotations

import calendar
import io
import re
import zipfile
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup

from ..common import clean_ws, http, log

FEED = "https://www.pbs.gov.pk/feed/"


def releases() -> list[dict]:
    try:
        r = http().get(FEED, timeout=25)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("PBS feed failed: %s", e)
        return []
    out = []
    for e in feedparser.parse(r.content).entries:
        t = e.get("published_parsed")
        out.append({"title": clean_ws(e.get("title", "")), "url": e.get("link", ""),
                    "published": datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc) if t else None})
    return out


def _docx_text(data: bytes) -> str:
    xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf8", "ignore")
    paras = []
    for p in re.findall(r"<w:p\b.*?</w:p>", xml, flags=re.S):
        t = "".join(re.findall(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>", p)).strip()
        if t:
            paras.append(t)
    return "\n".join(paras)


def _signed(word: str, val: str) -> float:
    v = float(val)
    return -v if word.lower().startswith("decrease") else v


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:6])


def tidy_text(text: str) -> str:
    """Repair the word breaks PDF extraction leaves ('month -on- month', 'a n increase', '1. 5%')."""
    t = clean_ws(text)
    t = re.sub(r"\s*-\s*", "-", t)
    t = re.sub(r"(\d)\.\s+(\d+\s?%)", r"\1.\2", t)
    t = re.sub(r"\b(in|de)\s+crease", r"\1crease", t, flags=re.I)
    t = re.sub(r"\ba n\b", "an", t)
    t = re.sub(r"\s+,", ",", t)
    return re.sub(r"(\d)\s+%", r"\1%", t)


def parse_cpi_text(text: str) -> dict:
    out: dict = {}
    text = tidy_text(text)
    for kind in ("General", "Urban", "Rural"):
        # the month-on-month figure must come from the SAME paragraph, never the next item's
        m = re.search(rf"CPI inflation {kind},?\s*(increased|decreased) by ([\d.]+)% on year-on-year basis in "
                      rf"(\w+ \d{{4}}) as compared to an? (increase|decrease) of ([\d.]+)% in the previous month"
                      rf"(?:(?!CPI inflation|SPI inflation).)*?On month-on-month basis, it (increased|decreased) "
                      rf"by ([\d.]+)%", text, re.S | re.I)
        if m:
            out[kind.lower()] = {"yoy": _signed(m.group(1), m.group(2)), "month": m.group(3),
                                 "prev_yoy": _signed(m.group(4), m.group(5)), "mom": _signed(m.group(6), m.group(7))}
    m = re.search(r"SPI inflation on YoY basis (increased|decreased) by ([\d.]+)%", text, re.I)
    if m:
        out["spi_yoy"] = _signed(m.group(1), m.group(2))
    m = re.search(r"WPI inflation on YoY basis (increased|decreased) by ([\d.]+)%", text, re.I)
    if m:
        out["wpi_yoy"] = _signed(m.group(1), m.group(2))
    return out


def cpi_details(post_url: str) -> dict | None:
    """Follow a 'Monthly Inflation Report' post to its review document and parse it."""
    try:
        soup = BeautifulSoup(http().get(post_url, timeout=30).content, "lxml")
        art = soup.find("article") or soup
        links = [a["href"] for a in art.find_all("a", href=True)]
        # the review used to be a Word file; from October 2026 PBS posts a PDF instead
        doc = (next((h for h in links if re.search(r"Monthly-Review.*\.docx$", h, re.I)), None)
               or next((h for h in links if re.search(r"Monthly-Review.*\.pdf$", h, re.I)), None))
        if not doc:
            log.warning("CPI post has no review document: %s", post_url)
            return None
        raw = http().get(doc, timeout=60).content
        data = parse_cpi_text(_docx_text(raw) if doc.lower().endswith(".docx") else _pdf_text(raw))
        g = data.get("general") or {}
        if g and not (abs(g["yoy"]) < 60 and abs(g["prev_yoy"]) < 60 and abs(g["mom"]) < 15):
            log.warning("CPI numbers out of range, ignored: %s", g)
            return None
        if g:
            data["doc"] = doc
            return data
    except Exception as e:  # noqa: BLE001
        log.warning("CPI parse failed for %s: %s", post_url, e)
    return None


def spi_details(post_url: str) -> dict | None:
    try:
        soup = BeautifulSoup(http().get(post_url, timeout=30).content, "lxml")
        text = clean_ws((soup.find("article") or soup).get_text(" "))
        m = re.search(r"week ended on ([\d-]+) is ([\d.]+) with (-?[\d.]+)% change", text)
        if m:
            return {"week": m.group(1), "index": float(m.group(2)), "wow": float(m.group(3))}
    except Exception as e:  # noqa: BLE001
        log.warning("SPI parse failed for %s: %s", post_url, e)
    return None
