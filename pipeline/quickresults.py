"""Fast results: read a company's results straight from its PSX filing (image or PDF) the moment it appears.

PSX publishes the results notice as an image (e.g. FFC 3Q26 was a .gif) or a PDF, minutes before data sites list
the numbers. The AI reads the notice (period, quarter EPS, cumulative EPS, the same figures a year earlier, profit,
payout); the reading is then checked:
  - the year-earlier EPS it read must match the company's own results history (scstrade) within 3%, when that
    history has the period (a misread notice fails here);
  - EPS must be a plain number, profit and EPS must have the same sign.
A reading that fails a check is not sent. It is sent PRIVATELY (bot chat), labelled "read by AI from the PSX
filing", with the tested rule: results with EPS +50% or back to profit AND a close 3%+ higher on the day beat similar
mid and small caps by about 5.7% over 3 months (2012-2026); the earlier the alert, the more of the day is left.
"""
from __future__ import annotations

import base64
import io
import re

from .common import esc, http, log

PROMPT = """This image or document is a results notice filed by {sym} with the Pakistan Stock Exchange.
Read it carefully and return JSON with exactly these keys (numbers as plain numbers, null if not shown):
"period": the period the results are for, as printed (e.g. "quarter ended 30 September 2026" or "year ended 30 June 2026"),
"period_kind": one of "1Q", "HY", "3Q", "FY",
"year_end_year": the 4-digit year in which that period ends,
"eps_period": earnings per share for the period being reported (basic, rupees; for HY / 3Q / FY this is the
  cumulative figure shown for the whole period),
"eps_period_last_year": the same figure for the corresponding period of the previous year,
"eps_quarter": EPS for the latest three months only if shown separately, else null,
"eps_quarter_last_year": the same for the previous year if shown, else null,
"profit_after_tax": profit after tax for the period in the units printed, "profit_units": e.g. "Rs in thousands",
"consolidated": true if these are consolidated figures, false if standalone,
"dividend": cash dividend announced, as printed (e.g. "Rs 7.50 per share" or "75%"), or null,
"bonus": bonus shares announced as printed, or null.
Do not guess: if a figure is not clearly printed, use null."""


def _fetch(url: str) -> tuple[str, bytes] | None:
    try:
        r = http().get(url, timeout=40)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.info("results filing fetch failed: %s", e)
        return None
    data, ctype = r.content, (r.headers.get("Content-Type") or "").lower()
    if data[:4] == b"%PDF" or "pdf" in ctype:
        return "application/pdf", data
    try:  # GIF and other images -> PNG (the AI reads PNG / JPEG / WEBP)
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.seek(0)
        out = io.BytesIO()
        im.convert("RGB").save(out, format="PNG")
        return "image/png", out.getvalue()
    except Exception as e:  # noqa: BLE001
        log.info("results filing is not a readable image: %s", e)
        return None


def _num(x):
    try:
        return None if x is None else float(str(x).replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return None


def check(rd: dict, history_ly: float | None) -> list[str]:
    """Problems with an AI reading (empty = fine)."""
    probs = []
    e, ely = _num(rd.get("eps_period")), _num(rd.get("eps_period_last_year"))
    if e is None:
        probs.append("no EPS read")
    if rd.get("period_kind") not in ("1Q", "HY", "3Q", "FY"):
        probs.append("period not recognised")
    if e is not None and abs(e) > 2000:
        probs.append("EPS out of range")
    pat = _num(rd.get("profit_after_tax"))
    if e is not None and pat is not None and e != 0 and pat != 0 and (e > 0) != (pat > 0):
        probs.append("EPS and profit have different signs")
    if history_ly is not None and ely is not None and abs(ely - history_ly) > max(0.03 * abs(history_ly), 0.02):
        probs.append(f"last year's EPS read as {ely} but the results history says {history_ly}")
    return probs


def label_of(rd: dict) -> str | None:
    """'3Q' + year '2026' (period ends in) -> scstrade-style label for history lookup, with the company's fiscal
    year: we look up both possibilities (calendar year and June year) and use whichever exists."""
    k, y = rd.get("period_kind"), str(rd.get("year_end_year") or "")
    if not k or not re.fullmatch(r"\d{4}", y):
        return None
    return f"{k}{y[2:]}"


def read(sym: str, url: str, cfg: dict) -> dict | None:
    """AI reading of the filing + checks + the comparison with last year. None when it cannot be trusted."""
    from . import analysis, earnings
    got = _fetch(url)
    if not got:
        return None
    mime, data = got
    rd = analysis.call_parts(cfg, [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
                                   {"text": PROMPT.format(sym=sym)}], timeout=90)
    if not rd:
        return None
    # history: the same period a year earlier (labels like 3Q26; June-year companies label by the year the FY ends)
    hist_ly, lab = None, label_of(rd)
    try:
        h = earnings.company(sym)
        if h is not None and lab:
            kind = "FY" if rd["period_kind"] == "FY" else rd["period_kind"]
            yy = int(lab[-2:])
            prev = h[(h["kind"].isin([kind, "CY"] if kind == "FY" else [kind])) & (h["yy"] == yy - 1)]
            if len(prev):
                col = "eps_cum" if prev.iloc[-1].get("eps_cum") == prev.iloc[-1].get("eps_cum") else "eps_q"
                hist_ly = float(prev.iloc[-1][col])
    except Exception as e:  # noqa: BLE001
        log.info("results history for %s unavailable: %s", sym, e)
    probs = check(rd, hist_ly)
    if probs:
        log.warning("fast results %s not sent: %s", sym, "; ".join(probs))
        return None
    e, ely = _num(rd["eps_period"]), _num(rd.get("eps_period_last_year"))
    q, qly = _num(rd.get("eps_quarter")), _num(rd.get("eps_quarter_last_year"))
    base_now, base_ly = (q, qly) if q is not None and qly is not None else (e, ely)
    cls = None
    if base_now is not None and base_ly is not None:
        if base_now > 0 > base_ly:
            cls = "turnaround"
        elif base_now < 0 < base_ly:
            cls = "to_loss"
        elif base_ly > 0:
            g = base_now / base_ly - 1
            cls = "big_up" if g >= 0.5 else "up" if g >= 0.15 else "big_down" if g <= -0.5 else "down" if g <= -0.15 else "flat"
    return {"sym": sym, "url": url, "read": rd, "eps": e, "eps_ly": ely, "q": q, "q_ly": qly, "cls": cls,
            "good": cls in earnings.GOOD, "history_checked": hist_ly is not None}


def message(r: dict, move_pct: float | None) -> str:
    rd = r["read"]
    f = lambda x: "n/a" if x is None else f"{x:,.2f}"  # noqa: E731
    lines = [f"<b>Results just filed: {esc(r['sym'])}</b> (read by AI from the PSX filing, before data sites)",
             f"{esc(str(rd.get('period') or ''))}" + (" · consolidated" if rd.get("consolidated") else ""),
             f"EPS {f(r['eps'])} vs {f(r['eps_ly'])} a year earlier"
             + (f" ({(r['eps'] / r['eps_ly'] - 1) * 100:+.0f}%)" if r["eps"] is not None and r["eps_ly"] and r["eps_ly"] > 0 else "")]
    if r["q"] is not None and r["q_ly"] is not None:
        lines.append(f"Latest quarter EPS {f(r['q'])} vs {f(r['q_ly'])}")
    pay = "; ".join(x for x in (f"dividend {rd['dividend']}" if rd.get("dividend") else "", f"bonus {rd['bonus']}" if rd.get("bonus") else "") if x)
    if pay:
        lines.append(esc(pay))
    if move_pct is not None:
        lines.append(f"Stock {move_pct:+.1f}% so far today")
    if r["good"]:
        lines.append("<b>Strong result</b> (EPS +50% or back to profit). Tested: when the stock also closes 3%+ higher on "
                     "the day, mid and small caps beat similar stocks by about 5.7% over 3 months (2012-2026); about half "
                     "of those days end locked at the upper limit.")
    elif r["cls"] in ("big_down", "to_loss"):
        lines.append("<b>Weak result</b> (EPS down 50%+ or into a loss). Heavy buying after weak results has been a "
                     "warning on PSX (-3.9% vs similar stocks over 3 months).")
    lines.append("<i>" + ("Last year's figure matched the results history. " if r["history_checked"] else "")
                 + "Check the filing before acting: " + esc(r["url"]) + "</i>")
    return "\n".join(lines)
