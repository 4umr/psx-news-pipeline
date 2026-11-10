"""Round-3 rules: what the results season and the dividend calendar tell us in advance.

All three use dates that are public before the event: PSX board meetings are announced at least
a week ahead and book closures (ex-dates) two weeks ahead. Like the earlier rounds, every rule is
designed on 2018-2022 and must also work on 2023-2026 before it is switched on.

- peer_results      sector peers that already reported this season mostly posted strong growth
                    -> flagged 5 sessions before the company's own results (same quarter, same costs
                    and prices across a sector: cement, banks, fertiliser, OMCs, textiles)
- peer_results_risk the same when the peers mostly reported falling profits or losses
- div_runup         cash dividend coming: buy 4 sessions before the ex-date, sell the close before it
- div_capture       the same entry, held a week past the ex-date (adjusted returns include the dividend)
- runup_momentum    stock already up 15%+ in the 20 sessions before results day ("sell the news"?)
- peer_spillover    the day a sector's early reporters turn out strong: its stocks still to report
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import earnings

# First test (2018-2026, Nov 2026): peer_results and peer_results_risk showed nothing; the dividend rules
# earned about +0.4% vs similar stocks over the ex-date week (real, but below our bar after costs);
# runup_risk was NOT a warning (the run-up continued: kept as a long candidate, not live); peer_spillover
# came out the OPPOSITE of the idea: the laggards LAGGED similar stocks by about 1.4% over 2 weeks in
# both halves (t -4), so it is kept as a warning. Verdicts are re-checked every Saturday.
EDGE_SIGNALS: dict[str, dict] = {
    "peer_results":      {"name": "Sector peers reported strong results", "kind": "long", "h": 20, "offset": 5,
                          "rule": "Results due within 5 sessions; at least 2 sector peers reported this season and 2 in 3 "
                                  "posted EPS up 50%+ (or back to profit)"},
    "peer_results_risk": {"name": "Sector peers reported weak results", "kind": "risk", "h": 4, "offset": 5,
                          "rule": "Results due within 5 sessions; at least 2 sector peers reported this season and 2 in 3 "
                                  "posted EPS down 50%+ (or a loss)"},
    "div_runup":         {"name": "Run-up into a cash dividend", "kind": "long", "h": 4, "offset": 5,
                          "rule": "Cash dividend of 2%+ of the price; buy 4 sessions before the ex-date, sell the close before it"},
    "div_capture":       {"name": "Cash dividend collected on the ex-date", "kind": "long", "h": 5, "offset": 5,
                          "rule": "Cash dividend of 2%+ of the price; buy 4 sessions before the ex-date, sell at the ex-date close "
                                  "(the dividend is included in the return)"},
    "runup_momentum":    {"name": "Big run-up into results", "kind": "long", "h": 60, "offset": 1,
                          "rule": "Up 15%+ in the 20 sessions before results day, bought at the open of results day"},
    "peer_spillover":    {"name": "Peers reported strong, own results due", "kind": "risk", "h": 10, "live": True,
                          "rule": "The day a sector's reported results this season reach 2+ with 2 in 3 strong: the sector's "
                                  "liquid stocks whose own results are due within 14 days (the sympathy rally tends to fade)"},
}
COLS = ["date", "sym", "si", "c", "chg", "vx", "adtv20", "tier", "ff_pct", "r10"]
SEASON_DAYS = 45       # peers that reported within this many days are reporting the same quarter
PEER_SHARE = 2 / 3
MIN_PEERS = 2
MIN_DIV_YIELD = 0.02   # ex-date reference price cut, as a share of the price
MIN_RUNUP = 0.15


def peer_stats(res: pd.DataFrame, sectors: dict[str, str], lead_days: int = 8) -> pd.DataFrame:
    """For every announcement: how many sector peers reported in the SEASON_DAYS before (lead_days before
    the company's own date, so the information was public at signal time) and the share that were
    strong / weak. Only announcements with a usable year-on-year comparison count as peers."""
    r = res[["sym", "date", "cls"]].copy()
    r["sector"] = r["sym"].map(lambda s: (sectors.get(s) or "").strip().upper())
    r = r[(r["sector"] != "") & r["date"].notna()]
    known = r[r["cls"] != "n/a"]
    by_sector = {s: (sub.sort_values("date")["date"].values, sub.sort_values("date")["cls"].values)
                 for s, sub in known.groupby("sector")}
    n, good, bad = [], [], []
    for sec, d, sym in zip(r["sector"], r["date"], r["sym"]):
        dates, cls = by_sector.get(sec, (np.array([], dtype="datetime64[ns]"), np.array([])))
        end = (d - pd.Timedelta(days=lead_days)).to_datetime64()
        start = (d - pd.Timedelta(days=lead_days + SEASON_DAYS)).to_datetime64()
        i, j = dates.searchsorted(start, side="left"), dates.searchsorted(end, side="right")
        part = cls[i:j]
        # the company's own earlier announcements are never its peers (one row per label, so none expected)
        n.append(len(part))
        good.append(float(np.isin(part, earnings.GOOD).mean()) if len(part) else 0.0)
        bad.append(float(np.isin(part, earnings.BAD).mean()) if len(part) else 0.0)
    out = pd.DataFrame({"peers": n, "peer_good": good, "peer_bad": bad}, index=r.index)
    return out.reindex(res.index).fillna({"peers": 0, "peer_good": 0.0, "peer_bad": 0.0})


def _offset_rows(df: pd.DataFrame, rows: np.ndarray, syms: np.ndarray, offset: int) -> np.ndarray:
    rows = rows - offset
    ok = rows >= 0
    rows, syms = rows[ok], syms[ok]
    return rows[df["sym"].values[rows] == syms]


def ex_date_events(df: pd.DataFrame, res: pd.DataFrame, min_adtv: float) -> pd.DataFrame:
    """Rows 5 sessions before every cash-dividend ex-date of a liquid stock (dividend 2%+ of the price)."""
    x = df[(df["exf"] < 1 - MIN_DIV_YIELD) & (df["exf"] > 0.5) & (df["adtv20"] >= min_adtv)]
    x = x[["date", "sym"]].copy()
    x["row"] = x.index.values  # features() resets the index, so the index is the row position
    ann = res[["sym", "date", "div", "bonus"]].dropna(subset=["date"]).sort_values("date")
    ann = ann.rename(columns={"date": "ann_date"})
    x = x.sort_values("date")
    m = pd.merge_asof(x, ann, left_on="date", right_on="ann_date", by="sym", direction="backward",
                      tolerance=pd.Timedelta(days=75), allow_exact_matches=False)
    cash = (m["div"] > 0) & ~(m["bonus"] > 0)
    out = []
    for name in ("div_runup", "div_capture"):
        rows = _offset_rows(df, m.loc[cash, "row"].values, m.loc[cash, "sym"].values, EDGE_SIGNALS[name]["offset"])
        e = df.iloc[rows][COLS].copy()
        e["signal"] = name
        out.append(e)
    return pd.concat(out) if out else pd.DataFrame()


def spillover_events(df: pd.DataFrame, res: pd.DataFrame, sectors: dict[str, str], min_adtv: float,
                     due_days: int = 14) -> pd.DataFrame:
    """Signal on the day a sector's season tally first turns strong (2+ reported, 2 in 3 strong), for the
    sector's liquid stocks that have not reported yet and whose own results come within `due_days`
    (in live use that is known from the board meeting notice). Once per sector per season."""
    sessions = np.sort(df["date"].unique())
    r = res[["sym", "date", "cls", "after_hours"]].dropna(subset=["date"]).copy()
    r["sector"] = r["sym"].map(lambda s: (sectors.get(s) or "").strip().upper())
    r = r[r["sector"] != ""].sort_values("date")
    # the day the announcement was public by the close
    k = sessions.searchsorted(r["date"].values.astype("datetime64[ns]"), side="right")
    nxt = pd.Series(sessions[np.minimum(k, len(sessions) - 1)], index=r.index)
    r["pub"] = pd.to_datetime(np.where(r["after_hours"], nxt.values, r["date"].values))
    rowid = {(s, d): i for i, (s, d) in enumerate(zip(df["sym"].values, df["date"].values))}
    liquid_row = df["adtv20"].values >= min_adtv
    rows = []
    for sec, sub in r.groupby("sector"):
        known = sub[sub["cls"] != "n/a"]
        if known.empty:
            continue
        kd, kc = known["pub"].values, known["cls"].values
        all_d = sub.groupby("sym")["pub"].apply(lambda s: np.sort(s.values))
        last_fire = None
        for pub in np.sort(known["pub"].unique()):
            if last_fire is not None and (pub - last_fire) < np.timedelta64(SEASON_DAYS, "D"):
                continue
            i, j = kd.searchsorted(pub - np.timedelta64(SEASON_DAYS, "D"), side="right"), kd.searchsorted(pub, side="right")
            part = kc[i:j]
            if len(part) < MIN_PEERS or np.isin(part, earnings.GOOD).mean() < PEER_SHARE:
                continue
            last_fire = pub
            for sym, dates in all_d.items():
                a = dates.searchsorted(pub, side="right")
                reported = a > 0 and (pub - dates[a - 1]) <= np.timedelta64(SEASON_DAYS, "D")
                due = a < len(dates) and (dates[a] - pub) <= np.timedelta64(due_days, "D")
                row = rowid.get((sym, pub))
                if not reported and due and row is not None and liquid_row[row]:
                    rows.append(row)
    e = df.iloc[sorted(set(rows))][COLS].copy()
    e["signal"] = "peer_spillover"
    return e


def events(df: pd.DataFrame, res: pd.DataFrame, sectors: dict[str, str], min_adtv: float) -> pd.DataFrame:
    """All round-3 events, shaped like signals.events()."""
    ps = peer_stats(res, sectors)
    res = pd.concat([res, ps], axis=1)
    r20 = df["ac"] / df.groupby("sym", sort=False)["ac"].shift(20) - 1
    # run-up measured at the close of the signal day = the session before the reaction day (offset 1)
    strong = (res["peers"] >= MIN_PEERS) & (res["peer_good"] >= PEER_SHARE)
    weak = (res["peers"] >= MIN_PEERS) & (res["peer_bad"] >= PEER_SHARE)
    ev = earnings.event_rows(res, df, min_adtv, rules={
        "peer_results": strong,
        "peer_results_risk": weak,
        "runup_momentum": pd.Series(True, index=res.index),
    }, offsets={k: v["offset"] for k, v in EDGE_SIGNALS.items() if v.get("offset")})
    if len(ev):
        ru = ev["signal"] == "runup_momentum"
        ev = ev[~ru | (r20.reindex(ev.index).values >= MIN_RUNUP)]
    ev = pd.concat([ev, ex_date_events(df, res, min_adtv), spillover_events(df, res, sectors, min_adtv)])
    ev["_row"] = ev.index  # two announcements on one day (e.g. FY and Q4) must not double a signal
    return ev.drop_duplicates(["signal", "_row"]).drop(columns="_row")
