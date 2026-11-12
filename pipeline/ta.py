"""Chart reading the way experienced technical analysts do it, on adjusted daily prices (point in time).

What the professionals look at (Weinstein stages, Minervini trend template and VCP, O'Neil / Morales pocket
pivots and distribution days, Wyckoff springs and upthrusts, relative strength lines), turned into formulas:

  TREND / STAGE   stage 2 = price > 50 > 150 > 200-day average, 200-day rising for a month, price >= 30% above
                  its 52-week low and within 25% of its 52-week high, relative strength in the top 30% of liquid
                  stocks (Minervini template). Stage 4 = the mirror image (falling averages, price below them).
                  Stage 1 / 3 = in between (basing or topping), told apart by the 200-day slope.
  BASE (VCP)      the last 40 sessions are a base: range <= 25% deep, recent volatility (10 sessions) under 60%
                  of the 50-session level (contraction), volume of the last 10 sessions under 75% of the 50-session
                  average (dry-up), price within 6% of the base high.
  BREAKOUT        close above the 40-session high on 1.5x+ normal value, from a base, in stage 2.
  VOLUME          up/down volume ratio (50 sessions: value on up days / value on down days), distribution days
                  (down 1%+ on more value than the day before) in the last 20 sessions, pocket pivot (an up day
                  whose value beats every down day of the last 10, above the 50-day average).
  WYCKOFF         spring = the low undercuts the 60-session low but the close recovers above it (from a range
                  <= 30% deep); upthrust = the high pokes above the 60-session high but the close falls back
                  below it, in the lower half of the day's range, on 1.5x+ value.
  RELATIVE        RS line = price / KSE-100; "RS leads" = the RS line at a 250-session high while the price is
  STRENGTH        still 3-15% below its own high (big money buying ahead of the breakout).
  SUPPORT /       swing levels (levels.py) and the 50 / 200-day averages.
  RESISTANCE

Every read is tested on PSX 2012-2026 against similar stocks (same size group, same days), after costs, in
both halves (backtest.SPLIT) before it is called an edge. TESTED holds the results (filled by the test).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Results of the 2012-2026 test (scratch ta_test.py, 13 Nov 2026): liquid stocks (Rs 2m+ a day), entry next open,
# versus same-size liquid stocks on the same days, after costs, both halves (2012-22 / 2023-26).
#   ON THEIR OWN (60 sessions): stage 2 entry +0.9% (median -5.5%), base breakout (VCP) 110 cases, halves +6.1 / -2.6,
#   pocket pivot +0.9% (median -3.3%), accumulation in stage 2 +0.8%, RS leads ~0, upthrust and distribution
#   cluster not negative -> no edge or weak. Two RELIABLE WARNINGS: entering stage 4 -1.1% over 60 sessions
#   (large caps -1.6%, both halves negative, 5 of 15 years positive) and the Wyckoff spring -1.5% over 20
#   sessions (0 of 15 years positive): on PSX an undercut of support usually keeps going.
#   AS A FILTER ON RESULTS (good results + up 3% on the day, mid/small caps, 60 sessions): all +4.1% (n 371);
#   in stage 2 +8.1% (n 125, halves +4.5 / +12.7); RS top 30% +6.5% (n 177, 9 of 12 years, +4.4 / +9.3);
#   above the 200-day +5.0%; RS bottom half -0.3%; below the 200-day +0.1%. => the chart CONFIRMS or CANCELS
#   a results signal; on its own it does not predict.
TESTED: dict[str, str] = {
    "confirm": "tested: good results with a strong chart (stage 2 or relative strength top 30%) +6.5% to +8.1% vs similar "
               "stocks over 3 months, against +4.1% for all",
    "cancel": "tested: good results with a weak chart (relative strength bottom half or below the 200-day) about 0%",
    "stage4": "tested: entering a stage 4 downtrend -1.1% vs similar stocks over 3 months (large caps -1.6%), a reliable warning",
    "spring": "tested: a Wyckoff spring was followed by -1.5% vs similar stocks over a month (0 of 15 years positive)",
    "alone": "on their own, chart patterns (breakouts, stage 2, pocket pivots, accumulation) had no reliable edge on PSX",
}

TA_SIGNALS: dict[str, dict] = {
    "ta_stage2_entry":  {"name": "Enters stage 2 (trend template)", "kind": "long", "gap": 60, "h": 60,
                         "rule": "First day all trend-template conditions hold (none in the last 60 sessions)"},
    "ta_vcp_breakout":  {"name": "Base breakout (VCP) in stage 2", "kind": "long", "gap": 20, "h": 60,
                         "rule": "Close above the 40-session high on 1.5x value, after a tight base with drying volume, in stage 2"},
    "ta_pocket_pivot":  {"name": "Pocket pivot", "kind": "long", "gap": 10, "h": 20,
                         "rule": "Up day on more value than any down day of the last 10, above the 50-day average, in stage 2"},
    "ta_spring":        {"name": "Wyckoff spring", "kind": "long", "gap": 20, "h": 20,
                         "rule": "Low undercuts the 60-session low, close recovers above it, from a range under 30% deep"},
    "ta_rs_leads":      {"name": "Relative strength leads price", "kind": "long", "gap": 20, "h": 60,
                         "rule": "RS line vs KSE-100 at a 250-session high while price is 3-15% below its own high"},
    "ta_accum":         {"name": "Accumulation (up/down volume 1.5x+) in stage 2", "kind": "long", "gap": 20, "h": 60,
                         "rule": "Value on up days 1.5x value on down days over 50 sessions, stage 2"},
    "ta_upthrust":      {"name": "Wyckoff upthrust (false breakout)", "kind": "risk", "gap": 20, "h": 20,
                         "rule": "High above the 60-session high, close back below it in the lower half of the range, 1.5x value"},
    "ta_distribution":  {"name": "Distribution cluster near the high", "kind": "risk", "gap": 20, "h": 20,
                         "rule": "5+ distribution days in 20 sessions while within 10% of the 52-week high"},
    "ta_stage4":        {"name": "Enters stage 4 (downtrend)", "kind": "risk", "gap": 60, "h": 60,
                         "rule": "First day price < 50 < 150 < 200-day average with the 200-day falling"},
}


def features(df: pd.DataFrame, kse: pd.Series | None = None, rs_rank: float | None = None) -> pd.DataFrame:
    """df = signals.features(prices.adjust(raw), meta). Adds the chart-reading columns (all point in time)."""
    df = df.sort_values(["sym", "date"]).reset_index(drop=True)
    g = df.groupby("sym", sort=False)

    def roll(s, n, fn="mean", minp=None):
        r = s.groupby(df["sym"]).rolling(n, min_periods=minp or n)
        return getattr(r, fn)().reset_index(level=0, drop=True)

    ac = df["ac"]
    df["sma150"] = roll(ac, 150, minp=140)
    if "sma50" not in df:
        df["sma50"] = roll(ac, 50)
    if "sma200" not in df:
        df["sma200"] = roll(ac, 200, minp=190)
    df["sma200_up"] = df["sma200"] > g["sma200"].shift(20)
    df["hi250"] = roll(df["ah"], 250, "max", minp=200)
    df["lo250"] = roll(df["al"], 250, "min", minp=200)
    # relative strength rank: weighted 3/6/9/12-month return, percentile among liquid stocks that day
    r = lambda n: ac / g["ac"].shift(n) - 1  # noqa: E731
    df["rs_raw"] = 0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(250)
    if rs_rank is not None:  # one stock read live: its rank among all liquid stocks comes from outside
        df["rs_rank"] = rs_rank
    else:
        liquid = df["adtv20"] >= 2e6
        df["rs_rank"] = df["rs_raw"].where(liquid).groupby(df["date"]).rank(pct=True)
    df["stage2"] = ((ac > df["sma50"]) & (df["sma50"] > df["sma150"]) & (df["sma150"] > df["sma200"])
                    & df["sma200_up"] & (ac >= 1.3 * df["lo250"]) & (ac >= 0.75 * df["hi250"]) & (df["rs_rank"] >= 0.7))
    df["stage4"] = (ac < df["sma50"]) & (df["sma50"] < df["sma150"]) & (df["sma150"] < df["sma200"]) & ~df["sma200_up"]
    # base / volatility contraction / volume dry-up (all from the previous 40-50 sessions)
    ret = g["ac"].pct_change()
    df["vol10"] = roll(ret, 10, "std")
    df["vol50"] = roll(ret, 50, "std")
    df["val10"] = roll(df["val"], 10)
    df["val50"] = roll(df["val"], 50)
    df["hi40"] = roll(df["ah"], 40, "max")
    df["lo40"] = roll(df["al"], 40, "min")
    df["base"] = (((df["hi40"] - df["lo40"]) / df["hi40"] <= 0.25) & (df["vol10"] < 0.6 * df["vol50"])
                  & (df["val10"] < 0.75 * df["val50"]) & (ac >= 0.94 * df["hi40"]))
    prev_hi40 = g["hi40"].shift(1)
    df["vcp_breakout"] = (df["stage2"] & (g["base"].shift(1).astype(float).fillna(0.0) > 0)
                          & (ac > prev_hi40) & (df["vx"] >= 1.5))
    # volume behaviour
    up = df["chg"] > 0
    df["upval50"] = roll(df["val"].where(up, 0.0), 50)
    df["dnval50"] = roll(df["val"].where(df["chg"] < 0, 0.0), 50)
    df["ud_ratio"] = df["upval50"] / df["dnval50"].replace(0, np.nan)
    dist = (df["chg"] <= -0.01) & (df["val"] > g["val"].shift(1))
    df["dist20"] = roll(dist.astype(float), 20, "sum")
    maxdown = roll(df["val"].where(df["chg"] < 0, 0.0), 10, "max")
    df["pocket"] = up & (df["val"] > maxdown.groupby(df["sym"]).shift(1)) & (ac > df["sma50"])
    # Wyckoff spring / upthrust against the previous 60 sessions
    lo60 = g["al"].shift(1).groupby(df["sym"]).rolling(60, min_periods=55).min().reset_index(level=0, drop=True)
    hi60 = g["ah"].shift(1).groupby(df["sym"]).rolling(60, min_periods=55).max().reset_index(level=0, drop=True)
    rng60 = (hi60 - lo60) / hi60
    df["spring"] = (rng60 <= 0.30) & (df["al"] < lo60) & (df["al"] >= 0.95 * lo60) & (ac > lo60)
    pos = df["pos"] if "pos" in df else 0.5
    df["upthrust"] = (df["ah"] > hi60) & (ac < hi60) & (pos < 0.5) & (df["vx"] >= 1.5)
    # relative strength line vs the KSE-100
    if kse is not None and len(kse):
        k = df["date"].map(kse)
        df["rsl"] = ac / k
        rsl_hi = roll(df["rsl"], 250, "max", minp=200)
        df["rs_leads"] = (df["rsl"] >= rsl_hi * 0.999) & (ac <= 0.97 * df["hi250"]) & (ac >= 0.85 * df["hi250"])
    else:
        df["rs_leads"] = False
    return df


def detect(df: pd.DataFrame) -> dict[str, pd.Series]:
    g = df.groupby("sym", sort=False)
    first = lambda m, n: m & ~(m.astype(float).groupby(df["sym"]).transform(  # noqa: E731
        lambda s: s.shift(1).rolling(n, min_periods=1).max()).fillna(0).astype(bool))
    return {
        "ta_stage2_entry": first(df["stage2"].fillna(False), 60),
        "ta_vcp_breakout": df["vcp_breakout"].fillna(False),
        "ta_pocket_pivot": (df["pocket"] & df["stage2"]).fillna(False),
        "ta_spring": df["spring"].fillna(False),
        "ta_rs_leads": df["rs_leads"].fillna(False),
        "ta_accum": ((df["ud_ratio"] >= 1.5) & df["stage2"]).fillna(False),
        "ta_upthrust": df["upthrust"].fillna(False),
        "ta_distribution": ((df["dist20"] >= 5) & (df["ac"] >= 0.9 * df["hi250"])).fillna(False),
        "ta_stage4": first(df["stage4"].fillna(False), 60),
    }


def stage_label(row) -> str:
    if row.get("stage2"):
        return "Stage 2 (advancing)"
    if row.get("stage4"):
        return "Stage 4 (declining)"
    up = row.get("sma200_up")
    return "Stage 3 (topping)" if up and row["ac"] < row.get("sma50", row["ac"]) else (
        "Stage 1 (basing)" if not up else "Between stages")


# ---------------------------------------------------------------- live: one stock's chart, read in words
def rs_ranks(session: str | None = None) -> dict[str, float]:
    """Relative-strength rank (0-1) of every liquid stock on the latest session of the swing price database."""
    import json

    from . import prices
    from .signals import features as sig_features
    if not prices.LIVE_PATH.exists():
        return {}
    raw = prices.load(prices.LIVE_PATH)
    try:
        meta = json.loads(prices.META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    d = sig_features(prices.adjust(raw), meta)
    g = d.groupby("sym", sort=False)
    r = lambda n: d["ac"] / g["ac"].shift(n) - 1  # noqa: E731
    d["rs_raw"] = 0.4 * r(63) + 0.2 * r(126) + 0.2 * r(189) + 0.2 * r(250)
    last = d[d["date"] == d["date"].max()]
    last = last[last["adtv20"] >= 2e6]
    return last.set_index("sym")["rs_raw"].rank(pct=True).to_dict()


def read(px: list[tuple], idx: dict[str, float], rs_rank: float | None = None) -> dict:
    """px rows (date, open, close, volume, high, low), oldest first, adjusted for splits (lab._history).
    Returns the chart read: stage, base, volume behaviour, relative strength, recent events, as facts."""
    if len(px) < 210:
        return {}
    d = pd.DataFrame(px, columns=["date", "o", "c", "v", "h", "l"][:len(px[0])])
    d["date"] = pd.to_datetime(d["date"])
    d["sym"] = "X"
    d["ac"] = d["c"].astype(float)
    hi = pd.to_numeric(d.get("h", d["c"]), errors="coerce")
    lo = pd.to_numeric(d.get("l", d["c"]), errors="coerce")
    d["ah"] = hi.where(hi > 0, d["ac"]).fillna(d["ac"])   # no-trade days come with a high / low of 0
    d["al"] = lo.where(lo > 0, d["ac"]).fillna(d["ac"])
    d["val"] = d["ac"] * d["v"].fillna(0).astype(float)
    d["chg"] = d["ac"].pct_change()
    d["adtv20"] = d["val"].rolling(20, min_periods=15).mean()
    d["vx"] = d["val"] / d["val"].shift(1).rolling(20, min_periods=15).mean()
    rng = (d["ah"] - d["al"]).replace(0, np.nan)
    d["pos"] = ((d["ac"] - d["al"]) / rng).fillna(1.0)
    d["sma50"] = d["ac"].rolling(50).mean()
    d["sma200"] = d["ac"].rolling(200, min_periods=190).mean()
    kse = pd.Series({pd.Timestamp(k): v for k, v in idx.items()}) if idx else None
    d = features(d, kse, rs_rank if rs_rank is not None else np.nan)
    last = d.iloc[-1]
    recent = d.iloc[-10:]
    ev = []
    for col, txt in (("spring", "Wyckoff spring (undercut support, closed back above)"),
                     ("upthrust", "Wyckoff upthrust (false breakout above resistance)"),
                     ("vcp_breakout", "base breakout on heavy value"), ("pocket", "pocket pivot (up day beating every recent down day's value)")):
        hit = recent[recent[col].fillna(False).astype(bool)]
        if len(hit):
            ev.append(f"{txt} on {hit['date'].iloc[-1]:%d %b}")
    out = {"stage": stage_label(last.to_dict()), "stage2": bool(last["stage2"]), "stage4": bool(last["stage4"]),
           "sma50": last["sma50"], "sma150": last["sma150"], "sma200": last["sma200"], "sma200_up": bool(last["sma200_up"]),
           "from_high": (last["ac"] / last["hi250"] - 1) * 100 if last["hi250"] else None,
           "from_low": (last["ac"] / last["lo250"] - 1) * 100 if last["lo250"] else None,
           "base": bool(last["base"]), "base_depth": (last["hi40"] - last["lo40"]) / last["hi40"] * 100 if last["hi40"] else None,
           "contraction": last["vol10"] / last["vol50"] if last["vol50"] else None,
           "dryup": last["val10"] / last["val50"] if last["val50"] else None,
           "ud_ratio": last["ud_ratio"], "dist20": int(last["dist20"]) if pd.notna(last["dist20"]) else None,
           "rs_rank": rs_rank, "rs_leads": bool(last.get("rs_leads", False)),
           "rsl_from_high": None, "events": ev, "price": float(last["ac"])}
    if "rsl" in d and d["rsl"].notna().sum() > 200:
        out["rsl_from_high"] = (last["rsl"] / d["rsl"].iloc[-250:].max() - 1) * 100
    return out


def lines(r: dict) -> list[str]:
    """The chart read in plain sentences (each a fact; the evidence label is added by the card)."""
    if not r:
        return []
    L = []
    f = lambda x: f"{x:,.2f}"  # noqa: E731
    trend = (f"{r['stage']}: price {f(r['price'])}, 50 / 150 / 200-day averages {f(r['sma50'])} / {f(r['sma150'])} / "
             f"{f(r['sma200'])}, 200-day {'rising' if r['sma200_up'] else 'falling'}")
    if r.get("from_high") is not None:
        trend += f"; {abs(r['from_high']):.0f}% below the 52-week high"
    if r.get("from_low") is not None:
        trend += f", {r['from_low']:.0f}% above the 52-week low"
    L.append(trend)
    if r.get("base"):
        L.append(f"Building a tight base: 40-day range {r['base_depth']:.0f}% deep, volatility at {r['contraction'] * 100:.0f}% "
                 f"of normal, value traded at {r['dryup'] * 100:.0f}% of normal (sellers drying up)")
    elif r.get("contraction") is not None:
        L.append(f"No tight base: volatility at {r['contraction'] * 100:.0f}% of normal, value traded at "
                 f"{r['dryup'] * 100:.0f}% of normal")
    if r.get("ud_ratio") == r.get("ud_ratio") and r.get("ud_ratio") is not None:
        who = "buyers in control" if r["ud_ratio"] >= 1.3 else ("sellers in control" if r["ud_ratio"] <= 0.77 else "balanced")
        L.append(f"Volume: value on up days is {r['ud_ratio']:.2f}x value on down days over 50 sessions ({who}); "
                 f"{r['dist20']} distribution day(s) in the last 20")
    rs = []
    if r.get("rs_rank") is not None and r["rs_rank"] == r["rs_rank"]:
        rs.append(f"stronger than {r['rs_rank'] * 100:.0f}% of liquid stocks over the past year")
    if r.get("rsl_from_high") is not None:
        rs.append("RS line vs KSE-100 at a 1-year high" if r["rsl_from_high"] > -0.5 else
                  f"RS line vs KSE-100 {abs(r['rsl_from_high']):.0f}% below its 1-year high")
    if rs:
        L.append("Relative strength: " + "; ".join(rs) + (" (leading the price)" if r.get("rs_leads") else ""))
    if r.get("events"):
        L.append("Last 10 sessions: " + "; ".join(r["events"]))
    return L


def verdicts(r: dict, results_good: bool) -> list[tuple[int, str]]:
    """Votes from the chart, ONLY the tested ones: confirmation or cancellation of good results, stage 4 and
    springs as warnings. (score, sentence)."""
    if not r:
        return []
    out = []
    rs = r.get("rs_rank")
    rs_ok = rs is not None and rs == rs
    above200 = r.get("sma200") and r.get("price") and r["price"] > r["sma200"]
    if results_good:
        if r.get("stage2") or (rs_ok and rs >= 0.7):
            out.append((1, "chart confirms the results: " + ("stage 2 uptrend" if r.get("stage2") else
                                                             f"relative strength top {100 - rs * 100:.0f}%")
                        + f" ({TESTED['confirm']})"))
        elif (rs_ok and rs < 0.5) or (r.get("sma200") and not above200):
            out.append((-1, "chart does not confirm the results: " + (f"relative strength bottom half" if rs_ok and rs < 0.5
                                                                      else "price below its 200-day average")
                        + f" ({TESTED['cancel']})"))
    if r.get("stage4"):
        out.append((-1, f"stage 4 downtrend ({TESTED['stage4']})"))
    if any(e.startswith("Wyckoff spring") for e in r.get("events") or []):
        out.append((-1, f"recent Wyckoff spring ({TESTED['spring']})"))
    return out
