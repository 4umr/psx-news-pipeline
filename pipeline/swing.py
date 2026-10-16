"""Daily swing scanner (private research, admin chat only).

Runs once per session after the close:
1. brings the price database up to date (prices.update_live)
2. finds today's signals for the rules that passed the backtest (live=True)
3. logs every signal and follows it until its holding period ends, measuring the
   result against similar-sized stocks exactly like the backtest does
4. sends a Swing Radar card (+ plain text copy) to the admin chat

The log lives in state/swing.json (with prices.csv.gz and meta.json); the workflow keeps
these in the Actions cache and an encrypted backup, because the signals are private.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pandas as pd

from . import backtest, earnings, prices
from .common import ROOT, esc, log, now_pkt
from .signals import SIGNALS, detect, features
from .sources import scs

LOG_PATH = ROOT / "state" / "swing.json"
SPECS = {**SIGNALS, **earnings.EVENT_SIGNALS}
COST = backtest.DEFAULT_COST


# ---------------------------------------------------------------- state
def _load() -> dict:
    try:
        st = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    st.setdefault("log", [])
    st.setdefault("done", {})
    return st


def _save(st: dict) -> None:
    cut = time.time() - 150 * 86400
    st["done"] = {k: v for k, v in st["done"].items() if v > cut}
    st["log"] = [e for e in st["log"] if e["status"] == "open" or e["date"] >= f"{now_pkt() - timedelta(days=400):%Y-%m-%d}"]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    tmp.replace(LOG_PATH)


def _signal(name: str, r: pd.Series, detail: str, **extra) -> dict:
    spec = SPECS[name]
    return {"id": f"{name}:{r['sym']}:{r['date']:%Y-%m-%d}", "sig": name, "kind": spec["kind"], "sym": r["sym"],
            "date": f"{r['date']:%Y-%m-%d}", "close": round(float(r["c"]), 2), "chg": round(float(r["chg"]), 4),
            "tier": r["tier"], "h": spec["h"], "detail": detail, "status": "open", **extra}


# ---------------------------------------------------------------- rules
def _price_signals(df: pd.DataFrame, sess: pd.Timestamp, min_adtv: float) -> list[dict]:
    out = []
    masks = detect(df, min_adtv)
    for name, spec in SIGNALS.items():
        if not spec.get("live"):
            continue
        for _, r in df[masks[name] & (df["date"] == sess)].iterrows():
            out.append(_signal(name, r, f"{r['chg'] * 100:+.1f}% today, closed at the {'upper' if r['chg'] > 0 else 'lower'} limit"))
    return out


def _history(syms: list[str]) -> dict[str, pd.DataFrame]:
    with ThreadPoolExecutor(6) as ex:
        rows = dict(zip(syms, ex.map(earnings.rows_for, syms)))
    return {s: earnings.classify(pd.DataFrame(r)) for s, r in rows.items() if r}


def _eps_text(h: pd.Series) -> str:
    if h.get("turnaround"):
        return f"back to profit (EPS {h['eps_q']:.2f} vs {h['eps_q_ly']:.2f} a year ago)"
    if h.get("to_loss"):
        return f"turned to a loss (EPS {h['eps_q']:.2f} vs {h['eps_q_ly']:.2f} a year ago)"
    if pd.notna(h.get("yoy_q")):
        return f"quarterly EPS {h['eps_q']:.2f} vs {h['eps_q_ly']:.2f} a year ago ({h['yoy_q'] * 100:+.0f}%)"
    return f"quarterly EPS {h['eps_q']:.2f}" if pd.notna(h.get("eps_q")) else "EPS not given"


def _results_signals(df: pd.DataFrame, sess: pd.Timestamp, since: pd.Timestamp, min_adtv: float, st: dict) -> list[dict]:
    """Results whose reaction session is today (announced today in market hours, or after the last session)."""
    sessions = sorted(df["date"].unique())
    today = df[df["date"] == sess].set_index("sym")
    todo = []
    for r in scs.results():
        sym, lab = (r.get("company_code") or "").strip(), (r.get("bm_quarter_number") or "").strip().upper()
        if not r.get("date") or sym not in today.index or f"res:{sym}:{lab}" in st["done"]:
            continue
        d = pd.Timestamp(r["date"].strftime("%Y-%m-%d"))
        if d < since:
            continue
        ah = earnings.after_hours(r.get("bm_time") or "")
        react = next((s for s in sessions if (s > d if ah else s >= d)), None)
        if react == sess and today.at[sym, "adtv20"] >= min_adtv:
            todo.append((sym, lab))
    if not todo:
        return []
    hist = _history(sorted({s for s, _ in todo}))
    out = []
    for sym, lab in todo:
        st["done"][f"res:{sym}:{lab}"] = time.time()
        h = hist.get(sym)
        if h is None or h[h["label"] == lab].empty:
            continue
        h = h[h["label"] == lab].iloc[-1]
        r = today.loc[sym].copy()
        r["sym"] = sym
        react = float(r["chg"])
        detail = f"{lab} results: {_eps_text(h)}; stock {react * 100:+.1f}% on the day"
        if r.get("lock_up"):
            detail += " (closed at the upper limit, shares may be hard to buy at the open)"
        if h["cls"] in earnings.GOOD and react >= 0.03 and r["tier"] in earnings.EVENT_SIGNALS["results_momentum"]["tiers"]:
            out.append(_signal("results_momentum", r, detail, label=lab))
        if react >= 0.05:
            out.append(_signal("results_reaction", r, detail, label=lab))
        if react <= -0.05 and earnings.EVENT_SIGNALS["results_drop"].get("live"):
            out.append(_signal("results_drop", r, detail, label=lab))
    return out


def _pre_results(df: pd.DataFrame, sess: pd.Timestamp, min_adtv: float, st: dict) -> list[dict]:
    """Board meeting within ~5 sessions (7 calendar days) and the previous result was bad."""
    today = df[df["date"] == sess].set_index("sym")
    todo = {}
    for b in scs.board_meetings():
        sym = (b.get("company_code") or "").strip()
        if not b.get("date") or sym not in today.index or today.at[sym, "adtv20"] < min_adtv:
            continue
        d = pd.Timestamp(b["date"].strftime("%Y-%m-%d"))
        if sess < d <= sess + pd.Timedelta(days=7) and f"pre:{sym}:{d:%Y-%m-%d}" not in st["done"]:
            todo[sym] = (d, (b.get("bm_quarter_number") or "").strip())
    if not todo:
        return []
    hist = _history(sorted(todo))
    out = []
    for sym, (d, lab) in todo.items():
        st["done"][f"pre:{sym}:{d:%Y-%m-%d}"] = time.time()
        h = hist.get(sym)
        if h is None or h.empty:
            continue
        last = h.sort_values("date").iloc[-1]
        if last["cls"] not in earnings.BAD:
            continue
        r = today.loc[sym].copy()
        r["sym"] = sym
        what = f"{lab} results" if lab else "Board meeting"
        out.append(_signal("pre_results_risk", r, f"{what} on {d:%a %d %b}; last result ({last['label']}): {_eps_text(last)}",
                           meeting=f"{d:%Y-%m-%d}"))
    return out


def fresh(new: list[dict], logged: list[dict], sessions: list) -> list[dict]:
    """Drop repeats: the same rule on the same stock within its quiet period (as in the backtest)."""
    pos = {f"{pd.Timestamp(d):%Y-%m-%d}": i for i, d in enumerate(sessions)}
    last: dict[tuple, int] = {}
    for e in logged:
        k = (e["sig"], e["sym"])
        last[k] = max(last.get(k, -10**6), pos.get(e["date"], -10**6))
    ids = {e["id"] for e in logged}
    out = []
    for e in new:
        k, i = (e["sig"], e["sym"]), pos.get(e["date"], 0)
        if e["id"] in ids or i - last.get(k, -10**6) < SPECS[e["sig"]].get("gap", 1):
            continue
        last[k] = i
        out.append(e)
    return out


# ---------------------------------------------------------------- tracking
def _track(st: dict, df: pd.DataFrame, min_adtv: float) -> list[dict]:
    """Update open signals; close those whose holding period has ended. Returns newly closed."""
    ao = df.pivot_table(index="date", columns="sym", values="ao")
    ac = df.pivot_table(index="date", columns="sym", values="ac")
    info = df.set_index(["date", "sym"])[["tier", "adtv20"]]
    closed = []
    for e in st["log"]:
        if e["status"] != "open" or e["sym"] not in ac.columns:
            continue
        sig_d = pd.Timestamp(e["date"])
        rows = ac[e["sym"]].dropna()
        after = rows[rows.index > sig_d]
        if after.empty:
            continue  # entry is tomorrow's open
        entry_d = after.index[0]
        held = min(len(after), e["h"])
        exit_d = after.index[held - 1]
        entry = ao.at[entry_d, e["sym"]]
        ret = float(ac.at[exit_d, e["sym"]] / entry - 1)
        # same-size liquid stocks over the same window
        try:
            day = info.xs(sig_d, level="date")
            peers = day[(day["tier"] == e["tier"]) & (day["adtv20"] >= min_adtv)].index
        except KeyError:
            peers = []
        peers = [p for p in peers if p in ac.columns]
        base = float((ac.loc[exit_d, peers] / ao.loc[entry_d, peers] - 1).mean()) if peers else 0.0
        cost = COST.get(e["tier"], 0.013) if e["kind"] == "long" else 0.0
        e.update({"entry_date": f"{entry_d:%Y-%m-%d}", "held": int(held), "ret": round(ret, 4),
                  "base": round(base, 4), "edge": round(ret - cost - base, 4)})
        if held >= e["h"]:
            e["status"] = "closed"
            e["exit_date"] = f"{exit_d:%Y-%m-%d}"
            closed.append(e)
    return closed


# ---------------------------------------------------------------- output
def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _track_record(name: str, stats: dict) -> str:
    s = stats.get(name)
    if not s:
        return ""
    months = {60: "3 months", 20: "1 month", 10: "2 weeks", 4: "the days before results"}.get(s["h"], f"{s['h']} sessions")
    if SPECS[name]["kind"] == "risk":
        return (f"{SPECS[name]['name']} ({s['verdict'].lower()}): {(1 - s['beat']) * 100:.0f}% of {s['n']} cases did worse "
                f"than similar stocks over {months}, typical gap {abs(s['med_edge']) * 100:.1f}%")
    return (f"{SPECS[name]['name']}: beat similar stocks by {s['edge'] * 100:.1f}% on average over {months} after costs "
            f"({s['n']} cases). Only {s['beat'] * 100:.0f}% beat them, so gains come from fewer, bigger winners; "
            f"1 in 10 lost {abs(s['p10']) * 100:.0f}% or more")


def radar(cfg: dict, sess: pd.Timestamp, new: list[dict], st: dict, closed: list[dict]) -> tuple[list[bytes], str]:
    from .cards import swing_cards
    stats = backtest.load_stats().get("rules", {})
    longs = [e for e in new if e["kind"] == "long"]
    risks = [e for e in new if e["kind"] == "risk"]
    # one line per stock: merge rules that fired together (e.g. strong results + big reaction)
    def merge(items):
        by: dict[str, dict] = {}
        for e in items:
            if e["sym"] in by:
                by[e["sym"]]["names"].append(SPECS[e["sig"]]["name"])
            else:
                by[e["sym"]] = {**e, "names": [SPECS[e["sig"]]["name"]]}
        return list(by.values())
    longs, risks = merge(longs), merge(risks)
    open_ = sorted([e for e in st["log"] if e["status"] == "open" and e["kind"] == "long" and "held" in e],
                   key=lambda e: e["date"], reverse=True)
    done = [e for e in st["log"] if e["status"] == "closed"]
    used = sorted({e["sig"] for e in new})
    ctx = {"date": sess, "longs": longs, "risks": risks, "open": open_, "done": done, "closed_today": closed,
           "names": {k: v["name"] for k, v in SPECS.items()},
           "notes": [_track_record(n, stats) for n in used if _track_record(n, stats)]}
    cards = swing_cards(cfg, ctx)
    # plain text copy (caption of the first card is short; the full text follows as a message)
    lines = [f"<b>Swing Radar</b> · {sess:%a %d %b %Y} · private research", ""]
    if longs:
        lines.append("<b>Ideas to research</b>")
        lines += [f"{e['sym']} ({e['tier']} cap, Rs {e['close']:,.2f}): {' + '.join(e['names'])}. {esc(e['detail'])}." for e in longs]
        lines.append("")
    if risks:
        lines.append("<b>Caution</b>")
        lines += [f"{e['sym']} ({e['tier']} cap): {' + '.join(e['names'])}. {esc(e['detail'])}." for e in risks]
        lines.append("")
    if closed:
        lines.append("<b>Finished today</b>")
        lines += [f"{e['sym']}, {SPECS[e['sig']]['name']}: {_pct(e['ret'])} ({_pct(e['edge'])} vs similar stocks"
                  f"{' after costs' if e['kind'] == 'long' else ''})" for e in closed]
        lines.append("")
    if ctx["notes"]:
        lines.append("<b>Track record</b>")
        lines += [esc(n) for n in ctx["notes"]]
        lines.append("")
    lines.append("Research only, not investment advice. Entry is assumed at the next open.")
    return cards, "\n".join(lines)


# ---------------------------------------------------------------- main
def scan(cfg: dict, dry: bool = False, force: bool = False) -> None:
    from .telegram import Sender
    sw = cfg.get("swing", {})
    if not sw.get("enabled", True):
        return
    min_adtv = float(sw.get("min_adtv", 10e6))
    now = now_pkt()
    raw = prices.update_live(now)
    if raw.empty:
        log.warning("swing: no price data")
        return
    sess = raw["date"].max()
    st = _load()
    if st.get("last_scan") == f"{sess:%Y-%m-%d}" and not force:
        log.info("swing: session %s already scanned", sess.date())
        return
    if sess < pd.Timestamp(now.strftime("%Y-%m-%d")) - pd.Timedelta(days=4) and not force:
        log.info("swing: latest session %s is old (holiday?), nothing to scan", sess.date())
        return
    meta = prices.meta(sorted(raw["sym"].unique()))
    df = features(prices.adjust(raw), meta)
    since = pd.Timestamp(st.get("last_scan") or sorted(df["date"].unique())[-2])
    new: list[dict] = []
    for part in (lambda: _price_signals(df, sess, min_adtv),
                 lambda: _results_signals(df, sess, since, min_adtv, st),
                 lambda: _pre_results(df, sess, min_adtv, st)):
        try:
            new += part()
        except Exception as e:  # noqa: BLE001  one failing source must not stop the scan
            log.exception("swing: rule group failed: %s", e)
    new = fresh(new, st["log"], sorted(df["date"].unique()))
    st["log"].extend(new)
    closed = _track(st, df, min_adtv)
    log.info("swing: %s, %d new signals, %d closed", sess.date(), len(new), len(closed))
    weekly = sess.dayofweek == 4
    if new or closed or weekly or force:
        cards, text = radar(cfg, sess, new, st, closed)
        s = Sender(dry)
        for i, png in enumerate(cards, 1):
            s.send_admin_photo(png, "Swing Radar" if i == 1 else "", name=f"swing_{i}")
        s.send_admin(text)
    st["last_scan"] = f"{sess:%Y-%m-%d}"
    _save(st)
