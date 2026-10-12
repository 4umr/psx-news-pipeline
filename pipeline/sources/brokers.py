"""Broker research notes, as listed publicly on KSE Alert (ksealert.com/Research).

KSE Alert lists the daily notes of Pakistani brokerages (AHL, AKD, JS Global, BMA, Darson,
Taurus, Rafi, ...) with the broker's name, date, headline and a public PDF. We only use the
broker name, headline and link, always credited to the broker, never presented as our own.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..common import http, log

URL = "https://ksealert.com/Research"


def reports() -> list[dict]:
    """[{date, broker, title, url, kind}] newest first ([] if the page can't be read)."""
    from bs4 import BeautifulSoup
    try:
        r = http().get(URL, timeout=25)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("broker research list failed: %s", e)
        return []
    out = []
    for row in BeautifulSoup(r.content, "lxml").select("article.krch-row"):
        t = row.find("time")
        a = row.select_one("a.krch-title-link")
        en = row.select_one('span[data-content-lang="en"]')
        name = row.select_one(".krch-broker-name")
        if not (t and a and en and name):
            continue
        try:
            when = datetime.strptime(t.get("datetime", ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        out.append({"date": when, "broker": re.sub(r"\s+", " ", name.get_text()).strip(),
                    "title": re.sub(r"\s+", " ", en.get_text()).strip(), "url": a.get("href", ""),
                    "kind": row.get("data-report-type", "")})
    log.info("broker research: %d notes listed", len(out))
    return out
