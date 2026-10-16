"""Analyst EPS expectations from results previews in the news, e.g.
'EFERT is expected to post EPS of PKR6.58 in 3QCY26, up 51% YoY' (Trade Chronicle, brokers' previews).

Previews only exist for some companies, so a result is compared with expectations only when one was
captured; otherwise nothing is shown. Kept in state for ~6 months (also builds a dataset for later testing).
"""
from __future__ import annotations

import re
import time

from .common import log
from .earnings import norm_label

_PAT = re.compile(
    r"\b(?P<sym>[A-Z][A-Z0-9]{1,7})\b[^.]{0,40}?\b(?:is|are)?\s*(?:expected|likely|set|projected|anticipated)\s+to\s+"
    r"(?:post|report|announce|record)\s+(?:an?\s+)?(?:EPS|earnings per share)\s+of\s+(?:PKR|Rs\.?)\s?(?P<eps>-?[\d,]+(?:\.\d+)?)"
    r"(?:\s*(?:/share|per share))?\s+(?:in|for)\s+(?P<per>[1-4]Q(?:FY|CY)?\d{2}|(?:FY|CY)\d{2})", re.I)


def parse(title: str) -> dict | None:
    m = _PAT.search(title or "")
    if not m:
        return None
    per = m.group("per").upper()
    q = re.match(r"([1-4])Q(?:FY|CY)?(\d{2})$", per)
    if q:
        # brokers write 1QFY27 / 3QCY26; results are filed as 1Q27 / 3Q26, and the 4th quarter with the annual (FY/CY)
        label = f"{q.group(1)}Q{q.group(2)}" if q.group(1) != "4" else f"FY{q.group(2)}"
        quarter = True
    else:
        label, quarter = per, False
    return {"sym": m.group("sym").upper(), "label": norm_label(label)[0], "eps": float(m.group("eps").replace(",", "")),
            "quarter": quarter, "q4": bool(q and q.group(1) == "4")}


def capture(items, state) -> int:
    """Store expectations found in news titles (and summaries). Returns how many were new."""
    store = state.data.setdefault("expect", {})
    n = 0
    for it in items:
        e = parse(it.title) or parse(getattr(it, "summary", "")[:400])
        if not e:
            continue
        key = f"{e['sym']}:{e['label']}"
        if key not in store:
            store[key] = {**e, "src": it.source, "url": it.url, "ts": int(time.time())}
            n += 1
            log.info("expectation captured: %s %s EPS %.2f (%s)", e["sym"], e["label"], e["eps"], it.source)
    cut = time.time() - 180 * 86400
    state.data["expect"] = {k: v for k, v in store.items() if v.get("ts", 0) >= cut}
    return n


def compare(state, sym: str, label: str, a: dict | None) -> str:
    """'vs expected Rs 6.58: beat by 12%' when an expectation was captured for this result, else ''."""
    e = (state.data.get("expect") or {}).get(f"{sym}:{norm_label(label)[0]}")
    if not e or not a:
        return ""
    actual = a.get("eps_q") if e["quarter"] else a.get("eps")
    if actual is None or actual != actual or not e["eps"]:
        return ""
    diff = actual / e["eps"] - 1 if e["eps"] > 0 else None
    if diff is None:
        return f"expected EPS {e['eps']:.2f} ({e['src']})"
    word = "beat" if diff > 0.05 else ("missed" if diff < -0.05 else "in line with")
    return (f"{word} expected EPS {e['eps']:.2f} ({e['src']})" + (f" by {abs(diff) * 100:.0f}%" if word != "in line with" else ""))
