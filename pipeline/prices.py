"""Price database: daily OHLC + volume for every listed stock.

Built from scstrade.com (history back to 2018) and kept up to date from the daily
activity table after each close. Prices are adjusted for dividends, bonus shares,
rights and splits using PSX's own reference price: on an ex-date PSX lowers the
reference price, so close - change differs from the previous close. The ratio
(reference / previous close) is the adjustment factor for that day.

Two files:
  state/prices.csv.gz   rolling ~15 months, used by the daily scanner (GitHub Actions)
  data/history.csv.gz   full history since 2018, used by the backtest (local, not committed)
"""
from __future__ import annotations

import gzip
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, log, now_pkt
from .sources import psx, scs

LIVE_PATH = ROOT / "state" / "prices.csv.gz"
HIST_PATH = ROOT / "data" / "history.csv.gz"
META_PATH = ROOT / "state" / "meta.json"
COLS = ["date", "sym", "o", "h", "l", "c", "v", "ref"]

# Not operating companies: no swing signals for these
EXCLUDE_SECTORS = {"ETFs", "CLOSE - END MUTUAL FUND"}
EXCLUDE_PREFIX = ("SPAC",)


def is_equity(sym: str, sector: str) -> bool:
    return sector.strip() not in EXCLUDE_SECTORS and not sym.startswith(EXCLUDE_PREFIX)


# ---------------------------------------------------------------- storage
def load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLS)
    try:
        df = pd.read_csv(path, parse_dates=["date"])
    except Exception as e:  # noqa: BLE001  damaged file: rebuild from source
        log.warning("price file %s unreadable (%s), rebuilding", path.name, e)
        return pd.DataFrame(columns=COLS)
    return df[COLS]


def save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.sort_values(["sym", "date"])
    buf = io.StringIO()
    df.to_csv(buf, index=False, date_format="%Y-%m-%d", float_format="%.4g")
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(buf.getvalue())
    tmp.replace(path)


# ---------------------------------------------------------------- fetching
def _rows(sym: str, start: datetime, end: datetime) -> list[tuple]:
    out = []
    for r in scs.price_history(sym, start, end):
        c, chg = r.get("trading_close"), r.get("trading_change")
        out.append((r["date"].strftime("%Y-%m-%d"), sym, r.get("trading_open"), r.get("trading_high"),
                    r.get("trading_low"), c, r.get("trading_vol") or 0,
                    round(c - chg, 4) if chg is not None else None))
    return out


def fetch(symbols: list[str], start: datetime, end: datetime, workers: int = 8) -> pd.DataFrame:
    rows: list[tuple] = []
    with ThreadPoolExecutor(workers) as ex:
        for i, part in enumerate(ex.map(lambda s: _rows(s, start, end), symbols), 1):
            rows.extend(part)
            if i % 100 == 0:
                log.info("price history: %d/%d symbols", i, len(symbols))
    df = pd.DataFrame(rows, columns=COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(["sym", "date"], keep="last")


def universe() -> tuple[list[str], dict[str, str]]:
    """Equity symbols traded in the latest session, with their sector."""
    act = scs.daily_activity()
    sectors = {r["company_code"].strip(): (r.get("sector_name") or "").strip() for r in act if r.get("company_code")}
    return sorted(s for s, sec in sectors.items() if is_equity(s, sec)), sectors


def index_history(start: datetime) -> pd.Series:
    days = (now_pkt() - start.replace(tzinfo=now_pkt().tzinfo)).days + 2
    rows = scs.index_history(days)
    return pd.Series({pd.Timestamp(r["date"].strftime("%Y-%m-%d")): r["kse_index_close"] for r in rows}).sort_index()


# ---------------------------------------------------------------- company facts
def meta(symbols: list[str], max_age_days: int = 30, workers: int = 8) -> dict:
    """Shares outstanding and free float per symbol (PSX company pages), refreshed monthly."""
    try:
        m = json.loads(META_PATH.read_text(encoding="utf-8")) if META_PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        m = {}
    cut = (now_pkt() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    todo = [s for s in symbols if m.get(s, {}).get("asof", "") < cut]
    if todo:
        def one(s):
            p = psx.company(s) or {}
            return s, {k: p[k] for k in ("shares", "free_float", "free_float_pct") if k in p}

        with ThreadPoolExecutor(workers) as ex:
            for s, info in ex.map(one, todo):
                if info:
                    m[s] = {**info, "asof": now_pkt().strftime("%Y-%m-%d")}
        META_PATH.parent.mkdir(parents=True, exist_ok=True)
        META_PATH.write_text(json.dumps(m, separators=(",", ":")), encoding="utf-8")
        log.info("company facts refreshed for %d symbols", len(todo))
    return m


# ---------------------------------------------------------------- adjustment
def adjust(df: pd.DataFrame) -> pd.DataFrame:
    """Add adjusted prices (ao/ah/al/ac), raw daily change vs reference (chg) and traded value (val)."""
    df = df.sort_values(["sym", "date"]).reset_index(drop=True)
    for c in ("o", "h", "l", "c", "v", "ref"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[(df["c"] > 0)].copy()
    df["o"] = df["o"].where(df["o"] > 0, df["c"])
    df["h"] = df[["h", "c", "o"]].max(axis=1)
    df["l"] = df[["l", "c", "o"]].where(df[["l", "c", "o"]] > 0).min(axis=1)
    prev = df.groupby("sym")["c"].shift(1)
    ratio = (df["ref"] / prev).where(df["ref"].notna() & prev.notna(), 1.0)
    # Market sessions = every date on which any stock traded. If a stock skipped sessions,
    # a lower reference price may just be a move on the missing days, so only consecutive
    # sessions count. Small differences are rounding; a real ex-date moves it by more than 0.3%.
    sess = {d: i for i, d in enumerate(sorted(df["date"].unique()))}
    df["si"] = df["date"].map(sess)
    consecutive = df.groupby("sym")["si"].diff() == 1
    f = ratio.where(consecutive & (ratio < 0.997) & (ratio > 0.02), 1.0)
    df["exf"] = f
    # backward cumulative product: each day's prices are multiplied by all LATER factors
    later = f.groupby(df["sym"]).transform(lambda s: s[::-1].cumprod()[::-1].shift(-1).fillna(1.0))
    for raw, adj in (("o", "ao"), ("h", "ah"), ("l", "al"), ("c", "ac")):
        df[adj] = df[raw] * later
    ref = df["ref"].where(df["ref"] > 0, prev)
    df["chg"] = df["c"] / ref - 1
    df["val"] = df["c"] * df["v"]
    # PSX daily price limit: a % band or Rs 1, whichever is higher. The band was 5% until
    # Jan 2020, 7.5% until Jun 2024 and 10% since (found from the data itself).
    band = np.select([df["date"] < "2020-01-20", df["date"] < "2024-06-10"], [0.05, 0.075], 0.10)
    df["limit"] = np.maximum(band, 1.0 / ref)
    return df


# ---------------------------------------------------------------- live database
def last_session(now: datetime) -> pd.Timestamp | None:
    rows = scs.index_history(10)
    return pd.Timestamp(rows[-1]["date"].strftime("%Y-%m-%d")) if rows else None


def update_live(now: datetime | None = None, keep_days: int = 460) -> pd.DataFrame:
    """Bring state/prices.csv.gz up to the latest completed session. Returns raw rows."""
    now = now or now_pkt()
    df = load(LIVE_PATH)
    syms, sectors = universe()
    sess = last_session(now)
    if sess is None or not syms:
        log.warning("price update: no session data from SCS")
        return df
    today = pd.Timestamp(now.strftime("%Y-%m-%d"))
    have = df["date"].max() if len(df) else None
    # Today's session only counts once the market has closed (daily activity is final ~16:45)
    complete = sess if (sess < today or now.hour * 60 + now.minute >= 17 * 60) else sess - pd.Timedelta(days=1)
    if have is None or have < complete - pd.Timedelta(days=12):
        start = (now - timedelta(days=keep_days)).replace(tzinfo=None)
        log.info("price database: full load since %s for %d symbols", start.date(), len(syms))
        df = fetch(syms, start, now.replace(tzinfo=None))
    elif have < complete:
        # Missed sessions (or just today): use the one-request daily table for today, history for gaps
        gap_start = have + pd.Timedelta(days=1)
        if sess == complete == today and gap_start >= sess:
            act = scs.daily_activity()
            new = pd.DataFrame([(sess, r["company_code"].strip(), r.get("trading_open"), r.get("trading_high"),
                                 r.get("trading_low"), r.get("trading_close"), r.get("trading_vol") or 0,
                                 round(r["trading_close"] - r["trading_change"], 4)
                                 if r.get("trading_change") is not None and r.get("trading_close") else None)
                                for r in act if r.get("company_code") and r.get("trading_close")], columns=COLS)
            new = new[new["sym"].isin(syms)]
        else:
            new = fetch(syms, gap_start.to_pydatetime(), complete.to_pydatetime())
        df = pd.concat([df, new]).drop_duplicates(["sym", "date"], keep="last")
        log.info("price database: added %d rows up to %s", len(new), complete.date())
    cut = pd.Timestamp((now - timedelta(days=keep_days)).strftime("%Y-%m-%d"))
    df = df[(df["date"] >= cut) & (df["date"] <= complete)]  # never keep a half-finished session
    save(df, LIVE_PATH)
    df.attrs["sectors"] = sectors
    return df


def build_history(start: str = "2018-01-01") -> pd.DataFrame:
    """Full history for the backtest (cached in data/history.csv.gz; refreshed if older than a day)."""
    if HIST_PATH.exists() and (datetime.now().timestamp() - HIST_PATH.stat().st_mtime) < 86400:
        return load(HIST_PATH)
    syms, _ = universe()
    log.info("downloading full price history for %d symbols since %s", len(syms), start)
    df = fetch(syms, datetime.fromisoformat(start), datetime.now())
    save(df, HIST_PATH)
    return df
