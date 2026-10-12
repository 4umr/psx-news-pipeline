"""Market context pack: the latest picture of Pakistan's market and economy, as plain text.

Given to the AI with every analysis so it judges news against the real backdrop
(rates, inflation, flows, the index trend, oil, the dollar, today's other news and
broker views) instead of in isolation. Built only from data the pipeline already has;
every number here comes from an official or market source.
"""
from __future__ import annotations

import time

from .common import now_pkt
from .state import State


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else 0.0


def build(state: State, view: list[dict] | None = None, mk: dict | None = None) -> str:
    from . import watchers as w

    now = now_pkt()
    L = [f"MARKET CONTEXT as of {now:%d %b %Y %H:%M} PKT"]
    view, mk = view or [], mk or {}

    # PSX
    cur = view[0].get("CurrentIndex") if view else None
    pre = view[0].get("PreIndex") if view else None
    closes = list((state.data.get("kse_closes") or {}).items())
    if cur and pre:
        L.append(f"KSE-100: {cur:,.0f} ({cur - pre:+,.0f} points, {_pct(cur, pre):+.2f}% vs previous close {pre:,.0f})")
    elif closes:
        L.append(f"KSE-100 last close: {closes[-1][1]:,.0f} ({closes[-1][0]})")
    if len(closes) >= 15:
        L.append(f"KSE-100 change over the last {len(closes[-21:]) - 1} sessions: "
                 f"{_pct(closes[-1][1], closes[-21:][0][1]):+.1f}%")
    if view:
        rows = sorted([r for r in view if r.get("NetIndexPoint") is not None], key=lambda r: r["NetIndexPoint"])
        up = [f"{r['company_code']} {r['NetIndexPoint']:+.0f}" for r in reversed(rows[-4:]) if r["NetIndexPoint"] > 0]
        dn = [f"{r['company_code']} {r['NetIndexPoint']:+.0f}" for r in rows[:4] if r["NetIndexPoint"] < 0]
        if up or dn:
            L.append(f"Index points today: lifting {', '.join(up) or 'none'}; dragging {', '.join(dn) or 'none'}")
    if f := state.snap("fipi_last"):
        net = (f.get("summary") or {}).get("FIPI", {}).get("net")
        if net is not None:
            L.append(f"Foreign investors (FIPI) on {f['date']}: net {net:+.2f} million USD")
    flows = [d.get("fipi") for _, d in sorted((state.data.get("daily") or {}).items())[-5:] if "fipi" in d]
    if len(flows) >= 2:
        L.append(f"Foreign flows over the last {len(flows)} sessions: net {sum(flows):+.2f} million USD")

    # Rates, inflation, external
    s = state.snap("sbp") or {}
    g = (state.snap("cpi") or {}).get("general")
    if s.get("policy_rate") is not None:
        L.append(f"SBP policy rate: {s['policy_rate']:.2f}%")
    if g:
        L.append(f"CPI inflation {g['month']}: {g['yoy']:.1f}% YoY (previous month {g['prev_yoy']:.1f}%), "
                 f"month on month {g['mom']:+.1f}%")
        if s.get("policy_rate") is not None:
            L.append(f"Real policy rate: {s['policy_rate'] - g['yoy']:+.1f}%")
    if sig := w.rate_signal(s):
        L.append(sig.replace("📡 ", ""))
    if (m := s.get("mtb")) and m.get("yields"):
        L.append("Latest T-bill cut-offs (" + str(m.get("as_on", "")) + "): " +
                 ", ".join(f"{t} {y:.2f}%" for t, y in m["yields"].items() if y))
    if k := s.get("kibor"):
        L.append(f"6M KIBOR: {k['6M'][1]:.2f}%")
    if r := s.get("reserves"):
        hist = list(((state.data.get("hist") or {}).get("reserves") or {}).values())
        chg = f", {r['sbp'] - hist[-5]:+,.0f} million over 4 weeks" if len(hist) >= 5 else ""
        L.append(f"SBP reserves: {r['sbp'] / 1000:.2f} billion USD (as on {r['as_on']}{chg}); "
                 f"total liquid {r['total'] / 1000:.2f} billion USD")
    if u := s.get("usdpkr"):
        L.append(f"USD/PKR interbank: {u['m2m']:.2f}")
    if nxt := w.next_mpc(state, now):
        L.append(f"Next SBP monetary policy meeting: {nxt:%d %b %Y} ({(nxt - now.date()).days} days away)")

    # Global
    names = {"BZ=F": "Brent oil USD", "GC=F": "Gold USD/oz", "DX-Y.NYB": "Dollar index", "^TNX": "US 10Y yield %",
             "^GSPC": "S&P 500", "^VIX": "VIX"}
    glob = [f"{n} {mk[sym]['last']:,.2f} ({mk[sym]['pct']:+.1f}% on the day)" for sym, n in names.items()
            if sym in mk and mk[sym].get("pct") is not None]
    if glob:
        L.append("Global: " + "; ".join(glob))

    # Today's other news and broker views
    cutoff = time.time() - 14 * 3600
    hs = sorted((h for h in state.data.get("headlines", []) if h["ts"] >= cutoff), key=lambda h: (-h["sc"], -h["ts"]))
    if hs:
        L.append("Other headlines today (as reported by media, NOT verified; official data above wins):")
        L += [f"- {h['t']} ({h['s']})" for h in hs[:6]]
    if notes := state.snap("broker_notes"):
        L.append("Broker research notes today:")
        L += [f"- {n}" for n in notes[:6]]
    return "\n".join(L)


def tickers(view: list[dict] | None) -> list[str]:
    return sorted({r["company_code"] for r in (view or []) if r.get("company_code")})
