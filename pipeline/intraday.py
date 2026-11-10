"""Intraday price snapshots, recorded by the fast loop about once a minute during the session.

Nobody offers free intraday PSX history, so the pipeline builds its own: every pass in market hours
writes the last price, cumulative volume, high and low of every stock whose price or volume changed
since the previous pass (plus the KSE-100 level). One file per trading day in state/intraday/,
gzipped once the session is over, kept 10 days in the Actions cache and archived for good as an
asset of the GitHub release "intraday" (public data, about 0.5 MB a day).

Uses later: early unusual volume (first 30 minutes vs usual), opening-gap behaviour, who is bought
before results, live "early movers" cards. Rules on this data get the same backtest treatment.
"""
from __future__ import annotations

import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .common import ROOT, in_window, log

DIR = ROOT / "state" / "intraday"
KEEP_DAYS = 10
HEADER = "time,sym,price,vol,high,low\n"
_last: dict[str, tuple] = {}   # sym -> (price, volume) last written, so unchanged rows are skipped
_loaded_day = ""


def session_open(now: datetime) -> bool:
    """Weekday, from the pre-open to a little after the Friday close (holidays just record nothing new)."""
    return now.weekday() < 5 and in_window(now, "09:15", "16:45")


def _path(day: str) -> Path:
    return DIR / f"{day}.csv"


def _load_last(day: str) -> None:
    """After a restart (new loop chunk), rebuild the 'last written' table from today's file."""
    global _loaded_day
    _last.clear()
    p = _path(day)
    if p.exists():
        with p.open(encoding="utf-8") as f:
            next(f, None)
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) >= 4:
                    _last[parts[1]] = (parts[2], parts[3])
    _loaded_day = day


def record(now: datetime, rows: list[dict], view: list[dict] | None = None) -> int:
    """Append one snapshot of the daily-activity table (changed rows only). Returns rows written."""
    day = now.strftime("%Y-%m-%d")
    if _loaded_day != day:
        _load_last(day)
    ts = now.strftime("%H:%M:%S")
    lines = []
    for r in rows:
        sym = (r.get("company_code") or "").strip()
        c, v = r.get("trading_close"), r.get("trading_vol")
        if not sym or not c or v is None:
            continue
        key = (f"{c:g}", f"{int(v)}")
        if _last.get(sym) == key:
            continue
        _last[sym] = key
        h, lo = r.get("trading_high") or "", r.get("trading_low") or ""
        lines.append(f"{ts},{sym},{key[0]},{key[1]},{h:g},{lo:g}\n" if h != "" and lo != ""
                     else f"{ts},{sym},{key[0]},{key[1]},,\n")
    if view and view[0].get("CurrentIndex"):
        key = (f"{view[0]['CurrentIndex']:g}", "0")
        if _last.get("KSE100") != key:
            _last["KSE100"] = key
            lines.append(f"{ts},KSE100,{key[0]},0,,\n")
    if not lines:
        return 0
    DIR.mkdir(parents=True, exist_ok=True)
    p = _path(day)
    new = not p.exists()
    with p.open("a", encoding="utf-8") as f:
        if new:
            f.write(HEADER)
        f.writelines(lines)
    return len(lines)


def housekeeping(now: datetime) -> None:
    """Gzip finished days (yesterday and earlier, or today after 17:00) and drop files older than KEEP_DAYS."""
    if not DIR.exists():
        return
    today = now.strftime("%Y-%m-%d")
    done_today = now.hour >= 17
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for p in sorted(DIR.glob("*.csv")):
        day = p.stem
        if day < today or (day == today and done_today):
            gz = p.with_suffix(".csv.gz")
            with p.open("rb") as src, gzip.open(gz, "wb") as dst:
                shutil.copyfileobj(src, dst)
            p.unlink()
            log.info("intraday: %s archived (%d KB)", day, gz.stat().st_size // 1024)
    for p in DIR.glob("*.csv.gz"):
        if p.name[:10] < cut:
            p.unlink()
