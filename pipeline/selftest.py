"""Self-test: every Research Lab formula against answers worked out by hand, plus live data checks.
Run before each Lab run (`python -m pipeline selftest`); if anything fails, the Lab sends nothing."""
from __future__ import annotations

import math


def _close(a, b, tol=1e-3) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=tol, abs_tol=tol)


def formulas() -> list[str]:
    """Returns the failures (empty = all correct)."""
    from . import checks, fairvalue, underdog
    bad = []

    def ok(name, cond):
        if not cond:
            bad.append(name)

    # fair value: price 100, book 50, trailing EPS 10 -> ROE 20%; payout 50% -> g = min(10%, 8% cap, 17%-4%) = 8%
    snap = {"sector": "CEMENT", "values": {"price": 100.0, "bv": 50.0, "ear": 40.0, "pr": 50.0, "d": 6.0},
            "period": {"ear": "to 2026 4Q", "bv": "to 2027 1Q"}}
    f = fairvalue.compute(snap, 0.17, 10.0, {"2026": 48.0})
    ok("EPV = 10 / 0.17 = 58.82", _close(f["methods"]["Earnings power"], 58.8235))
    ok("RI = 50 x (0.20 - 0.08) / (0.17 - 0.08) = 66.67", _close(f["methods"]["Residual income"], 66.6667))
    ok("DDM = 6 x 1.08 / 0.09 = 72", _close(f["methods"]["Dividend discount"], 72.0))
    ok("fair = median(58.82, 66.67, 72) = 66.67", _close(f["fair"], 66.6667))
    ok("gap = 66.67 / 100 - 1 = -33.3%", _close(f["upside"], -33.333))
    ok("assets = 48 / 0.40 = 120", _close(f["assets"], 120.0))
    ok("liabilities = 120 - 48 = 72", _close(f["liabilities"], 72.0))
    ok("liabilities % = 60", _close(f["liab_pct"], 60.0))
    ok("growth = 8%", _close(f["g"], 0.08))
    f2 = fairvalue.compute({**snap, "values": {**snap["values"], "pr": 20.0}}, 0.17, 10.0, {})
    ok("no DDM when payout < 40%", "Dividend discount" not in f2["methods"])
    f3 = fairvalue.compute(snap, 0.17, -2.0, {})
    ok("loss-maker: no fair value", f3.get("fair") is None)
    ok("cost of equity = 12 + 5 = 17%", _close(fairvalue.coe({}, 12.0), 0.17))
    # quarters must be consecutive
    ok("consecutive quarters", checks.last4({"2025 Q4": 1, "2026 Q1": 1, "2026 Q2": 1, "2026 Q3": 1})[1])
    ok("gap in quarters detected", not checks.last4({"2025 Q3": 1, "2026 Q1": 1, "2026 Q2": 1, "2026 Q3": 1})[1])
    # underdog levels: closes 1..1000; floor = 10th percentile of closes 250..999 = 250 + 0.1 x 749 = 324.9
    lv = underdog.levels([float(x) for x in range(1, 1001)], 400.0)
    ok("history floor = 324.9", _close(lv["hf"], 324.9))
    ok("zone = 324.9 to 420", _close(lv["zone_low"], 324.9) and _close(lv["zone_high"], 420.0))
    ok("break = 324.9 x 0.9 = 292.41", _close(lv["brk"], 292.41))
    ok("price 1000 is 138.1% above the zone", _close(lv["vs_zone"], 138.095))
    ok("price / book = 2.5", _close(lv["pb"], 2.5))
    # season: Feb returns +10% (2020) and +20% (2021) -> average 15%, up 2 of 2
    ss = underdog.season({"2020-01": 100, "2020-02": 110, "2021-01": 100, "2021-02": 120})
    ok("season Feb average +15%", _close(ss[2]["avg"], 15.0) and ss[2]["up"] == 2 and ss[2]["n"] == 2)
    # ignition: today 40 vs 60-day average 10 -> 4x, up 5% -> on
    ig = underdog.ignition([10.0] * 60 + [40.0], 0.05)
    ok("ignition 4x and on", _close(ig["x"], 4.0) and ig["on"])
    ok("no ignition when price falls", not underdog.ignition([10.0] * 60 + [40.0], -0.01)["on"])
    tr = underdog.trap({"2025": 10.0, "2026": 8.0}, {"2026 Q1": -0.5, "2026 Q2": -0.2, "2026 Q3": 0.1, "2026 Q4": -0.4}, 250.0)
    ok("trap flags: book -20%, losses, debt 2.5x", len(tr) == 3 and "20%" in tr[0])
    return bad


def scoring() -> list[str]:
    """Scoring of a call: entry next session's open, exit close after h sessions, minus the KSE-100."""
    from . import lab
    px = [(f"2026-01-{d:02d}", 100.0 + d, 100.0 + d, 0) for d in range(1, 31)]
    idx = {p[0]: 1000.0 for p in px}
    st = {"calls": [{"id": "x", "sym": "T", "date": "2026-01-01", "view": "Outperform"}]}
    lab.score(st, idx, {"T": px})
    c = st["calls"][0]
    # entry = open of 01-02 = 102; after 5 sessions close of 01-06 = 106 -> +3.92%; index flat
    bad = []
    if not _close(c.get("r5"), round((106 / 102 - 1) * 100, 2), 1e-2):
        bad.append(f"5-session score {c.get('r5')} (expected 3.92)")
    if not _close(c.get("r20"), round((121 / 102 - 1) * 100, 2), 1e-2):
        bad.append(f"20-session score {c.get('r20')} (expected 18.63)")
    return bad


def live() -> list[str]:
    """The sources still look the way the code expects (a changed website is caught here)."""
    from . import checks
    from .sources import scs, scs_deep
    bad = []
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    for sym in ("FFC", "OGDC"):
        sn = scs_deep.snapshot(sym)
        if not sn:
            bad.append(f"{sym}: snapshot could not be read")
            continue
        for k in ("price", "bv", "pe", "roe"):
            if sn["values"].get(k) is None:
                bad.append(f"{sym}: snapshot field '{k}' missing (page layout changed?)")
        probs = checks.basic(sn, (act.get(sym) or {}).get("trading_close"))
        bad += [f"{sym}: {p}" for p in probs]
        t = checks.trailing(sym, sn)
        if t["ttm"] is None:
            bad.append(f"{sym}: trailing EPS could not be built ({'; '.join(t['problems'])})")
        if not scs_deep.book_values(sym):
            bad.append(f"{sym}: book value history missing")
    return bad


def run(with_live: bool = True) -> list[str]:
    from .common import log
    bad = [f"formula: {x}" for x in formulas()] + [f"scoring: {x}" for x in scoring()]
    if with_live:
        bad += [f"data: {x}" for x in live()]
    for b in bad:
        log.error("SELFTEST FAILED: %s", b)
    log.info("selftest: %s", "all checks passed" if not bad else f"{len(bad)} failure(s)")
    return bad
