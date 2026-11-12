"""Daily stock scan (Research Lab, bot chat): a few stocks every trading day, each labelled with how much the
2010-2026 tests support it. Built after the close from the swing price database (state/prices.csv.gz, updated
at 17:40), the underdog radar (snapshots, verified) and the company results announcements.

SECTIONS (tested against similar stocks over 60 sessions unless noted)
  breakouts   results with EPS +50% or back to profit AND up 3%+ on the day, small and mid caps   +4.5% (11/16 yrs)
              ... and also at 1.2x book or less                                                     +11%  (2022-26)
  backed      at 1.2x book or less, good results in the last 90 days, earnings value gap 50%+      +5.1% (4/5 yrs)
  volume      value traded 3x normal, up 3%+, near a 3-month high, good results in the last 90 days +1.5% weak
  warnings    value traded 3x normal, up 3%+, after BAD results in the last 90 days                  -3.9% (2/16 yrs)
  following   everything flagged in the last 10 sessions: move since the flag vs the KSE-100, still above
              its breakout level or not
A volume burst on its own lagged similar stocks (-1.3%, 0 of 19 years): it is never shown alone.
"""
from __future__ import annotations

import pandas as pd

from .common import log

TESTED = {"breakout": "tested: +4.5% vs similar stocks over 3 months (11 of 16 years)",
          "breakout_backed": "tested: +11% vs similar stocks over 3 months (2022-26, 78 cases)",
          "backed": "tested: +5.1% vs similar stocks over 3 months (2022-26, 4 of 5 years)",
          "volume": "weak evidence: +1.5% vs similar stocks (11 of 16 years)",
          "warning": "tested: -3.9% vs similar stocks over 3 months (only 2 of 16 years positive)"}
FOLLOW_SESSIONS = 10


def _today(session: str, min_adtv: float) -> pd.DataFrame | None:
    """Today's row per liquid stock with value vs 60-day average, change and 3-month high (adjusted)."""
    import json

    from . import prices
    from .signals import features
    if not prices.LIVE_PATH.exists():
        log.warning("scan: no price database (swing cache not restored)")
        return None
    raw = prices.load(prices.LIVE_PATH)
    raw = raw[raw["date"] >= pd.Timestamp(session) - pd.Timedelta(days=200)]
    if raw.empty or f"{raw['date'].max():%Y-%m-%d}" != session:
        log.warning("scan: price database ends %s, not %s", raw["date"].max() if len(raw) else None, session)
        return None
    try:
        meta = json.loads(prices.META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    df = features(prices.adjust(raw), meta)
    g = df.groupby("sym", sort=False)
    df["vx60"] = df["val"] / g["val"].transform(lambda s: s.shift(1).rolling(60, min_periods=40).mean())
    df["hi60"] = g["ac"].transform(lambda s: s.shift(1).rolling(60, min_periods=40).max())
    t = df[df["date"] == pd.Timestamp(session)].copy()
    return t[(t["adtv20"] >= min_adtv) & t["tier"].isin(["Mid", "Small", "Micro", "n/a"])]


def _last_result(sym: str, session: str) -> dict | None:
    """The latest results announcement up to today, if within 90 days: label, class, EPS line, date."""
    from . import earnings
    try:
        h = earnings.company(sym)
    except Exception:  # noqa: BLE001
        return None
    if h is None or h.empty:
        return None
    h = h[h["date"] <= pd.Timestamp(session)].sort_values("date")
    if h.empty:
        return None
    r = h.iloc[-1]
    if (pd.Timestamp(session) - r["date"]).days > 90:
        return None
    # the class is judged on the QUARTER's EPS against the same quarter a year earlier (for a full-year
    # announcement that is the fourth quarter), so the line shows exactly that
    q, q_ly = r.get("eps_q"), r.get("eps_q_ly")
    which = "fourth-quarter" if r.get("kind") in ("FY", "CY") else "quarterly"
    if q == q and q is not None and q_ly == q_ly and q_ly is not None:
        tail = (f" ({(q / q_ly - 1) * 100:+.0f}%)" if q_ly > 0 else (" (back to profit)" if q > 0 > q_ly else
                                                                     (" (turned to a loss)" if q < 0 < q_ly else "")))
        line = f"{which} EPS {q:.2f} vs {q_ly:.2f} a year ago{tail}"
    else:
        a = earnings.assess(sym, r["label"])
        line = earnings.eps_line(a) if a else ""
    return {"label": r["label"], "cls": r["cls"], "date": f"{r['date']:%Y-%m-%d}", "line": line,
            "days": (pd.Timestamp(session) - r["date"]).days}


def build(cfg: dict, session: str, radar: dict, st: dict, idx: dict) -> dict:
    """All sections for today; also updates the follow list and logs new flags for scoring."""
    from concurrent.futures import ThreadPoolExecutor

    from . import earnings
    from .underdog import UD_ADTV
    out = {"session": session, "breakouts": [], "backed": [], "volume": [], "warnings": [], "following": [],
           "notes": []}
    rows = {r["sym"]: r for r in radar.get("rows", [])}
    # 1) results breakouts: the radar's verified triggers + good results up 3% without the book condition
    t = _today(session, UD_ADTV)
    if t is None:
        out["notes"].append("Volume and breakout sections need today's price database; it was not available.")
    recent = {}
    from .sources import scs
    for a in scs.results():
        if a.get("date") and (pd.Timestamp(session) - pd.Timestamp(a["date"].strftime("%Y-%m-%d"))).days <= 1:
            recent.setdefault((a.get("company_code") or "").strip(), a)
    for sym, a in recent.items():
        if sym not in rows or rows[sym].get("large") or rows[sym]["adtv"] < UD_ADTV:
            continue
        ass = earnings.assess(sym, a.get("bm_quarter_number") or "")
        r = rows[sym]
        px, chg = r["price"], r["chg"]
        react = chg / (px - chg) * 100 if px and chg is not None and px - chg else None
        if not ass or ass["cls"] not in earnings.GOOD or react is None or react < 3:
            continue
        backed = bool(r.get("pb") and r["pb"] <= 1.2)
        out["breakouts"].append({"sym": sym, "price": px, "move": react, "kind": "breakout_backed" if backed else "breakout",
                                 "why": f"{ass['label']}: {earnings.eps_line(ass)}" + (f"; P/B {r['pb']:.2f}" if r.get("pb") else "")})
    # 2) asset-backed and improving (verified list from the radar + good results in the last 90 days)
    cands = [b for b in radar.get("backed", []) if (b["fv"].get("epv_gap") or 0) >= 50][:15]
    with ThreadPoolExecutor(4) as ex:
        res = dict(zip([b["sym"] for b in cands], ex.map(lambda b: _last_result(b["sym"], session), cands)))
    for b in cands:
        lr = res.get(b["sym"])
        if lr and lr["cls"] in earnings.GOOD:
            cyc = any("may not last" in f for f in b["fv"].get("flags") or [])
            out["backed"].append({"sym": b["sym"], "price": b["price"], "move": None, "kind": "backed",
                                  "why": f"P/B {b['pb']:.2f}, earnings value gap {b['fv']['epv_gap']:+.0f}%; {lr['label']}: {lr['line']}"
                                         + ("; cyclical: its 5-year average EPS is a loss" if cyc else "")})
    # 3) volume with good results, 4) volume after bad results
    if t is not None:
        burst = t[(t["vx60"] >= 3) & (t["chg"] >= 0.03)]
        syms = list(burst["sym"])[:40]
        with ThreadPoolExecutor(4) as ex:
            lr_all = dict(zip(syms, ex.map(lambda s: _last_result(s, session), syms)))
        for _, x in burst.iterrows():
            lr = lr_all.get(x["sym"])
            if not lr:
                continue
            item = {"sym": x["sym"], "price": float(x["c"]), "move": float(x["chg"]) * 100, "vx": float(x["vx60"])}
            near_hi = pd.notna(x["hi60"]) and x["ac"] >= x["hi60"] * 0.98
            if lr["cls"] in earnings.GOOD and near_hi:
                out["volume"].append({**item, "kind": "volume", "why": f"value traded {x['vx60']:.1f}x normal, at a 3-month high; "
                                                                        f"{lr['label']}: {lr['line']}"})
            elif lr["cls"] in earnings.BAD:
                out["warnings"].append({**item, "kind": "warning", "why": f"value traded {x['vx60']:.1f}x normal after weak results; "
                                                                          f"{lr['label']}: {lr['line']}"})
    for k in ("breakouts", "backed", "volume", "warnings"):
        out[k] = out[k][:6]
    # 5) following: everything flagged in the last 10 sessions
    fl = st.setdefault("scan_flags", [])
    known = {(f["sym"], f["date"]) for f in fl}
    for k in ("breakouts", "backed", "volume", "warnings"):
        for x in out[k]:
            if (x["sym"], session) not in known:
                fl.append({"sym": x["sym"], "date": session, "kind": x["kind"], "price": x["price"],
                           "view": "Underperform" if x["kind"] == "warning" else "Outperform"})
                st.setdefault("calls_scan", []).append({"id": f"SC:{x['sym']}:{session}", "sym": x["sym"], "date": session,
                                                        "price": x["price"], "kind": x["kind"],
                                                        "view": "Underperform" if x["kind"] == "warning" else "Outperform"})
    sessions = sorted(d for d in idx if d <= session)
    recent_days = set(sessions[-FOLLOW_SESSIONS - 1:-1])
    last = {r["sym"]: r for r in radar.get("rows", [])}
    for f in fl:
        if f["date"] not in recent_days or f["sym"] not in last:
            continue
        now = last[f["sym"]]["price"]
        if not now or not f["price"] or f["date"] not in idx:
            continue
        mv = (now / f["price"] - 1) * 100
        kse = (idx[session] / idx[f["date"]] - 1) * 100 if session in idx else 0.0
        held = None
        if t is not None and f["sym"] in set(t["sym"]):
            x = t[t["sym"] == f["sym"]].iloc[0]
            held = bool(pd.notna(x["hi60"]) and x["ac"] >= x["hi60"] * 0.95)
        out["following"].append({"sym": f["sym"], "since": f["date"], "kind": f["kind"], "move": mv, "vs_kse": mv - kse,
                                 "held": held})
    out["following"] = sorted(out["following"], key=lambda x: x["since"], reverse=True)[:10]
    st["scan_flags"] = [f for f in fl if f["date"] >= (sessions[-60] if len(sessions) >= 60 else sessions[0])]
    return out


def text(sc: dict) -> str:
    from .common import esc
    L = [f"<b>Daily stock scan · {sc['session']}</b>"]
    names = {"breakouts": "Results breakouts", "backed": "Asset-backed and improving", "volume": "Volume with good results",
             "warnings": "Warnings: volume after weak results"}
    any_ = False
    for k, title in names.items():
        if sc[k]:
            any_ = True
            L.append(f"\n<b>{title}</b>")
            for x in sc[k]:
                mv = f" ({x['move']:+.1f}% today)" if x.get("move") is not None else ""
                L.append(f"{x['sym']} Rs {x['price']:,.2f}{mv}: {esc(x['why'])}")
    if not any_:
        L.append("\nNo stock met any of the tested conditions today.")
    if sc["following"]:
        L.append("\n<b>Following (flagged in the last 10 sessions)</b>")
        for f in sc["following"]:
            L.append(f"{f['sym']} since {f['since'][5:]}: {f['move']:+.1f}% ({f['vs_kse']:+.1f} vs KSE-100)"
                     + ("" if f["held"] is None else (", holding near its high" if f["held"] else ", slipped from its high")))
    L += [f"\n<i>{n}</i>" for n in sc["notes"]]
    return "\n".join(L)
