"""Underdog method (Umer's way of buying small and mid caps), turned into formulas and tested.

Big companies are bought on fair value and fundamentals. Underdogs are bought where the price has a floor:
the company's assets and the levels where buyers came in before, at a season that has worked before,
then held for the move when volume (institutions, brokers, high-net-worth buyers) arrives.

FORMULAS (all per share, all from public data):
  AV       asset value          = latest book value per share (scstrade)
  HF       history floor        = 10th percentile of daily closes over the last 3 years (adjusted for
                                  dividends, bonus and splits; needs 2 years of history)
  ZONE     accumulation zone    = from min(AV, HF) to max(AV, HF) x 1.05
  ENTRY    average buy price    = middle of the zone
  BREAK    zone broken          = close below min(AV, HF) x 0.90 (the floor failed: the idea is wrong)
  ATH%     price / all-time high (adjusted)
  SEASON   for each calendar month: average return in past years and the share of years it rose
           (needs 8+ years); a strong season = average >= +3% and up in >= 70% of years
  IGNITION today's traded value >= 3x its 60-day average and the price up 3%+ (big buyers arriving)
  TRAP     book value down 15%+ in a year, losses in the last 4 quarters, or debt above 2x equity:
           cheap for a reason; the floor can keep falling
Rules built from these are judged like every other rule: entry next open, costs, versus similar stocks,
and they must work in both halves of the history before they count.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FLOOR_WINDOW = 750          # sessions (about 3 years)
FLOOR_MIN = 500             # need at least 2 years
FLOOR_Q = 0.10
ZONE_PAD = 1.05
BREAK = 0.90
IGNITION_X = 3.0
IGNITION_UP = 0.03
SEASON_MIN_YEARS = 8
SEASON_AVG = 3.0
SEASON_UP = 0.70
UD_ADTV = 2e6               # underdogs trade less: Rs 2m a day is the floor (the main rules use Rs 10m)

UD_SIGNALS: dict[str, dict] = {
    "ud_floor":        {"name": "Back at its history floor", "kind": "long", "h": 60, "gap": 40,
                        "rule": "Price enters the zone near its 3-year floor (10th percentile of closes); small and mid caps"},
    "ud_floor_ign":    {"name": "Floor plus volume ignition", "kind": "long", "h": 60, "gap": 40,
                        "rule": "In the floor zone within the last 20 sessions, then 3x normal value traded and up 3%+"},
    "ud_deep":         {"name": "Deep below all-time high, at floor", "kind": "long", "h": 60, "gap": 40,
                        "rule": "Price at 25% or less of its all-time high and entering the floor zone"},
    "ud_season":       {"name": "Strong season ahead", "kind": "long", "h": 20, "gap": 15,
                        "rule": "Start of a calendar month that rose in 70%+ of 8+ past years, +3% on average"},
    "ud_season_floor": {"name": "Strong season, near the floor", "kind": "long", "h": 20, "gap": 15,
                        "rule": "Strong season starting and the price within 15% of its 3-year floor"},
    "ud_asset":        {"name": "Below asset value", "kind": "long", "h": 60, "gap": 60,
                        "rule": "Price falls below book value per share (P/B under 1), first time in 60 sessions"},
    "ud_asset_ign":    {"name": "Near asset value plus ignition", "kind": "long", "h": 60, "gap": 40,
                        "rule": "P/B 1.2 or less, then 3x normal value traded and up 3%+"},
    "ud_trap":         {"name": "Cheap but assets shrinking", "kind": "risk", "h": 60, "gap": 60,
                        "rule": "P/B under 1 while book value fell 15%+ in a year"},
}


# ---------------------------------------------------------------- live formulas for one stock
def levels(closes: list[float], av: float | None, ath: float | None = None) -> dict:
    """Zone, entry, break level and distances, from adjusted daily closes (oldest first) and book value."""
    c = np.asarray([x for x in closes if x], dtype=float)
    out: dict = {"price": float(c[-1]) if len(c) else None, "av": av}
    if len(c) >= FLOOR_MIN:
        out["hf"] = float(np.quantile(c[-FLOOR_WINDOW - 1:-1], FLOOR_Q))
    base = [x for x in (out.get("hf"), av) if x and x > 0]
    if base:
        lo, hi = min(base), max(base) * ZONE_PAD
        out.update(zone_low=lo, zone_high=hi, entry=(lo + hi) / 2, brk=lo * BREAK)
        p = out["price"]
        out["vs_zone"] = (p / hi - 1) * 100 if p > hi else (0.0 if p >= lo else (p / lo - 1) * 100)
        out["in_zone"] = lo <= p <= hi
    if len(c) >= 250:
        out["hi52"] = float(c[-250:].max())
        out["med3y"] = float(np.median(c[-FLOOR_WINDOW:]))
        out["hi3y"] = float(c[-FLOOR_WINDOW:].max())
    top = max(ath or 0, float(c.max()) if len(c) else 0)
    out["ath"] = top or None
    out["ath_pct"] = out["price"] / top * 100 if top and out["price"] else None
    if av and out["price"]:
        out["pb"] = out["price"] / av
    return out


def season(monthly: dict[str, float]) -> dict[int, dict]:
    """monthly = {'YYYY-MM': month-end adjusted close}. Per calendar month: average return, years up, years."""
    keys = sorted(monthly)
    rets: dict[int, list[float]] = {}
    for a, b in zip(keys, keys[1:]):
        rets.setdefault(int(b[5:]), []).append((monthly[b] / monthly[a] - 1) * 100)
    return {m: {"avg": float(np.mean(r)), "up": sum(x > 0 for x in r), "n": len(r),
                "strong": len(r) >= SEASON_MIN_YEARS and np.mean(r) >= SEASON_AVG and sum(x > 0 for x in r) / len(r) >= SEASON_UP}
            for m, r in rets.items()}


def ignition(values: list[float], chg: float | None) -> dict:
    """Traded value today vs the 60-day average (values oldest first, today last)."""
    v = np.asarray(values, dtype=float)
    if len(v) < 61 or not v[-61:-1].mean():
        return {"x": None, "on": False}
    x = float(v[-1] / v[-61:-1].mean())
    return {"x": x, "x5": float(v[-5:].mean() / v[-65:-5].mean()) if v[-65:-5].mean() else None,
            "on": x >= IGNITION_X and (chg or 0) >= IGNITION_UP}


def trap(bv_hist: dict[str, float], eps_q: dict[str, float], tde: float | None) -> list[str]:
    flags = []
    ys = sorted(bv_hist)
    if len(ys) >= 2 and bv_hist[ys[-2]] > 0 and bv_hist[ys[-1]] / bv_hist[ys[-2]] - 1 <= -0.15:
        flags.append(f"book value down {abs(bv_hist[ys[-1]] / bv_hist[ys[-2]] - 1) * 100:.0f}% in a year")
    last4 = [eps_q[k] for k in sorted(eps_q, key=lambda k: (k[:4], k[-1]))[-4:]]
    if len(last4) == 4 and sum(last4) < 0:
        flags.append(f"losses over the last 4 quarters (EPS {sum(last4):.2f})")
    if tde and tde > 200:
        flags.append(f"debt {tde / 100:.1f}x equity")
    return flags


# ---------------------------------------------------------------- backtest features (point in time)
def features(df: pd.DataFrame, bv: pd.DataFrame | None = None, res: pd.DataFrame | None = None) -> pd.DataFrame:
    """df = signals.features(prices.adjust(raw)) on the long history. Adds floor, zone, ATH ratio, value
    ratio, P/B (when book values are known at that date) and season statistics."""
    g = df.groupby("sym", sort=False)
    df["hf"] = g["ac"].transform(lambda s: s.shift(1).rolling(FLOOR_WINDOW, min_periods=FLOOR_MIN).quantile(FLOOR_Q))
    df["zone_hi"] = df["hf"] * ZONE_PAD
    df["in_floor"] = (df["ac"] <= df["zone_hi"]) & (df["ac"] >= df["hf"] * BREAK)
    df["near_floor"] = df["ac"] <= df["hf"] * 1.15
    df["athr"] = df["ac"] / g["ac"].transform(lambda s: s.cummax().shift(1))
    df["vx60"] = df["val"] / g["val"].transform(lambda s: s.shift(1).rolling(60, min_periods=40).mean())
    df["ign"] = (df["vx60"] >= IGNITION_X) & (df["chg"] >= IGNITION_UP)
    df["floor20"] = g["in_floor"].transform(lambda s: s.astype(float).rolling(20, min_periods=1).max()) > 0
    # season: for each stock, month and year, the same month's returns in EARLIER years only
    df["ym"] = df["date"].dt.to_period("M")
    me = df.groupby(["sym", "ym"])["ac"].last().reset_index()
    me["ret"] = me.groupby("sym")["ac"].pct_change() * 100
    me["m"] = me["ym"].dt.month
    me = me.sort_values(["sym", "m", "ym"])
    gm = me.groupby(["sym", "m"])["ret"]
    me["s_n"] = gm.transform(lambda s: s.shift(1).expanding().count())
    me["s_avg"] = gm.transform(lambda s: s.shift(1).expanding().mean())
    me["s_up"] = gm.transform(lambda s: (s.shift(1) > 0).astype(float).where(s.shift(1).notna()).expanding().mean())
    df = df.merge(me[["sym", "ym", "s_n", "s_avg", "s_up"]], on=["sym", "ym"], how="left")
    first = df["date"] == df.groupby(["sym", "ym"])["date"].transform("min")
    df["season_start"] = first & (df["s_n"] >= SEASON_MIN_YEARS) & (df["s_avg"] >= SEASON_AVG) & (df["s_up"] >= SEASON_UP)
    # P/B from book values known at the time (published with that fiscal year's latest results)
    df["pb"], df["bv_chg"] = np.nan, np.nan
    if bv is not None and res is not None and len(bv):
        df = _pb(df, bv, res)
    return df


def _pb(df: pd.DataFrame, bv: pd.DataFrame, res: pd.DataFrame) -> pd.DataFrame:
    r = res[["sym", "date", "yy"]].dropna().copy()
    r["date"] = pd.to_datetime(r["date"])
    pub = r.groupby(["sym", "yy"])["date"].max().reset_index()
    b = bv.copy()
    b["yy"] = b["year"].astype(int) % 100
    b = b.merge(pub, on=["sym", "yy"], how="inner").sort_values(["sym", "year"])
    b["bv_prev"] = b.groupby("sym")["bv"].shift(1)
    b["bv_chg"] = b["bv"] / b["bv_prev"] - 1
    b = b.rename(columns={"date": "pub"}).sort_values("pub")
    left = df[["date", "sym"]].reset_index().sort_values("date")
    m = pd.merge_asof(left, b[["pub", "sym", "bv", "bv_chg"]], left_on="date", right_on="pub", by="sym",
                      allow_exact_matches=False).set_index("index").sort_index()
    df["bv"], df["bv_chg"] = m["bv"], m["bv_chg"]
    df["pb"] = (df["c"] / df["bv"]).where(df["bv"] > 0)
    # announced book value per share mixes share counts around a bonus issue or split: skip 2 years after one
    big = (df["exf"] < 0.92).astype(float).groupby(df["sym"]).transform(lambda s: s.rolling(520, min_periods=1).max())
    df.loc[big > 0, "pb"] = np.nan
    return df


def events(df: pd.DataFrame) -> pd.DataFrame:
    from .signals import debounce
    liquid = (df["adtv20"] >= UD_ADTV) & df["tier"].isin(["Mid", "Small", "Micro", "n/a"])
    fresh_floor = df["in_floor"] & ~df.groupby("sym")["in_floor"].shift(1, fill_value=False)
    pb, pbp = df["pb"], df.groupby("sym")["pb"].shift(1)
    masks = {
        "ud_floor": fresh_floor,
        "ud_floor_ign": df["floor20"] & df["ign"],
        "ud_deep": fresh_floor & (df["athr"] <= 0.25),
        "ud_season": df["season_start"],
        "ud_season_floor": df["season_start"] & df["near_floor"],
        "ud_asset": (pb < 1) & (pbp >= 1),
        "ud_asset_ign": (pb <= 1.2) & df["ign"],
        "ud_trap": (pb < 1) & (pbp >= 1) & (df["bv_chg"] <= -0.15),
    }
    out = []
    cols = ["date", "sym", "si", "c", "chg", "vx", "adtv20", "tier", "ff_pct", "r10"]
    for name, m in masks.items():
        k = debounce(df, (m.fillna(False) & liquid), UD_SIGNALS[name]["gap"])
        e = df.loc[k, cols].copy()
        e["signal"] = name
        out.append(e)
    return pd.concat(out)


# ---------------------------------------------------------------- daily radar (live)
TRIGGER_TEXT = ("Tested trigger: results with EPS up 50%+ on last year (or back to profit), the stock up 3%+ on the "
                "day and the price at 1.2x book value or less: +11% versus similar stocks over the next 60 sessions "
                "(2022-2026, 78 cases, about half beat; promising, not proven).")


def _history_detail(sym: str) -> dict:
    """Adjusted daily history since 2008 for one stock: closes, values, month-end closes."""
    from datetime import datetime

    from . import prices
    raw = prices.fetch([sym], datetime(2008, 1, 1), datetime.now(), workers=1)
    if raw.empty:
        return {}
    df = prices.adjust(raw)
    me = df.groupby(df["date"].dt.to_period("M"))["ac"].last()
    return {"closes": df["ac"].tolist(), "values": df["val"].tolist(), "chg": float(df["chg"].iloc[-1]),
            "monthly": {str(k): float(v) for k, v in me.items()}, "first": f"{df['date'].iloc[0]:%Y}",
            "last_date": f"{df['date'].iloc[-1]:%Y-%m-%d}", "last_close": float(df["c"].iloc[-1])}


def verify(sym: str, snap: dict, close: float | None, cost: float) -> dict:
    """Every check for a stock we are about to show (checks.py). Fair value only from verified EPS."""
    from . import checks, fairvalue
    from .sources import scs_deep
    probs = checks.basic(snap, close)
    t = checks.trailing(sym, snap)
    bvh = scs_deep.book_values(sym)
    if bvh and snap["values"].get("bv") and abs(bvh[max(bvh)] / snap["values"]["bv"] - 1) > 0.01:
        probs.append(f"book value {snap['values']['bv']:,.2f} differs from the latest in its history {bvh[max(bvh)]:,.2f}")
    # book value brought up to date when the snapshot misses the latest results:
    # + earnings of those quarters - cash dividends declared with them (dividend % x face value)
    v = snap["values"]
    bv_now, bv_note = v.get("bv"), None
    if t.get("added") and v.get("bv"):
        face = v.get("facevalue") or 10.0
        change = sum(a["eps"] - a["div_pct"] / 100 * face for a in t["added"])
        bv_now = v["bv"] + change
        bv_note = (f"book value updated for {', '.join(a['key'] for a in t['added'])}: Rs {v['bv']:,.2f} "
                   f"{'+' if change >= 0 else '-'} Rs {abs(change):,.2f} = Rs {bv_now:,.2f} (estimate until the next balance sheet)")
    snap_now = {**snap, "values": {**v, "bv": bv_now}} if bv_now != v.get("bv") else snap
    fv = fairvalue.compute(snap_now, cost, None if (t["problems"] or probs) else t["ttm"], bvh)
    if bv_now is not None and bv_now <= 0:
        probs.append(f"book value is negative after the latest results (Rs {bv_now:,.2f})")
    return {"fv": fv, "problems": probs + t["problems"], "notes": t["notes"] + ([bv_note] if bv_note else []),
            "quarters": dict(t["quarters"]), "ttm": t["ttm"], "bv_hist": bvh, "bv_now": bv_now}


def watch_detail(sym: str, snap: dict, ver: dict, meetings: list[dict], session: str) -> dict:
    h = _history_detail(sym)
    v = snap["values"]
    probs = list(ver["problems"])
    if h.get("last_close") and v.get("price") and abs(h["last_close"] / v["price"] - 1) > 0.01:
        probs.append(f"price history ends at {h['last_close']:,.2f} on {h['last_date']}, snapshot shows {v['price']:,.2f}")
    lv = levels(h.get("closes") or [v.get("price")], ver.get("bv_now", v.get("bv")))
    nxt = int(session[5:7]) % 12 + 1
    ss = season(h.get("monthly") or {})
    ign = ignition(h.get("values") or [], h.get("chg"))
    bm = sorted(f"{b['date']:%d %b %Y}" for b in meetings
                if (b.get("company_code") or "").strip() == sym and b.get("date") and f"{b['date']:%Y-%m-%d}" >= session)
    bv = ver.get("bv_now", v.get("bv"))
    fv = ver["fv"]
    flags = trap(ver["bv_hist"], ver["quarters"] or (snap.get("eps_quarters") or {}), v.get("tde"))
    has_losses = any("losses" in f for f in flags)
    for f in fv.get("flags", []):
        if "debt" in f:                      # trap() already reports debt to equity
            continue
        if "loss-making" in f and has_losses:  # trap() already reports the losses
            continue
        flags.append(f)
    return {"sym": sym, "name": snap["name"], "sector": snap["sector"], "price": v.get("price"), "bv": bv,
            "bv_period": snap.get("period", {}).get("bv"),
            "asset_zone": (bv * 0.8, bv * 1.2) if bv and bv > 0 else None, "levels": lv, "fv": fv,
            "season_next": ss.get(nxt), "season_month": nxt, "season_now": ss.get(int(session[5:7])),
            "ignition": ign, "bv_hist": ver["bv_hist"], "flags": list(dict.fromkeys(flags)),
            "meeting": bm[0] if bm else None, "since": h.get("first"), "problems": probs, "notes": ver["notes"],
            "ttm": ver["ttm"]}


def zone_state(w: dict) -> str:
    """inside / above / below / broken (below the break level) / none."""
    az, p = w.get("asset_zone"), w.get("price")
    if not az or not p:
        return "none"
    if p < az[0] * 0.9:
        return "broken"
    return "inside" if az[0] <= p <= az[1] else ("above" if p > az[1] else "below")


def radar(cfg: dict, session: str, policy_rate: float | None, watch: list[str] | None = None) -> dict:
    """All liquid stocks screened quickly; everything that will be SHOWN is fully verified first.
    Returns the asset-backed list, today's tested triggers and the watch-stock details."""
    from concurrent.futures import ThreadPoolExecutor

    from . import checks, earnings, fairvalue
    from .common import log
    from .sources import scs, scs_deep
    conf = ((cfg.get("lab") or {}).get("underdog") or {})
    watch = [s.upper() for s in (watch if watch is not None else conf.get("watch", ["STCL"]))]
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    cand = sorted({c for c, r in act.items() if (r.get("trading_close") or 0) * (r.get("trading_vol") or 0) >= 0.5e6} | set(watch))
    with ThreadPoolExecutor(4) as ex:
        snaps = {s: sn for s, sn in zip(cand, ex.map(scs_deep.snapshot, cand)) if sn}
    cost = fairvalue.coe(cfg, policy_rate)
    rows, skipped = [], {}
    for s, sn in snaps.items():
        v = sn["values"]
        close = (act.get(s) or {}).get("trading_close")
        probs = checks.basic(sn, close)
        if probs:
            skipped[s] = probs[0]
            continue
        keys, ok = checks.last4(sn.get("eps_quarters") or {})
        quick_ttm = sum(sn["eps_quarters"][k] for k in keys) if ok and len(keys) == 4 else None
        fv = fairvalue.compute(sn, cost, quick_ttm, None)
        rows.append({"sym": s, "name": sn["name"], "sector": sn["sector"], "price": v.get("price"), "mcap": v.get("mktcap") or 0,
                     "adtv": (v.get("avgvol") or 0) * (v.get("price") or 0), "pb": fv.get("pb"), "fv": fv,
                     "chg": (act.get(s) or {}).get("trading_change"), "close": close})
    log.info("underdog radar: %d snapshots of %d candidates, %d passed the basic checks", len(snaps), len(cand), len(rows))
    rows.sort(key=lambda r: -r["mcap"])
    for i, r in enumerate(rows):
        r["large"] = i < 30
    gaps = sorted(r["fv"]["epv_gap"] for r in rows if r["fv"].get("epv_gap") is not None)
    for r in rows:
        u = r["fv"].get("epv_gap")
        r["upside_rank"] = (sum(x <= u for x in gaps) / len(gaps) * 100) if u is not None and gaps else None
    small = [r for r in rows if not r["large"] and r["adtv"] >= UD_ADTV]
    cands = sorted([r for r in small if r["pb"] and 0 < r["pb"] <= 1.2],
                   key=lambda r: -(r["fv"].get("epv_gap") if r["fv"].get("epv_gap") is not None else -999))
    want = int(conf.get("max_list", 10))
    backed = []
    for r in cands[: want * 3]:
        if len(backed) >= want:
            break
        ver = verify(r["sym"], snaps[r["sym"]], r["close"], cost)
        if ver["problems"]:
            skipped[r["sym"]] = ver["problems"][0]
            continue
        backed.append({**r, "fv": ver["fv"], "notes": ver["notes"]})
    # today's tested trigger: good results + up 3% + P/B <= 1.2 (results announced today or the session before)
    res = scs.results()
    days = sorted({f"{x['date']:%Y-%m-%d}" for x in res if x.get("date")})
    recent = days[-2:] if days and days[-1] == session else days[-1:]
    by_sym = {r["sym"]: r for r in small}
    triggers = []
    for a in res:
        sym = (a.get("company_code") or "").strip()
        if not a.get("date") or f"{a['date']:%Y-%m-%d}" not in recent or sym not in by_sym:
            continue
        ass = earnings.assess(sym, a.get("bm_quarter_number") or "")
        if not ass or ass["cls"] not in earnings.GOOD:
            continue
        r = by_sym[sym]
        px, chg = r["price"], r["chg"]
        react = chg / (px - chg) * 100 if px and chg is not None and px - chg else None
        if react is None or react < 3 or not r["pb"] or r["pb"] > 1.2:
            continue
        ver = verify(sym, snaps[sym], r["close"], cost)
        if ver["problems"]:
            skipped[sym] = ver["problems"][0]
            continue
        triggers.append({**r, "fv": ver["fv"], "label": ass["label"], "eps_line": earnings.eps_line(ass), "react": react,
                         "ver": ver})
    meetings = scs.board_meetings()
    details = []
    for w in watch + [t["sym"] for t in triggers if t["sym"] not in watch]:
        if w not in snaps:
            continue
        try:
            row = next((r for r in rows if r["sym"] == w), None)
            ver = next((t["ver"] for t in triggers if t["sym"] == w), None) or verify(
                w, snaps[w], (act.get(w) or {}).get("trading_close"), cost)
            det = watch_detail(w, snaps[w], ver, meetings, session)
            det["rank"] = row.get("upside_rank") if row else None
            det["trigger"] = any(t["sym"] == w for t in triggers)
            details.append(det)
        except Exception:  # noqa: BLE001
            log.exception("underdog detail %s failed", w)
    for s, why in skipped.items():
        log.info("not shown, data check: %s: %s", s, why)
    return {"session": session, "coe": cost, "n": len(rows), "n_small": len(small), "triggers": triggers,
            "backed": backed, "n_backed": len(cands), "watch": details, "rows": rows, "skipped": skipped,
            "snaps": snaps, "act": act}


def details_for(cfg: dict, session: str, policy_rate: float | None, syms: list[str]) -> list[dict]:
    """On-demand: verified one-page details for the given stocks only (no market-wide scan)."""
    from . import fairvalue
    from .common import log
    from .sources import scs, scs_deep
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    cost = fairvalue.coe(cfg, policy_rate)
    meetings = scs.board_meetings()
    out = []
    for s in syms:
        sn = scs_deep.snapshot(s)
        if not sn:
            log.warning("on-demand: no snapshot for %s", s)
            continue
        ver = verify(s, sn, (act.get(s) or {}).get("trading_close"), cost)
        det = watch_detail(s, sn, ver, meetings, session)
        det["rank"], det["trigger"] = None, False
        out.append(det)
    return out
