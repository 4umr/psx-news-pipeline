"""Global market snapshot via Yahoo Finance (free, no key)."""
from __future__ import annotations

import logging

from ..common import log

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def snapshot(symbols: list[str]) -> dict:
    """symbol -> {last, prev, pct, date}. Missing symbols are skipped."""
    try:
        import yfinance as yf
        df = yf.download(symbols, period="7d", interval="1d", progress=False,
                         auto_adjust=False, threads=True)["Close"]
    except Exception as e:  # noqa: BLE001
        log.warning("yfinance failed: %s", e)
        return {}
    out = {}
    for sym in symbols:
        if sym not in df:
            continue
        s = df[sym].dropna()
        if len(s) < 2:
            continue
        last, prev = float(s.iloc[-1]), float(s.iloc[-2])
        out[sym] = {"last": last, "prev": prev, "pct": (last / prev - 1) * 100 if prev else None,
                    "date": s.index[-1].strftime("%Y-%m-%d")}
    log.info("markets: %d/%d symbols", len(out), len(symbols))
    return out
