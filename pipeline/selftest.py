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

    # fair value: price 100, book 50, trailing EPS 10 -> ROE 20%; payout 50% -> natural growth 10% -> capped at 8%
    # next year's EPS 10.8 -> ROE1 21.6%; RI = 50 x (0.216 - 0.08) / (0.17 - 0.08) = 75.56; no DDM (growth capped)
    snap = {"sector": "CEMENT", "values": {"price": 100.0, "bv": 50.0, "ear": 40.0, "pr": 50.0, "d": 6.0},
            "period": {"ear": "to 2026 4Q", "bv": "to 2027 1Q"}}
    f = fairvalue.compute(snap, 0.17, 10.0, {"2026": 48.0})
    ok("no-growth value = 10 / 0.17 = 58.82 (shown, not averaged)", _close(f["epv"], 58.8235) and "Earnings power" not in f["methods"])
    ok("RI = 50 x (0.216 - 0.08) / 0.09 = 75.56", _close(f["methods"]["Residual income"], 75.5556))
    ok("no DDM when natural growth (10%) is above the cap", "Dividend discount" not in f["methods"])
    ok("fair = 75.56, gap -24.4%", _close(f["fair"], 75.5556) and _close(f["upside"], -24.4444))
    ok("assets = 48 / 0.40 = 120", _close(f["assets"], 120.0))
    ok("liabilities = 120 - 48 = 72", _close(f["liabilities"], 72.0))
    ok("liabilities % = 60", _close(f["liab_pct"], 60.0))
    ok("growth = 8%", _close(f["g"], 0.08))
    # payout 70%: natural growth 6% (not capped) -> E1 10.6, ROE1 21.2%; RI = 50 x 0.152 / 0.11 = 69.09;
    # DDM = 10.6 x 0.7 / 0.11 = 67.45; fair = median = 68.27
    fd = fairvalue.compute({**snap, "values": {**snap["values"], "pr": 70.0}}, 0.17, 10.0, {})
    ok("DDM 67.45 and RI 69.09 -> fair 68.27", _close(fd["methods"].get("Dividend discount"), 67.4545)
       and _close(fd["methods"]["Residual income"], 69.0909) and _close(fd["fair"], 68.2727))
    f2 = fairvalue.compute({**snap, "values": {**snap["values"], "pr": 20.0}}, 0.17, 10.0, {})
    ok("no DDM when payout < 40%", "Dividend discount" not in f2["methods"])
    # ROE 10% below the 17% required: growth 5% LOWERS value: RI = 50 x (0.105 - 0.05) / 0.12 = 22.92,
    # DDM = 5.25 x 0.5 / 0.12 = 21.88, fair 22.40, under the no-growth 29.41
    fl = fairvalue.compute(snap, 0.17, 5.0, {})
    ok("growth below the required return lowers value (22.40 < 29.41)", _close(fl["fair"], 22.3958) and fl["fair"] < fl["epv"])
    f3 = fairvalue.compute(snap, 0.17, -2.0, {})
    ok("loss-maker: no fair value", f3.get("fair") is None)
    ok("cost of equity = 12 + 5 = 17%", _close(fairvalue.coe({}, 12.0), 0.17))
    ok("risk-free = 10-year PIB yield", fairvalue.risk_free({"policy_rate": 12.0, "pib": {"yields": {"10-Y": 12.85}}}) == 12.85)
    ok("risk-free = policy rate without PIB", fairvalue.risk_free({"policy_rate": 11.0}) == 11.0)
    # normalised earnings: a loss now with a 5-year average of 3 -> 3 / 0.17 = 17.65 is the only method
    ann = {"2022": 3.0, "2023": 3.0, "2024": 3.0, "2025": 3.0, "2026": 3.0}
    f4 = fairvalue.compute({**snap, "eps_annual": ann}, 0.17, -2.0, {})
    ok("loss now, 5-year average 3: fair = 17.65", _close(f4.get("fair"), 17.647))
    try:
        fairvalue.lines(f4)
        fairvalue.lines(f)
        ok("the fair value sentences are written", True)
    except Exception as e:  # noqa: BLE001
        ok(f"the fair value sentences are written ({e})", False)
    # a spike: trailing 30 vs average 10 -> normalised 58.82 joins RI 50 x (0.648 - 0.08) / 0.09 = 315.56 -> 187.19
    f5 = fairvalue.compute({**snap, "eps_annual": {k: 10.0 for k in ann}}, 0.17, 30.0, {})
    ok("spike year: median(315.56, 58.82) = 187.19", _close(f5.get("fair"), 187.19))
    f6 = fairvalue.compute({**snap, "eps_annual": {k: 9.0 for k in ann}}, 0.17, 10.0, {"2026": 48.0})
    ok("normal year: normalised not used", "Normalised earnings" not in f6["methods"] and _close(f6["fair"], 75.5556))
    # normalised by ROE: 3 years of EPS 5 on book 50 -> 10% ROE; now 20% (2x) -> normal EPS 0.10 x 50 = 5 -> 29.41
    bvh = {"2022": 50.0, "2023": 50.0, "2024": 50.0}
    f9 = fairvalue.compute({**snap, "eps_annual": {"2022": 5.0, "2023": 5.0, "2024": 5.0}}, 0.17, 10.0, bvh)
    ok("normalised by ROE = 29.41", _close(f9["methods"].get("Normalised earnings"), 29.4118))
    # grown, not abnormal: EPS 4 -> 10 while book grew 20 -> 50 (ROE 20% throughout): no normalisation
    f10 = fairvalue.compute({**snap, "eps_annual": {"2022": 4.0, "2023": 6.0, "2024": 8.0}},
                            0.17, 10.0, {"2022": 20.0, "2023": 30.0, "2024": 40.0})
    ok("a company that grew is not normalised down", "Normalised earnings" not in f10["methods"])
    # 5-year ROE a loss, profit now: book 50 joins RI 75.56 -> 62.78
    f11 = fairvalue.compute({**snap, "eps_annual": {"2022": -1.0, "2023": -1.0, "2024": -1.0}}, 0.17, 10.0, bvh)
    ok("boom year of a loss-maker: book value anchor, fair 62.78", _close(f11["fair"], 62.7778))
    f8 = fairvalue.compute({**snap, "values": {**snap["values"], "pr": None, "d": None}}, 0.17, 10.0, {})
    ok("unknown payout: no growth, RI = no-growth value", list(f8["methods"]) == ["Residual income"]
       and _close(f8["fair"], 58.8235))
    f7 = fairvalue.compute({**snap, "eps_annual": {k: -1.0 for k in ann}}, 0.17, -2.0, {})
    ok("losses now and on average: no fair value", f7.get("fair") is None)
    # growth stage, worked by hand: book 100, ROE 30%, required 17%, growth 20% fading to 8% over 5 years
    # residual income 13.00, 15.60, 18.35, 21.13, 23.84 discounted = 56.11; terminal 292.43 discounted = 133.38
    ok("two-stage residual income = 289.49", _close(fairvalue._ri_two_stage(100, 0.30, 0.17, 0.20, 0.08), 289.4932))
    ok("two-stage with no extra growth = one-stage 75.56", _close(fairvalue._ri_two_stage(50, 0.216, 0.17, 0.08, 0.08), 75.5556))
    h1 = fairvalue.history({"2020": 1, "2021": 1.2, "2022": 1.5, "2023": 1.8, "2024": 2.2, "2025": 2.6}, {})
    ok("steady growth 1 -> 2.6 in 5 years = 21.06% a year", _close(h1["cagr"], 0.21058))
    h2 = fairvalue.history({"2020": 10, "2021": 12, "2022": 14, "2023": 2, "2024": 2.4, "2025": 2.8},
                           {"2022": 100, "2023": 15})
    ok("unadjusted split detected (EPS and book fell together)", h2["split"] and h2["cagr"] is None)
    h3 = fairvalue.history({"2020": 1, "2021": 1.2, "2022": 5, "2023": 6, "2024": 7, "2025": 8}, {})
    ok("boom detected (profit up 4x in a year)", h3["boom"] and h3["cagr"] is None)
    # a steady grower qualifies: book 50, EPS 10 (ROE 20%), payout 20% (fundable growth 16%), shown 21% -> 16%
    fg = fairvalue.compute({**snap, "values": {**snap["values"], "pr": 20.0},
                            "eps_annual": {"2020": 3.8, "2021": 4.6, "2022": 5.6, "2023": 6.8, "2024": 8.2, "2025": 10.0}},
                           0.17, 10.0, {})
    ok("growth stage used: 16% fading to 8%", _close(fg.get("g1"), 0.16) and _close(fg["g"], 0.08)
       and _close(fg["fair"], fairvalue._ri_two_stage(50, 11.6 / 50, 0.17, 0.16, 0.08)))
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


def levels_tests() -> list[str]:
    """Technicals, buy / sell levels, the setup vote, the split adjustment and result wording, by hand."""
    from . import earnings, lab, levels
    bad = []

    def ok(name, cond):
        if not cond:
            bad.append(name)

    import numpy as np
    ok("RSI of a rising series = 100", _close(levels._rsi(np.arange(1, 40, dtype=float)), 100.0))
    ok("RSI of a falling series = 0", _close(levels._rsi(np.arange(40, 1, -1, dtype=float)), 0.0))
    # 10 ups and 4 downs of 1, then a flat day: average gain 10/14, loss 4/14 -> RS 2.5 -> RSI 71.43
    c = np.cumsum([100.0] + [1.0] * 10 + [-1.0] * 4 + [0.0])
    ok("RSI 71.43 worked by hand", _close(levels._rsi(c), 71.4286))
    # weeks run Monday to Friday: 2-6 Nov (last close Friday) and 9-11 Nov (last close Wednesday)
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"]
    wc = levels.weekly_closes([(d, 0, float(i + 1), 0) for i, d in enumerate(days)])
    ok("weekly closes = Friday 6 Nov and Wednesday 11 Nov", wc == [5.0, 8.0])
    # 30 sessions with range 90-110, then 10 sessions 98-104, price 100
    px = [(f"2026-01-{i:02d}", 100.0, 100.0, 0, 110.0, 90.0) for i in range(1, 31)] +          [(f"2026-02-{i:02d}", 100.0, 100.0, 0, 104.0, 98.0) for i in range(1, 11)]
    lv = levels.swing_levels(px, 100.0)
    ok("supports 98 / 90, resistances 104 / 110", (lv["s1"], lv["s2"], lv["r1"], lv["r2"]) == (98.0, 90.0, 104.0, 110.0))
    d = {"snap": {"values": {"price": 100.0}}, "px": px, "fv": {"pb": 0.8, "fair": 150.0, "upside": 50.0, "nav": 125.0}}
    good = {"cls": "big_up", "label": "1Q27", "line": "x"}
    su = levels.setup(d, {"view": "Outperform", "confidence": 4}, {"kind": "backed", "tested": "t"}, good)
    ok("setup: results + value + AI = BUY SIDE (+3)", su["score"] == 3 and su["label"] == "BUY SIDE")
    # the next support 90 is more than 8% under 98, so the risk line is 98 x 0.95 = 93.10; reward to risk (104-100)/(100-93.1)
    ok("risk line 93.10, buy zone 98 to 100.94", _close(su["risk"], 93.1) and _close(su["zone"][1], 100.94))
    ok("reward to risk 0.58", _close(su["rr"], 4 / 6.9))
    d2 = {"snap": {"values": {"price": 100.0}}, "px": px, "fv": {"fair": 60.0, "upside": -40.0}}
    su2 = levels.setup(d2, None, {"kind": "warning", "tested": "t"}, {"cls": "big_down", "label": "1Q27", "line": "x"})
    ok("setup: warning + 40% above fair value = SELL SIDE (-3)", su2["score"] == -3 and su2["side"] == "sell")
    # a 10-for-1 split: 100 -> opens at 10; earlier prices become 10, volume x10
    adj = lab.adjust_jumps([("2026-01-01", 100.0, 100.0, 1000, 101.0, 99.0), ("2026-01-02", 100.0, 100.0, 1000, 101.0, 99.0),
                            ("2026-01-05", 10.0, 10.2, 10000, 10.5, 9.9)])
    ok("split adjustment", _close(adj[0][2], 10.0) and _close(adj[1][4], 10.1) and _close(adj[0][3], 10000))
    ok("a normal 10% day is not adjusted", lab.adjust_jumps([("a", 100.0, 100.0, 1, 0, 0), ("b", 100.0, 110.0, 1, 0, 0)])[0][2] == 100.0)
    long_text = "\n\n".join(f"<b>Stock {i}</b>\n" + "line " * 300 for i in range(12))
    parts = lab.chunks(long_text)
    ok("long Lab message split under Telegram's 4096 limit, nothing lost",
       all(len(x) <= 4096 for x in parts) and len(parts) > 1 and sum(x.count("<b>Stock") for x in parts) == 12)
    rec = lab.scan_record({"calls_scan": [{"date": "2026-11-12", "kind": "backed", "r5": 2.0},
                                          {"date": "2026-11-12", "kind": "backed", "r5": -1.0},
                                          {"date": "2026-11-12", "kind": "warning", "r5": -3.0}]})
    ok("scan record: asset-backed 2, average +0.5%, 50% right; warnings 1, 100% right",
       "asset-backed 2, average +0.5% vs KSE-100, 50% right" in rec and "warnings 1, average -3.0% vs KSE-100, 100% right" in rec)
    from .sources import scs as _scs
    v20 = [{"company_code": f"S{i}", "CurrentPrice": 101.0, "LDCP": 100.0} for i in range(20)]
    ok("all-stock table still yesterday's (shows the previous close) -> not today",
       _scs.activity_is_today([{"company_code": f"S{i}", "trading_close": 100.0} for i in range(20)], v20) is False)
    ok("all-stock table updated after the close -> today",
       _scs.activity_is_today([{"company_code": f"S{i}", "trading_close": 101.0} for i in range(20)], v20) is True)
    ok("too few moving stocks to tell -> unknown", _scs.activity_is_today([], v20[:5]) is None)
    from . import levelwatch as lw
    buy = {"side": "buy", "zone": (98.0, 100.94), "risk": 93.1, "r1": 104.0, "r2": 110.0}
    ok("level alert: inside the buy zone at 100", [e for e, _ in lw.events(buy, 100.0)] == ["in_zone"])
    ok("level alert: above the zone, below target: nothing at 102", lw.events(buy, 102.0) == [])
    ok("level alert: target 1 at 104.5", [e for e, _ in lw.events(buy, 104.5)] == ["target1"])
    ok("level alert: target 2 at 111", [e for e, _ in lw.events(buy, 111.0)] == ["target2"])
    ok("level alert: setup failed at 93", [e for e, _ in lw.events(buy, 93.0)] == ["failed"])
    sell = {"side": "sell", "sell_zone": (5.63, 5.80), "exit": 4.86}
    ok("level alert: sell zone at 5.70", [e for e, _ in lw.events(sell, 5.70)] == ["sell_zone"])
    ok("level alert: support broke at 4.80", [e for e, _ in lw.events(sell, 4.80)] == ["broke"])
    v = [{"company_code": f"S{i}", "CurrentPrice": 101.0, "LDCP": 100.0} for i in range(20)]
    ok("PSX screener live when it shows the live index prices",
       lw.screener_live(v, {f"S{i}": (101.0, 1.0) for i in range(20)}) is True
       and lw.screener_live(v, {f"S{i}": (100.0, 0.0) for i in range(20)}) is False)
    from . import ta
    strong = {"stage2": True, "rs_rank": 0.9, "sma200": 90.0, "price": 100.0, "events": []}
    weak = {"stage2": False, "rs_rank": 0.3, "sma200": 110.0, "price": 100.0, "events": []}
    ok("chart confirms good results (stage 2 / strong RS): +1", [v for v, _ in ta.verdicts(strong, True)] == [1])
    ok("chart cancels good results (weak RS, below 200-day): -1", [v for v, _ in ta.verdicts(weak, True)] == [-1])
    ok("chart alone does not vote without good results", ta.verdicts(strong, False) == [])
    ok("stage 4 and a spring are warnings", [v for v, _ in ta.verdicts({**weak, "stage4": True,
                                              "events": ["Wyckoff spring (x) on 01 Jan"]}, False)] == [-1, -1])
    # a clean uptrend read from prices: 300 sessions rising 0.3% a day -> stage 2 needs RS; with rank 0.9 it is stage 2
    import datetime as _dt
    days = [(_dt.date(2025, 1, 1) + _dt.timedelta(days=i)).isoformat() for i in range(300)]
    pxs = [(d, 100 * 1.003 ** i, 100 * 1.003 ** i, 100000, 100 * 1.003 ** i * 1.01, 100 * 1.003 ** i * 0.99) for i, d in enumerate(days)]
    rd = ta.read(pxs, {d: 1000.0 for d in days}, 0.9)
    ok("steady uptrend with strong RS reads as stage 2", rd.get("stage2") is True and rd.get("stage4") is False)
    from . import patterns as pt
    import numpy as _np
    base_ = [100 * (1 + 0.002 * ((-1) ** i)) for i in range(150)]
    leg = (list(_np.linspace(100, 80, 20)) + list(_np.linspace(80, 90, 10)) + list(_np.linspace(90, 80.5, 10))
           + list(_np.linspace(80.5, 95, 15)))
    cc = _np.array(base_ + leg)
    evs = pt.events(cc, cc * 1.003, cc * 0.997, _np.full(len(cc), 1e5))
    ok("double bottom 80 / 80.5 with a peak at 90 completes on the close above 90",
       any(n == "pt_double_bottom" and cc[i] > 90 >= cc[i - 1] for i, n in evs))
    ok("pattern warnings vote once: head and shoulders top",
       [v for v, _ in pt.verdicts({"recent": [("2026-11-10", "pt_hs_top"), ("2026-11-11", "pt_bull_div")]}, True)] == [-1])
    ok("bull flag inside stage 2 is not a warning", pt.verdicts({"recent": [("d", "pt_bull_flag")]}, True) == [])
    ok("bull flag outside stage 2 is a warning", [v for v, _ in pt.verdicts({"recent": [("d", "pt_bull_flag")]}, False)] == [-1])
    hh = pt.hurst(_np.cumprod(1 + _np.random.default_rng(1).normal(0, 0.01, 400)) * 100)
    ok("Hurst of a random walk is near 0.5", hh is not None and 0.35 < hh < 0.65)
    vp = pt.volume_profile(_np.array([10.0] * 60 + [12.0] * 60), _np.array([1e6] * 60 + [1e5] * 60))
    ok("volume profile: most value traded at 10", abs(vp["poc"] - 10.0) < 0.1)
    from . import levels as _lv
    dz = {"snap": {"values": {"price": 100.0}}, "px": [(f"2026-01-{i:02d}", 100.0, 100.0, 0, 104.0, 98.0) for i in range(1, 31)] * 2,
          "fv": {}, "patterns": {"recent": [("2026-01-30", "pt_hs_top")]}, "chart": {}}
    sz = _lv.setup(dz, None, None, None)
    ok("one chart-only warning reads as MIXED, CAUTION with buy-side levels", sz["label"] == "MIXED, CAUTION" and sz["side"] == "buy")
    from . import watchers as _w
    ok("results filing sent at once", bool(_w.RESULTS_FILING.search("Financial Results for the Quarter Ended 30 Sep 2026")))
    ok("report transmission waits for the batch", not _w.RESULTS_FILING.search("Transmission of Quarterly Report for the Period"))
    from . import analysis as _an
    _keep = _an._model
    _an._model = "main-model"
    _cf = {"ai": {"fallback_model": "lite", "research_model": "lite"}}
    ok("news tries the main model first, research the other one",
       _an.models_for(_cf) == ["main-model", "lite"] and _an.models_for(_cf, True) == ["lite", "main-model"])
    _an._model = _keep
    ln = earnings.eps_line({"annual": True, "eps": 10.0, "eps_ly": 8.0, "eps_q": 3.0, "eps_q_ly": 1.0, "yoy_q": 2.0, "yoy": 0.25})
    ok("annual result line shows the fourth quarter first", ln == "fourth-quarter EPS 3.00 vs 1.00 a year ago (+200%); full year 10.00 vs 8.00")
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
    bad = [f"formula: {x}" for x in formulas()] + [f"scoring: {x}" for x in scoring()] +         [f"levels: {x}" for x in levels_tests()]
    if with_live:
        bad += [f"data: {x}" for x in live()]
    for b in bad:
        log.error("SELFTEST FAILED: %s", b)
    log.info("selftest: %s", "all checks passed" if not bad else f"{len(bad)} failure(s)")
    return bad
