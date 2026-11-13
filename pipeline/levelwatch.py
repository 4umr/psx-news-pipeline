"""Level alerts during the session (private, bot chat only): the stocks of last night's Research Lab report,
watched against their levels with live prices.

Events (each once per stock per report, so a price hovering at a level does not repeat itself):
  buy side   in_zone   price inside the buy zone (support to support + 3%), above the fails-below line
             failed    price below the fails-below line (the chart setup is broken)
             target1   price at or above the first resistance; target2 the second
  sell side  sell_zone price inside the sell zone (resistance - 3% to resistance)
             broke     price below support
Live prices: KSE-100 stocks from scstrade's index view (live). Every other stock from the PSX screener, used only
when it agrees with the live index view at that moment (checked each time); scstrade's all-stock table is
yesterday's until about 16:15 and is never used here.
Levels come from chart support and resistance and are not tested; the setup label comes from tested signals.
"""
from __future__ import annotations

import json
import time

from .common import ROOT, esc, in_window, log
from .sources.psx import screener_live  # noqa: F401  (used by the self-test)

LAB_PATH = ROOT / "state" / "lab" / "calls.json"


def _report() -> dict:
    try:
        return (json.loads(LAB_PATH.read_text(encoding="utf-8")) or {}).get("report") or {}
    except (OSError, ValueError):
        return {}



def live_prices(view: list[dict], need: set[str], max_age: float = 180.0) -> tuple[dict[str, float], str]:
    """{sym: live price} for the stocks in `need`, and a note on where the prices came from."""
    px = {r["company_code"]: r["CurrentPrice"] for r in view or [] if r.get("company_code") and r.get("CurrentPrice")}
    rest = need - set(px)
    if not rest:
        return px, "live KSE-100 prices"
    from .sources import psx
    if scr := psx.live_screener(view, max_age):
        px.update({s: scr[s][0] for s in rest if s in scr})
        return px, "live prices (KSE-100 view and PSX screener)"
    return px, "live KSE-100 prices only (PSX screener not live right now)"


def events(it: dict, price: float) -> list[tuple[str, str]]:
    """(event key, plain sentence) for one stock at this price."""
    out = []
    f2 = lambda x: f"{x:,.2f}"  # noqa: E731
    if it.get("side") == "sell":
        sz, ex = it.get("sell_zone"), it.get("exit")
        if sz and sz[0] <= price <= sz[1] * 1.01:
            out.append(("sell_zone", f"reached its sell zone ({f2(sz[0])} to {f2(sz[1])}); support breaks below "
                                     + (f2(ex) if ex else "n/a")))
        if ex and price < ex:
            out.append(("broke", f"broke below support {f2(ex)}: the weak setup is playing out"))
        return out
    z, risk, r1, r2 = it.get("zone"), it.get("risk"), it.get("r1"), it.get("r2")
    if risk and price < risk:
        out.append(("failed", f"fell below {f2(risk)}, the line where the setup fails"))
    elif z and z[0] <= price <= z[1]:
        out.append(("in_zone", f"is inside its buy zone ({f2(z[0])} to {f2(z[1])}); fails below "
                               + (f2(risk) if risk else "n/a") + (f", target {f2(r1)}" if r1 else "")))
    if r2 and price >= r2:
        out.append(("target2", f"reached target 2 ({f2(r2)})"))
    elif r1 and price >= r1:
        out.append(("target1", f"reached target 1 ({f2(r1)})" + (f"; next {f2(r2)}" if r2 else "")))
    return out


def check(now, state, cfg: dict, view: list[dict]) -> str | None:
    """One pass: returns the alert message (or None). Weekdays 09:32-16:35, only on days the market trades."""
    conf = cfg.get("level_alerts") or {}
    if not conf.get("enabled", True) or now.weekday() > 4 or not in_window(now, "09:32", "16:35"):
        return None
    moving = sum(1 for r in view or [] if r.get("CurrentPrice") and r.get("LDCP") and abs(r["CurrentPrice"] - r["LDCP"]) >= 0.006)
    if moving < 20:
        return None  # holiday, or the index view has not started moving yet
    rep = _report()
    items = rep.get("items") or []
    if not items:
        return None
    from datetime import datetime, timedelta
    if rep.get("session", "") < (now - timedelta(days=5)).strftime("%Y-%m-%d"):
        return None
    px, src = live_prices(view, {it["sym"] for it in items})
    done = state.data.setdefault("level_alerts", {})
    lines = []
    for it in items:
        p = px.get(it["sym"])
        if not p:
            continue
        for ev, txt in events(it, p):
            key = f"{rep['session']}:{it['sym']}:{ev}"
            if key in done:
                continue
            done[key] = f"{now:%Y-%m-%d %H:%M}"
            # every alert is logged with its price so the Research Lab can score it later (5 and 20 sessions vs KSE-100)
            lg = state.data.setdefault("level_log", [])
            lg.append({"d": f"{now:%Y-%m-%d}", "t": f"{now:%H:%M}", "sym": it["sym"], "ev": ev, "px": round(p, 2),
                       "label": it.get("label"), "kind": it.get("kind")})
            state.data["level_log"] = lg[-600:]
            from .watchers import log_price
            log_price(state, it["sym"], p, "level alert")
            chg = f" ({(p / it['close'] - 1) * 100:+.1f}% since the close)" if it.get("close") else ""
            lines.append(f"<b>{esc(it['sym'])}</b> Rs {p:,.2f}{chg} {esc(txt)}. Setup: {esc((it.get('label') or 'n/a').lower())}")
    # keep the log small: the last 10 days of keys
    cut = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    for k in [k for k, v in done.items() if v[:10] < cut]:
        del done[k]
    if not lines:
        return None
    sess = datetime.strptime(rep["session"], "%Y-%m-%d")
    log.info("level alerts: %d", len(lines))
    return (f"<b>Level alert · {now:%H:%M}</b>\n" + "\n".join(lines)
            + f"\n\n<i>Levels from the research after the close of {sess:%d %b}; {src}. Zones and targets are chart "
              "levels (not tested); the setup comes from tested signals. Not investment advice.</i>")
