"""Who is buying which sector: weekly net flows of foreign investors and local mutual funds (NCCPL data via
scstrade, history back to 2019). A date range on the scstrade endpoint returns the total for the range,
so one request per week is enough."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd

from .common import ROOT, log, now_pkt
from .sources import scs

HIST_PATH = ROOT / "data" / "flows.csv.gz"
TYPES = {"FIPI": "foreign", "MUTUAL FUNDS": "funds"}


def sector_key(name: str) -> str:
    """'Oil and Gas Exploration Companies (mn$)' / 'OIL & GAS EXPLORATION COMPANIES' -> 'OIL & GAS EXPLORATION COMPANIES'."""
    n = re.sub(r"\(mn\$\)", "", name or "", flags=re.I)
    n = re.sub(r"\band\b", "&", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip().upper()


def week(start: datetime, end: datetime) -> list[dict]:
    rows = scs._post("FIPILIPI.aspx/loadfipisector", {"date1": start.strftime("%m/%d/%Y"), "date2": end.strftime("%m/%d/%Y")})
    out = []
    for r in rows:
        t = TYPES.get((r.get("FLTypeNew") or "").strip().upper())
        sec = sector_key(r.get("FLSectorName") or "")
        if not t or not sec or "ALL OTHER" in sec or "DEBT" in sec or r.get("FLNetValueUSD") is None:
            continue
        out.append({"week": end.strftime("%Y-%m-%d"), "who": t, "sector": sec, "net": r["FLNetValueUSD"],
                    "gross": abs(r.get("FLBuyValue") or 0) + abs(r.get("FLSellValue") or 0)})
    return out


def history(start: str = "2019-01-01", refresh: bool = False) -> pd.DataFrame:
    """Weekly flows (Monday-Friday windows), cached; only missing weeks are downloaded."""
    old = pd.read_csv(HIST_PATH) if HIST_PATH.exists() and not refresh else pd.DataFrame(columns=["week"])
    have = set(old["week"].astype(str))
    d = datetime.fromisoformat(start)
    d -= timedelta(days=d.weekday())
    today = now_pkt().replace(tzinfo=None)
    todo = []
    while d + timedelta(days=4) < today - timedelta(days=1):
        fri = d + timedelta(days=4)
        if fri.strftime("%Y-%m-%d") not in have:
            todo.append((d, fri))
        d += timedelta(days=7)
    if todo:
        log.info("sector flows: downloading %d weeks", len(todo))
        with ThreadPoolExecutor(6) as ex:
            new = [r for part in ex.map(lambda w: week(*w), todo) for r in part]
        old = pd.concat([old, pd.DataFrame(new)], ignore_index=True)
        HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        old.to_csv(HIST_PATH, index=False, compression="gzip")
    old["week"] = pd.to_datetime(old["week"])
    return old


def intensity(fl: pd.DataFrame, who: str, weeks: int = 4) -> pd.DataFrame:
    """Per week and sector: net / gross over the last `weeks` weeks (-1 = all selling, +1 = all buying)."""
    f = fl[fl["who"] == who].pivot_table(index="week", columns="sector", values=["net", "gross"], aggfunc="sum").fillna(0)
    net, gross = f["net"].rolling(weeks).sum(), f["gross"].rolling(weeks).sum()
    return (net / gross.where(gross > 0)).dropna(how="all")
