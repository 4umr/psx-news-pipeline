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
    # second view, returns fade: book 100, ROE 30% fading to the 20% required return over 2 years, no growth:
    # year 1 (30% - 20%) x 100 / 1.2 = 8.333; year 2 ROE 25%: 5 / 1.44 = 3.472; value 111.81
    ok("fade, 2 years: 111.81", _close(fairvalue._ri_fade(100.0, 0.30, 0.20, 0.0, 0.0, False, 2), 111.8056))
    # with 10% book growth: year 2 excess return is on a book of 110: 5.5 / 1.44 = 3.819 -> 112.15
    ok("fade with growth: 112.15", _close(fairvalue._ri_fade(100.0, 0.30, 0.20, 0.10, 0.10, False, 2), 112.1528))
    ok("ROE equal to the required return: worth its book", _close(fairvalue._ri_fade(100.0, 0.2, 0.2, 0.08, 0.08, False), 100.0))
    ok("high return: the fade view is below the steady view and above book", 50 < f["fade"] < f["fair"])
    lo = fairvalue.compute({**snap, "values": {**snap["values"], "pr": 50.0}}, 0.17, 5.0, {})  # ROE 10% < 17%
    ok("low return: the fade view is above the steady view and below book", lo["fair"] < lo["fade"] < 50)
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
    # the index leg starts at the index OPEN of the entry day too: it opened at 1010 on 01-02 and closed at 1020 on
    # 01-06 -> market +0.99%; stock +3.92% -> relative +2.93% (the old way, from the 01-01 close of 1000: +1.92%)
    idx2 = {**idx, **{f"2026-01-{d:02d}": 1020.0 for d in range(2, 31)}}
    st2 = {"calls": [{"id": "y", "sym": "T", "date": "2026-01-01", "view": "Outperform"}]}
    lab.score(st2, idx2, {"T": px}, {"2026-01-02": 1010.0})
    if not _close(st2["calls"][0].get("r5"), round((106 / 102 - 1) * 100 - (1020 / 1010 - 1) * 100, 2), 1e-2):
        bad.append(f"5-session score with the index open {st2['calls'][0].get('r5')} (expected 2.93)")
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
    ok("setup: warning -2; the model fair value (40% above) does not vote = LEANING SELL",
       su2["score"] == -2 and su2["side"] == "sell" and any("does not vote" in x for x in su2["info"]))
    # value votes only where tested: a large cap at 0.8x book with good results gets no value vote
    d3 = {"snap": {"values": {"price": 100.0}}, "px": px, "tier": "Large", "fv": {"pb": 0.8, "fair": 150.0, "upside": 50.0}}
    su3 = levels.setup(d3, None, None, good)
    ok("large cap: results vote only (value rules were tested on mid and small caps)", su3["score"] == 1)
    d4 = {"snap": {"values": {"price": 100.0}}, "px": px, "tier": "Small", "fv": {"pb": 2.0, "epv": 160.0, "epv_gap": 60.0}}
    ok("small cap 60% below its no-growth value: tested value vote", levels.setup(d4, None, None, None)["score"] == 1)
    # the buy zone stops below the first target (support 98, resistance 99.5 -> zone top 98.5, not 100.94)
    px5 = px[:-1] + [("2026-02-11", 98.6, 98.6, 0, 99.5, 98.0)]
    su5 = levels.setup({"snap": {"values": {"price": 98.6}}, "px": px5, "fv": {}}, None, None, None)
    ok("buy zone below target 1", su5.get("r1") is None or su5["zone"][1] < su5["r1"])
    from . import checks as _ck, earnings as _ea
    ok("EPS with notes: '12.23 Restated' 12.23, '(0.45)' -0.45, 'B-53.87 D- 31.07' -53.87, '-0..11' -0.11",
       _ea._num("12.23 Restated") == 12.23 and _ea._num("(0.45)") == -0.45 and _ea._num("B-53.87 D- 31.07") == -53.87
       and _ea._num("-0..11") == -0.11)
    ok("sign changes are words, not percentages", _ea.change_word(-0.24, 0.02) == " (turned to a loss)"
       and _ea.change_word(0.44, -0.24) == " (back to profit)" and _ea.change_word(19.72, 17.24) == " (+14%)")
    ok("pre-split EPS is not used next to post-split EPS", _ck.pick_quarter(14.63, 73.17) == 14.63
       and _ck.pick_quarter(16.63, 17.24) == 17.24)
    oo = _ck.oneoff([("2026 Q2", 8.07), ("2026 Q3", 9.82), ("2026 Q4", 29.55), ("2027 Q1", 13.6)])
    ok("one-off quarter: OGDC FY26 Q4", oo and oo["key"] == "2026 Q4" and not oo["latest"] and _close(oo["typical"], 9.82))
    ok("no one-off in a steady year", _ck.oneoff([("a", 15.14), ("b", 13.62), ("c", 15.43), ("d", 19.72)]) is None)
    seas = {"2025 Q1": 1.0, "2025 Q2": 6.0, "2025 Q3": 1.2, "2025 Q4": 0.9}
    ok("a quarter that is big every year is seasonal, not a one-off",
       _ck.oneoff([("2026 Q1", 1.1), ("2026 Q2", 6.5), ("2026 Q3", 1.3), ("2026 Q4", 1.0)], seas) is None)
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
    ok("level alert: closed inside the zone -> no repeat next morning",
       lw.fresh_events({**buy, "close": 99.5}, 100.0) == [])
    ok("level alert: closed above the zone, falls into it -> in_zone",
       [e for e, _ in lw.fresh_events({**buy, "close": 102.0}, 100.0)] == ["in_zone"])
    ok("level alert: closed inside the zone, reaches target 1 -> target1 only",
       [e for e, _ in lw.fresh_events({**buy, "close": 99.5}, 104.5)] == ["target1"])
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
    _keep, _keep_av = _an._model, _an._AVAILABLE
    _an._model, _an._AVAILABLE = "main-model", []          # no live model list: the configured backup is used
    _cf = {"ai": {"fallback_model": "lite"}}
    ok("news tries the main model first, research the other one",
       _an.models_for(_cf) == ["main-model", "lite"] and _an.models_for(_cf, True) == ["lite", "main-model"])
    _an._AVAILABLE = ["gemini-3-flash", "gemini-flash-lite-latest", "gemini-2.0-flash-lite", "gemini-2.0-flash", "main-model",
                      "text-embedding"]
    ok("backup the key cannot use is replaced by available models (stable alias first)",
       _an._backups({"ai": {"fallback_model": "gemini-2.5-flash-lite"}}, "main-model")[:2]
       == ["gemini-flash-lite-latest", "gemini-2.0-flash-lite"])
    _keep_dead = set(_an._DEAD)
    _an._DEAD.add("gemini-flash-lite-latest")
    ok("a model that answered 404 is skipped", "gemini-flash-lite-latest" not in _an.models_for({"ai": {}}, True))
    _an._DEAD.clear()
    _an._DEAD.update(_keep_dead)
    _an._model, _an._AVAILABLE = _keep, _keep_av
    from . import analogs as _ag
    sz = _ag.size(100.0, 95.0, "up")
    ok("position size: 5% to the fails-below line -> 1% risk = 20% capped at 10%, risking 0.5%",
       abs(sz["pct"] - 10.0) < 1e-6 and abs(sz["risk_pct"] - 0.5) < 1e-6)
    sz2 = _ag.size(100.0, 80.0, "up")
    ok("position size: 20% to the line -> 5% of the portfolio, risking 1%", abs(sz2["pct"] - 5.0) < 1e-6 and abs(sz2["risk_pct"] - 1.0) < 1e-6)
    ok("no size on the sell side", _ag.size(100.0, 95.0, "up", "sell") == {})
    pf = _ag.profile("big_up", {"stage2": True, "rs_rank": 0.9}, 0.9, "Mid", "up")
    ok("profile buckets", pf == {"res": "G", "chart": "S2", "rs": "H", "pb": "V", "tier": "M", "regime": "up"})
    lk = _ag.lookup(pf)
    ok("similar past cases found with at least 40 cases", lk is not None and lk["n"] >= 40 and lk["matched"])
    ok("regime from index history", _ag.regime({f"d{i:04d}": 100 + i * 0.1 for i in range(300)})["name"] == "up")
    ln = earnings.eps_line({"annual": True, "eps": 10.0, "eps_ly": 8.0, "eps_q": 3.0, "eps_q_ly": 1.0, "yoy_q": 2.0, "yoy": 0.25})
    ok("annual result line shows the fourth quarter first", ln == "fourth-quarter EPS 3.00 vs 1.00 a year ago (+200%); full year 10.00 vs 8.00")
    return bad


def session_tests() -> list[str]:
    """Which session the live data is about (the 13 / 19 Nov 2026 'PSX closed today' mistake, the 09:15
    pre-open 'stocks moving sharply' card), replayed with the real numbers of 18-20 Nov 2026."""
    import tempfile
    import time as _t
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from unittest import mock

    from . import watchers as w
    from .state import State
    bad = []

    def ok(name, cond):
        if not cond:
            bad.append(name)

    pkt = timezone(timedelta(hours=5))
    hist = [("2026-11-17", 177517.73), ("2026-11-18", 176294.33), ("2026-11-19", 176772.18)]
    st = State(Path(tempfile.gettempdir()) / "selftest-none.json")
    # our own record was a day behind (the 16:50 table read was stale), the official history is right
    st.data["kse_closes"] = {"2026-11-17": 177517.73, "2026-11-18": 176294.33}
    st.set_snap("kse_last_close", 176294.33)
    keep = dict(w._HIST)
    w._HIST.update(at=_t.time(), rows=hist)
    try:
        fri_0915 = datetime(2026, 11, 20, 9, 15, tzinfo=pkt)
        rolled = [{"CurrentIndex": 176772.18, "PreIndex": 176772.18}]       # rolled over, nothing traded yet
        old = [{"CurrentIndex": 176772.18, "PreIndex": 176294.33}]          # still Thursday's session
        trading = [{"CurrentIndex": 177100.0, "PreIndex": 176772.18}]
        ok("rolled-over view = today, despite a stale own record", w.view_session(rolled, st, fri_0915) == "today")
        ok("Thursday's view on Friday morning = previous session", w.view_session(old, st, fri_0915) == "previous")
        ok("no trading before the index moves", not w.trading_started(fri_0915, rolled))
        ok("Friday trading from 09:15 once the index moves", w.trading_started(fri_0915, trading))
        thu_0915 = datetime(2026, 11, 19, 9, 20, tzinfo=pkt)
        ok("Mon-Thu pre-open (before 09:30) is not trading",
           not w.trading_started(thu_0915, [{"CurrentIndex": 176300.0, "PreIndex": 176294.33}]))
        thu_close = datetime(2026, 11, 19, 16, 55, tzinfo=pkt)
        ok("Thursday evening: the view shows today's session", w.view_session(old, st, thu_close) == "today")
        w.sync_closes(st, thu_close)
        ok("missing close filled from the official history", st.data["kse_closes"].get("2026-11-19") == 176772.18
           and st.snap("kse_last_close") == 176772.18 and st.snap("kse_session_date") == "2026-11-19")
        # the 16:50 recording takes the close from the live view, not from the late table
        st2 = State(Path(tempfile.gettempdir()) / "selftest-none.json")
        st2.data["kse_closes"] = {"2026-11-18": 176294.33}
        w._HIST.update(at=_t.time(), rows=hist[:2])
        with mock.patch.object(w.scs, "kse100_view", return_value=[{"CurrentIndex": 176772.18, "PreIndex": 176294.33,
                                                                      "company_code": "UBL", "CurrentPrice": 427.66}]), \
                mock.patch.object(w.scs, "daily_activity", return_value=[]):
            w.record_close(thu_close, st2)
        ok("close recorded from the live view", st2.data["kse_closes"].get("2026-11-19") == 176772.18
           and st2.snap("kse_session_date") == "2026-11-19" and st2.data["daily"]["2026-11-19"]["px"]["UBL"] == 427.66)
        ok("volumes wait for today's all-stock table", not st2.flag("volrec:2026-11-19"))
        # a holiday: the view never rolls over -> no close, flag set, no session today
        st3 = State(Path(tempfile.gettempdir()) / "selftest-none.json")
        hol = datetime(2026, 11, 20, 17, 0, tzinfo=pkt)
        w._HIST.update(at=_t.time(), rows=hist)
        with mock.patch.object(w.scs, "kse100_view", return_value=old):
            w.record_close(hol, st3)
        ok("holiday: nothing recorded for today", "2026-11-20" not in st3.data["kse_closes"]
           and st3.flag("closerec:2026-11-20") and st3.snap("kse_session_date") != "2026-11-20")
    finally:
        w._HIST.clear()
        w._HIST.update(keep)
    # 20 Nov 2026 09:17: the view's rows still showed Thursday (PGLC +10% then) while a real move happened today
    act = [{"company_code": "PGLC", "trading_close": 22.43, "trading_change": 2.04, "company_name": "x"},
           {"company_code": "ABC", "trading_close": 50.0, "trading_change": 0.1, "company_name": "y"}]
    view5 = [{"company_code": "PGLC", "CurrentPrice": 22.43, "LDCP": 20.39, "CurrentIndex": 176600.0, "PreIndex": 176772.0},
             {"company_code": "ABC", "CurrentPrice": 55.0, "LDCP": 50.0, "CurrentIndex": 176600.0, "PreIndex": 176772.0}]
    st5 = State(Path(tempfile.gettempdir()) / "selftest-none.json")
    with mock.patch.object(w.scs, "daily_activity", return_value=act), \
            mock.patch.object(w, "avg_values", return_value={"PGLC": 2e7, "ABC": 2e7}), \
            mock.patch.object(w.scs, "activity_is_today", return_value=False), \
            mock.patch.object(w.scs, "live_activity", return_value=([], False)), \
            mock.patch.object(w, "move_reasons", return_value={}):
        w.stock_moves(datetime(2026, 11, 20, 9, 17, tzinfo=pkt), st5, {"big_stock_move_pct": 7.5}, view5)
    moved = [k for k in st5.data["flags"] if k.startswith("stk:")]
    ok("yesterday's move still on screen is not posted again; today's real move is", moved == ["stk:2026-11-20:ABC:up"])
    # owner commands (offline: symbol list and workflow start are replaced)
    from . import commands as cm
    with mock.patch.object(cm, "_known", return_value={"STCL", "LUCK", "FFC"}), \
            mock.patch.object(cm, "dispatch", return_value=(True, "204")) as disp:
        st4 = State(Path(tempfile.gettempdir()) / "selftest-none.json")
        now = datetime(2026, 11, 20, 11, 0, tzinfo=pkt)
        ok("bare ticker starts the Lab", "Analysing <b>STCL</b>" in cm.respond("stcl", st4, {}, now)
           and disp.call_args[0] == ("lab.yml", {"command": "lab", "symbol": "STCL"}))
        ok("two tickers", "STCL, LUCK" in cm.respond("/analyse STCL, luck", st4, {}, now))
        ok("unknown ticker is refused", "not a PSX symbol" in cm.respond("/analyse XYZQ", st4, {}, now))
        ok("ordinary words get the help", "did not understand" in cm.respond("hi there", st4, {}, now))
        ok("note preview goes to stock.yml", "Stock of the Day note on <b>FFC</b>" in cm.respond("/note ffc", st4, {}, now)
           and disp.call_args[0] == ("stock.yml", {"command": "sotd --preview", "symbol": "FFC"}))
        st4.data["tg_dispatch"] = {"d": "2026-11-20", "n": 12}
        ok("daily limit", "limit" in cm.respond("STCL", st4, {}, now))
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
    import logging

    from .common import log
    # the tests use made-up data: their own log lines ('price history adjusted', 'owner command started') would
    # look like real events in the public run log, so they are muted while the tests run
    logging.disable(logging.INFO)
    try:
        bad = [f"formula: {x}" for x in formulas()] + [f"scoring: {x}" for x in scoring()] + \
            [f"levels: {x}" for x in levels_tests()] + [f"session: {x}" for x in session_tests()]
    finally:
        logging.disable(logging.NOTSET)
    if with_live:
        bad += [f"data: {x}" for x in live()]
    for b in bad:
        log.error("SELFTEST FAILED: %s", b)
    log.info("selftest: %s", "all checks passed" if not bad else f"{len(bad)} failure(s)")
    return bad
