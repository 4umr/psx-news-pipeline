"""Street View: capture brokerage forecasts from the news and score them against reality.

No public source ranks Pakistani analysts by accuracy, so we build our own record:
1. Every run, headlines/summaries mentioning a known brokerage + a forecast of CPI
   or the SBP policy decision are captured (with source link).
2. When PBS releases CPI, or SBP announces the policy decision, each captured
   forecast is scored. Over months this becomes a data-backed accuracy table.
"""
from __future__ import annotations

import re
import time

from .common import NewsItem, esc, link
from .state import State

BROKERS = {
    "Topline": r"top\s?line",
    "Arif Habib (AHL)": r"arif habib|\bahl\b",
    "JS Global": r"js global|js research|\bjsgcl\b",
    "AKD Securities": r"\bakd\b",
    "Insight Securities": r"insight securities",
    "Optimus Capital": r"optimus",
    "Foundation Securities": r"foundation securities",
    "Pearl Securities": r"pearl securities",
    "Ismail Iqbal": r"ismail iqbal",
    "Intermarket (IMS)": r"intermarket|\bims\b",
    "BMA Capital": r"\bbma\b",
    "Alpha Capital": r"alpha capital",
    "Chase Securities": r"chase securities",
    "Sherman Securities": r"sherman",
    "Next Capital": r"next capital",
    "Taurus Securities": r"taurus",
    "Darson Securities": r"darson",
    "Elixir Securities": r"elixir",
    "Tola Capital": r"tola capital",
    "Al Habib Capital": r"al habib capital|ahcml",
    "EFG Hermes": r"efg hermes",
    "Moody's Analytics": r"moody'?s analytics",
    "Reuters poll": r"reuters poll",
}
_BROKER_RE = {k: re.compile(v, re.I) for k, v in BROKERS.items()}
_VERB = re.compile(r"expect|forecast|project|estimat|predict|anticipat|likely|see[sn]?\b|preview|poll|survey|eye[sd]?\b", re.I)
_CPI = re.compile(r"(?:cpi|inflation|headline)[^.%]{0,90}?(\d{1,2}(?:\.\d{1,2})?)\s?(?:%|pc|percent)", re.I)
_MPC_BPS = re.compile(r"(\d{2,3})\s?(?:bps|basis points?)\s?(cut|reduction|hike|increase)|"
                      r"(cut|reduce|hike|raise|increase)[^.]{0,30}?(\d{2,3})\s?(?:bps|basis points?)", re.I)
_MPC_HOLD = re.compile(r"(hold|unchanged|status quo|maintain|pause|keep)[^.]{0,40}(policy rate|interest rate|rates?)|"
                       r"(policy rate|interest rate)[^.]{0,40}(unchanged|on hold|steady)", re.I)


def _brokers(text: str) -> list[str]:
    return [k for k, rx in _BROKER_RE.items() if rx.search(text)]


def capture(items: list[NewsItem], state: State) -> list[dict]:
    """Record new brokerage forecasts found in news items. Returns the newly captured ones."""
    store = state.data.setdefault("street", {"forecasts": [], "scores": {}})
    have = {f["u"] for f in store["forecasts"]}
    new = []
    for it in items:
        text = f"{it.title}. {it.summary}"
        if it.url in have or not _VERB.search(text):
            continue
        brokers = _brokers(text)
        if not brokers:
            continue
        rec = None
        if m := _CPI.search(text):
            v = float(m.group(1))
            if 0 < v < 50:
                rec = {"kind": "cpi", "value": v}
        elif re.search(r"policy rate|interest rate|monetary policy|\bmpc\b|rate cut|rate hike", text, re.I):
            if m := _MPC_BPS.search(text):
                bps = int(m.group(1) or m.group(4))
                word = (m.group(2) or m.group(3)).lower()
                rec = {"kind": "mpc", "value": -bps if word.startswith(("cut", "reduc")) else bps}
            elif _MPC_HOLD.search(text):
                rec = {"kind": "mpc", "value": 0}
        if not rec:
            continue
        for b in brokers[:2]:
            f = {**rec, "broker": b, "t": it.title, "u": it.url, "s": it.source, "ts": int(time.time()),
                 "scored": False}
            store["forecasts"].append(f)
            new.append(f)
        have.add(it.url)
    store["forecasts"] = store["forecasts"][-300:]
    return new


def _score(state: State, kind: str, actual: float, window_days: int, hit_tol: float) -> list[dict]:
    store = state.data.setdefault("street", {"forecasts": [], "scores": {}})
    cutoff = time.time() - window_days * 86400
    latest: dict[str, dict] = {}
    for f in store["forecasts"]:
        if f["kind"] == kind and not f["scored"] and f["ts"] >= cutoff:
            latest[f["broker"]] = f  # most recent call per broker counts
    for f in store["forecasts"]:
        if f["kind"] == kind and f["ts"] >= cutoff:
            f["scored"] = True
    results = []
    for b, f in latest.items():
        err = abs(f["value"] - actual)
        s = store["scores"].setdefault(b, {"n": 0, "hits": 0, "abs_err": 0.0, "cpi_n": 0, "mpc_n": 0})
        s["n"] += 1
        s[f"{kind}_n"] = s.get(f"{kind}_n", 0) + 1
        s["hits"] += int(err <= hit_tol)
        s["abs_err"] += err if kind == "cpi" else 0.0
        results.append({**f, "err": err, "hit": err <= hit_tol})
    return sorted(results, key=lambda r: r["err"])


def score_cpi(state: State, actual: float) -> str:
    res = _score(state, "cpi", actual, window_days=35, hit_tol=0.3)
    if not res:
        return ""
    lines = [f"{'✅' if r['hit'] else '▫️'} {esc(r['broker'])}: {r['value']:.1f}% "
             f"({'exact' if r['err'] < 0.05 else f'off by {r['err']:.1f}'})" for r in res]
    return "🧠 <b>Street forecasts vs actual</b>\n" + "\n".join(lines)


def score_mpc(state: State, actual_bps: int) -> str:
    res = _score(state, "mpc", actual_bps, window_days=30, hit_tol=0)
    if not res:
        return ""
    lines = [f"{'✅' if r['hit'] else '❌'} {esc(r['broker'])}: expected {_call(r['value'])}" for r in res]
    return "🧠 <b>Who called it?</b>\n" + "\n".join(lines)


def pending_calls(state: State, kind: str, days: int = 35) -> str:
    """Latest unscored forecast per broker (shown before CPI day / in the MPC reminder)."""
    store = state.data.get("street", {})
    cutoff = time.time() - days * 86400
    latest: dict[str, dict] = {}
    for f in store.get("forecasts", []):
        if f["kind"] == kind and not f["scored"] and f["ts"] >= cutoff:
            latest[f["broker"]] = f
    if not latest:
        return ""
    if kind == "cpi":
        return " · ".join(f"{esc(b)} {f['value']:.1f}%" for b, f in latest.items())
    return " · ".join(f"{esc(b)}: {_call(f['value'])}" for b, f in latest.items())


def _call(v: float) -> str:
    return "hold" if v == 0 else f"{abs(int(v))}bps {'cut' if v < 0 else 'hike'}"


def scoreboard(state: State, min_calls: int = 2) -> str:
    sc = state.data.get("street", {}).get("scores", {})
    rows = [(b, s) for b, s in sc.items() if s["n"] >= min_calls]
    if not rows:
        return ""
    rows.sort(key=lambda x: (-(x[1]["hits"] / x[1]["n"]), -x[1]["n"],
                             x[1]["abs_err"] / max(x[1].get("cpi_n", 0), 1)))
    out = []
    for i, (b, s) in enumerate(rows[:8], 1):
        mae = f" · CPI avg error {s['abs_err'] / s['cpi_n']:.2f}pp" if s.get("cpi_n") else ""
        out.append(f"{i}. {esc(b)} — {s['hits']}/{s['n']} correct ({s['hits'] / s['n'] * 100:.0f}%){mae}")
    return "🏆 <b>Forecast accuracy scoreboard</b> (tracked by us)\n" + "\n".join(out)
