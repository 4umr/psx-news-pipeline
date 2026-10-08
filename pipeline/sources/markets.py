"""Global market snapshot via Yahoo Finance (free, no key)."""
from __future__ import annotations

import logging

from ..common import log

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def snapshot(symbols: list[str]) -> dict:
    """symbol -> {last, prev, pct, date, hist}. hist = ~1 month of daily closes (for sparklines)."""
    symbols = list(dict.fromkeys(symbols))
    try:
        import yfinance as yf
        df = yf.download(symbols, period="1mo", interval="1d", progress=False,
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
                    "date": s.index[-1].strftime("%Y-%m-%d"), "hist": [round(float(v), 4) for v in s.tolist()]}
    log.info("markets: %d/%d symbols", len(out), len(symbols))
    return out
