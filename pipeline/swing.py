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
SPECS = backtest.SPECS
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
        sym, lab = (r.get("company_code") or "").strip(), earnings.norm_label(r.get("bm_quarter_number") or "")[0]
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
        prev = h.get("prev_yoy_q")
        if pd.notna(h.get("yoy_q")) and pd.notna(prev) and h["yoy_q"] >= 0.2 and prev >= 0 and h["yoy_q"] - prev >= 0.3:
            out.append(_signal("earn_accel", r, f"{detail}; growth up from {prev * 100:+.0f}% last quarter", label=lab))
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


def _valuation_signals(df: pd.DataFrame, sess: pd.Timestamp, min_adtv: float) -> list[dict]:
    """Weekly (Friday): most expensive 20% of liquid stocks by trailing P/E whose last result showed EPS down 20%+."""
    from . import research
    res = earnings.classify(earnings.load_all(sorted(df["sym"].unique())))
    v = research.valuation(df, res)
    t = v[(v["date"] == sess) & (v["adtv20"] >= min_adtv) & v["pe"].notna()].copy()
    if len(t) < 50:  # too few stocks with a usable P/E: something is wrong with the data, skip
        log.warning("swing: only %d stocks with a P/E, valuation rule skipped", len(t))
        return []
    t["pct"] = t["pe"].rank(pct=True)
    out = []
    if t["date"].min() - df["date"].min() < pd.Timedelta(days=700):
        log.warning("swing: price history shorter than 2 years, valuation rule skipped until it is backfilled")
        return []
    for _, r in t[(t["pct"] >= 0.8) & (t["last_yoy"] <= -0.2)].iterrows():
        last = "turned to a loss" if r["last_yoy"] <= -1 else f"EPS {r['last_yoy'] * 100:+.0f}% vs a year ago"
        out.append(_signal("dear_worsening", r, f"P/E {r['pe']:.0f} on trailing 12-month EPS of Rs {r['ttm']:.2f} "
                           f"(most expensive fifth of liquid stocks); last result {last}"))
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
def active_rules(st: dict) -> dict[str, str]:
    """Rules the scanner may use now -> why not, for the others ('' = on).

    A rule is on only if it is marked live, its latest re-test verdict still qualifies, and its
    live track record has not failed (see _monitor)."""
    stats = backtest.load_stats().get("rules", {})
    out = {}
    for name, spec in SPECS.items():
        if not spec.get("live"):
            continue
        v = (stats.get(name) or {}).get("verdict", "")
        if v not in backtest.ACTIVE:
            out[name] = f"re-test verdict: {v or 'none'}"
        elif name in st.get("paused", {}):
            out[name] = st["paused"][name]
        else:
            out[name] = ""
    return out


def _band_ok(df: pd.DataFrame, sess: pd.Timestamp) -> bool:
    """PSX price limits are coded from history (10% since Jun 2024). If several stocks moved beyond the
    limit today, PSX has probably changed it: circuit-based rules pause until the code is updated."""
    d = df[(df["date"] == sess) & (df["c"] > 20)]
    beyond = (d["chg"].abs() > d["limit"] + 0.012).sum()
    return beyond < 3


def _monitor(st: dict, notes: list[str]) -> None:
    """Live track record per rule. Warn when it falls short of the backtest; pause a rule that keeps failing."""
    paused = st.setdefault("paused", {})
    for name, spec in SPECS.items():
        if not spec.get("live") or name in paused:
            continue
        done = [e["edge"] for e in st["log"] if e["sig"] == name and e["status"] == "closed"]
        if len(done) < 20:
            continue
        avg = sum(done) / len(done)
        failing = avg < 0 if spec["kind"] == "long" else avg > 0
        if not failing:
            continue
        if len(done) >= 40 and abs(avg) >= 0.01:
            paused[name] = f"paused: live record {avg * 100:+.1f}% vs similar stocks over {len(done)} cases"
            notes.append(f"{spec['name']} is now PAUSED: after {len(done)} live cases it did {avg * 100:+.1f}% vs "
                         "similar stocks, the opposite of the backtest.")
        else:
            notes.append(f"Watch: {spec['name']} has done {avg * 100:+.1f}% vs similar stocks over {len(done)} live "
                         "cases, weaker than the backtest. It pauses itself if this continues to 40 cases.")


def scan(cfg: dict, dry: bool = False, force: bool = False) -> None:
    from .telegram import Sender
    sw = cfg.get("swing", {})
    if not sw.get("enabled", True):
        return
    min_adtv = float(sw.get("min_adtv", 10e6))
    now = now_pkt()
    s = Sender(dry)
    started_fresh = not LOG_PATH.exists()
    raw = prices.update_live(now)
    if raw.empty:
        log.warning("swing: no price data")
        s.send_admin("<b>Swing scanner check</b>\nNo price data could be loaded today (scstrade). It retries automatically.")
        return
    if raw.attrs.get("stale"):
        s.send_admin("<b>Swing scanner check</b>\nToday's prices could not be loaded completely (scstrade). "
                     "The database was not changed; the next run retries.")
        return
    sess = raw["date"].max()
    st = _load()
    if st.get("last_scan") == f"{sess:%Y-%m-%d}" and not force:
        log.info("swing: session %s already scanned", sess.date())
        return
    if sess < pd.Timestamp(now.strftime("%Y-%m-%d")) - pd.Timedelta(days=4) and not force:
        log.info("swing: latest session %s is old (holiday?), nothing to scan", sess.date())
        return
    notes: list[str] = []
    if started_fresh:
        notes.append("Swing log started fresh (no saved data was found). Earlier open ideas, if any, are not tracked.")
    n_today = int((raw["date"] == sess).sum())
    if n_today < 250:
        notes.append(f"Only {n_today} stocks have prices for {sess:%d %b}; the data source may be incomplete today.")
    meta = prices.meta(sorted(raw["sym"].unique()))
    if sum(1 for m in meta.values() if m.get("shares")) < 0.8 * raw["sym"].nunique():
        notes.append("Company share counts are missing for many stocks (PSX pages); size groups may be wrong today.")
    df = features(prices.adjust(raw), meta)
    on = active_rules(st)
    if not _band_ok(df, sess):
        for name in ("lower_lock", "upper_lock2"):
            if name in on and not on[name]:
                on[name] = "paused today: PSX price limit looks different from 10%"
        notes.append("Several stocks moved beyond the 10% price limit today: has PSX changed the limit? "
                     "Circuit-based rules are paused until the code is checked.")
    use = {k for k, why in on.items() if not why}
    if not use:
        notes.append("All swing rules are switched off (re-test verdicts or live record). No new signals until one qualifies again.")
    since = pd.Timestamp(st.get("last_scan") or sorted(df["date"].unique())[-2])
    new: list[dict] = []
    groups = [("price rules", lambda: _price_signals(df, sess, min_adtv)),
              ("results rules", lambda: _results_signals(df, sess, since, min_adtv, st))]
    if "pre_results_risk" in use:
        groups.append(("pre-results rule", lambda: _pre_results(df, sess, min_adtv, st)))
    if "dear_worsening" in use and (sess.dayofweek == 4 or force):
        groups.append(("valuation rule", lambda: _valuation_signals(df, sess, min_adtv)))
    for label, part in groups:
        try:
            new += part()
        except Exception as e:  # noqa: BLE001  one failing source must not stop the scan
            log.exception("swing: %s failed", label)
            notes.append(f"{label} failed today ({type(e).__name__}); the other rules still ran.")
    new = [e for e in new if e["sig"] in use]
    new = fresh(new, st["log"], sorted(df["date"].unique()))
    st["log"].extend(new)
    try:
        closed = _track(st, df, min_adtv)
    except Exception as e:  # noqa: BLE001
        log.exception("swing: tracking failed")
        notes.append(f"Tracking of open ideas failed today ({type(e).__name__}).")
        closed = []
    _monitor(st, notes)
    off = [f"{SPECS[k]['name']}: {why}" for k, why in on.items() if why]
    log.info("swing: %s, %d new signals, %d closed, rules off: %s", sess.date(), len(new), len(closed), off or "none")
    if new or closed or sess.dayofweek == 4 or force:
        cards, text = radar(cfg, sess, new, st, closed)
        for i, png in enumerate(cards, 1):
            s.send_admin_photo(png, "Swing Radar" if i == 1 else "", name=f"swing_{i}")
        if off:
            text += "\n\n<b>Rules switched off</b>\n" + "\n".join(esc(x) for x in off)
        s.send_admin(text)
    if notes:
        s.send_admin("<b>Swing scanner check</b>\n" + "\n".join(esc(n) for n in notes))
    st["last_scan"] = f"{sess:%Y-%m-%d}"
    _save(st)


# ---------------------------------------------------------------- Sunday public cards (data only)
def _no_recent_filing(syms: list[str], days: int = 14) -> set[str]:
    from .sources import psx
    cut = f"{now_pkt() - timedelta(days=days):%Y-%m-%d}"
    with ThreadPoolExecutor(6) as ex:
        pages = dict(zip(syms, ex.map(psx.company, syms)))
    return {s for s, p in pages.items() if p is not None and not any(f["date"] >= cut for f in p["filings"])}


def weekly(cfg: dict, dry: bool = False, force: bool = False) -> None:
    """Sunday: 'Mid & small caps this week' + 'Results week ahead' to the public channel."""
    from .cards import _p, _col, weekly_cards
    from .telegram import Sender
    now = now_pkt()
    st = _load()
    week_id = f"{now:%G-W%V}"
    if st.get("weekly") == week_id and not force:
        log.info("weekly cards already posted for %s", week_id)
        return
    min_adtv = float(cfg.get("swing", {}).get("min_adtv", 10e6))
    raw = prices.update_live(now)
    meta = prices.meta(sorted(raw["sym"].unique()))
    df = features(prices.adjust(raw), meta)
    sessions = sorted(df["date"].unique())
    wk = pd.Series(sessions).dt.to_period("W")
    this_week = [s for s, w in zip(sessions, wk) if w == wk.iloc[-1]]
    if not this_week or (pd.Timestamp(now.strftime("%Y-%m-%d")) - pd.Timestamp(this_week[-1])).days > 6:
        log.info("weekly: no session this week")
        return
    end, start = pd.Timestamp(this_week[-1]), pd.Timestamp(sessions[sessions.index(this_week[0]) - 1])
    last = df[df["date"] == end].set_index("sym")
    prev = df[df["date"] == start].set_index("sym")["ac"]
    pool = last[(last["adtv20"] >= min_adtv) & last["tier"].isin(["Mid", "Small", "Micro"])].copy()
    pool["wk"] = (pool["ac"] / prev.reindex(pool.index) - 1) * 100
    pool = pool.dropna(subset=["wk"])
    big = last[last["tier"] == "Large"].copy()
    big_wk = ((big["ac"] / prev.reindex(big.index) - 1) * 100).dropna()
    up, down = int((pool["wk"] > 0).sum()), int((pool["wk"] < 0).sum())
    strip = [("Liquid mid & small", str(len(pool)), None), ("Up / down", f"{up} / {down}", None),
             ("Typical move", f"{pool['wk'].median():+.1f}%", _col(pool["wk"].median()))]
    if len(big_wk):
        strip.append(("Large caps typical", f"{big_wk.median():+.1f}%", _col(big_wk.median())))
    srt = pool.sort_values("wk")
    gain = [(s, float(v)) for s, v in srt["wk"][::-1].head(5).items() if v > 0]
    lose = [(s, float(v)) for s, v in srt["wk"].head(5).items() if v < 0]
    # trading value this week vs the 4 weeks before
    wk_rows = df[df["date"].isin(this_week)].groupby("sym")["val"].mean()
    before = df[(df["date"] < this_week[0]) & (df["date"] >= sessions[max(0, sessions.index(this_week[0]) - 20)])]
    usual = before.groupby("sym")["val"].mean()
    ratio = (wk_rows / usual).reindex(pool.index).dropna().sort_values(ascending=False)
    active = [(s, pool.at[s, "tier"], f"{x:.1f}x", (_p(pool.at[s, "wk"] / 100), _col(pool.at[s, "wk"])))
              for s, x in ratio.head(5).items() if x >= 1.5]
    movers = [s for s in pool.index if abs(pool.at[s, "wk"]) >= 15][:10]
    quiet = []
    if movers:
        for s in sorted(_no_recent_filing(movers), key=lambda s: -abs(pool.at[s, "wk"])):
            quiet.append((s, f"{pool.at[s, 'wk']:+.1f}% this week", "No company filing on PSX in the last 2 weeks. "
                          "Check the facts before acting on rumours.", f"Rs {pool.at[s, 'c']:,.2f}", pool.at[s, "wk"]))
    notes = ["Mid and small caps: stocks ranked 31st and below by market value, trading Rs 10m+ a day.",
             "Week = last close of the previous week to this week's close; dividends and bonuses included."]
    # Results week ahead: board meetings in the next 7 days for liquid stocks
    nxt = now + timedelta(days=(7 - now.weekday()) % 7 or 7)
    s0 = pd.Timestamp(nxt.strftime("%Y-%m-%d"))
    meetings = {}
    for b in scs.board_meetings():
        sym = (b.get("company_code") or "").strip()
        if not b.get("date") or sym not in last.index or last.at[sym, "adtv20"] < min_adtv:
            continue
        d = pd.Timestamp(b["date"].strftime("%Y-%m-%d"))
        if s0 <= d < s0 + pd.Timedelta(days=7):
            meetings[sym] = min(meetings.get(sym, d), d)
    cal, n_more = [], 0
    if meetings:
        with ThreadPoolExecutor(6) as ex:
            last_res = dict(zip(meetings, ex.map(lambda s: earnings.assess(s), meetings)))
        order = sorted(meetings, key=lambda s: (meetings[s], -(last.at[s, "mcap"] if pd.notna(last.at[s, "mcap"]) else 0)))
        for s in order:
            a = last_res.get(s)
            txt, chg = "no earlier result found", ""
            if a and pd.notna(a["eps"]):
                txt = f"{a['label']}: EPS {a['eps']:.2f}" + (f" vs {a['eps_ly']:.2f}" if pd.notna(a["eps_ly"]) else "")
                if a["yoy"] is not None:
                    chg = (f"{a['yoy'] * 100:+,.0f}%" if abs(a["yoy"]) >= 1 else _p(a["yoy"]), _col(a["yoy"]))
                elif a["turnaround"]:
                    chg = ("Profit", _col(1))
                elif a["eps"] < 0:
                    chg = ("Loss", _col(-1))
            cal.append((f"{meetings[s]:%a %d}", s, txt, chg))
        n_more = max(0, len(cal) - 17)
    cal_notes = ["Board meeting dates from PSX notices; most are for quarterly results.",
                 "Last result = the latest announcement, compared with the same period a year earlier."]
    if n_more:
        cal_notes.insert(0, f"{n_more} more liquid companies also meet next week.")
    ctx = {"label": f"Week ending {end:%d %b %Y}", "strip": strip, "gain": gain, "lose": lose, "active": active,
           "quiet": quiet, "notes": notes, "calendar": cal, "cal_notes": cal_notes,
           "cal_label": f"Week of {s0:%d %b %Y}"}
    cards = weekly_cards(cfg, ctx)
    caption = (f"<b>Mid and small caps this week</b> · week ending {end:%d %b}"
               + (f"\n<b>Results due next week</b>: {len(cal)} liquid companies" if cal else "")
               + "\nData only, not investment advice.")
    s = Sender(dry)
    if s.send_album(cards, caption, name="weekly_midsmall"):
        st["weekly"] = week_id
        _save(st)
