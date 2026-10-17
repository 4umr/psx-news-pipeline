"""Annual and quarterly reports filed with PSX (financials.psx.com.pk, back to ~2013)."""
from __future__ import annotations

import io
import re

from ..common import http, log

BASE = "https://financials.psx.com.pk/"


def listing(symbol: str) -> list[dict]:
    """[{kind: 'annual'|'quarterly', period, posted, id}] oldest first."""
    try:
        r = http().post(BASE + "annQtrStmts.php", data={"name": "get_comp_data", "smbCode": symbol}, timeout=40,
                        headers={"X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        rows = r.json() or []
    except Exception as e:  # noqa: BLE001
        log.warning("report listing %s failed: %s", symbol, e)
        return []
    out = []
    for row in rows:
        m = re.search(r"id=([\w-]+)", row.get("Reports", ""))
        if m:
            out.append({"kind": "annual" if "Annual" in row["Reports"] else "quarterly",
                        "period": str(row.get("period_ended", "")), "posted": str(row.get("posting_date", "")),
                        "id": m.group(1)})
    return sorted(out, key=lambda x: (x["posted"], x["period"]))


def latest(symbol: str, kind: str = "annual") -> dict | None:
    rows = [r for r in listing(symbol) if r["kind"] == kind]
    return rows[-1] if rows else None


def pages(report_id: str, max_pages: int = 600) -> list[str]:
    """Text of each page ([] on failure). Annual reports are 100-500 pages, 5-30 MB."""
    import pypdf
    try:
        r = http().get(BASE + "lib/DownloadPDF.php", params={"id": report_id}, timeout=300)
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            log.warning("report %s is not a PDF", report_id)
            return []
        rd = pypdf.PdfReader(io.BytesIO(r.content))
        return [(rd.pages[i].extract_text() or "") for i in range(min(len(rd.pages), max_pages))]
    except Exception as e:  # noqa: BLE001
        log.warning("report %s could not be read: %s", report_id, e)
        return []
