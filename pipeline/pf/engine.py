"""The whole review in one pass: positions -> performance -> risk -> stress -> fundamentals -> rules -> findings.

Weights are today's market values. The history of the portfolio is TODAY's holdings at today's weights, rebalanced
daily (the standard 'current portfolio' backtest of risk systems); it starts when the youngest holding was listed.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import fund, metrics as mt, optimize, rules, stress
from .data import policy_cycles, rf_daily, sessions_per_year
from .holdings import NOTIONAL


def _frame(series: dict, syms: list[str], dates: pd.DatetimeIndex) -> pd.DataFrame:
    cols = {}
    for s in syms:
        x = series.get(s)
        if x is None or not len(x):
            continue
        x = x[~x.index.duplicated()].reindex(dates.union(x.index)).ffill().reindex(dates)
        cols[s] = x
    return pd.DataFrame(cols, index=dates)


def positions(hold: list[dict], cash, mode: str, D: dict) -> dict:
    act, snaps = D["act"], D["snaps"]
    price, missing = {}, []
    for h in hold:
        s = h["sym"]
        p = D["live_px"].get(s) or (act.get(s) or {}).get("trading_close") or \
            ((snaps.get(s) or {}).get("values") or {}).get("price")
        if p and s in D["tr"]:
            price[s] = float(p)
        else:
            missing.append(s)
    rows, notes = [], []
    if mode == "weights":
        tw = sum(h.get("weight", 0) for h in hold if h["sym"] in price) + (cash or 0)
        if abs(tw - 1) > 0.005:
            notes.append(f"weights added up to {tw * 100:.1f}%; scaled to 100%")
        cash_val = (cash or 0) / tw * NOTIONAL if tw else 0
        for h in hold:
            if h["sym"] in price:
                v = h["weight"] / tw * NOTIONAL
                rows.append({"sym": h["sym"], "qty": v / price[h["sym"]], "value": v, "cost": None})
        notes.append("percentages given: rupee figures use a Rs 1,000,000 portfolio")
    else:
        cash_val = float(cash or 0)
        for h in hold:
            s = h["sym"]
            if s not in price:
                continue
            if mode == "values":
                rows.append({"sym": s, "qty": h["value"] / price[s], "value": h["value"], "cost": None})
            else:
                rows.append({"sym": s, "qty": h["qty"], "value": h["qty"] * price[s], "cost": h.get("cost")})
    for r in rows:
        r["price"] = price[r["sym"]]
    eq = sum(r["value"] for r in rows)
    total = eq + cash_val
    for r in rows:
        r["w"] = r["value"] / total
        r["weq"] = r["value"] / eq
    rows.sort(key=lambda r: -r["value"])
    return {"rows": rows, "total": total, "equity": eq, "cash": cash_val, "cash_w": cash_val / total if total else 0,
            "missing": missing, "notes": notes, "mode": mode}


def analyse(hold: list[dict], cash, mode: str, D: dict, cfg: dict) -> dict:
    from .. import fairvalue
    P = positions(hold, cash, mode, D)
    if not P["rows"]:
        return {"error": "none of the holdings could be priced", "pos": P}
    syms = [r["sym"] for r in P["rows"]]
    w = {r["sym"]: r["w"] for r in P["rows"]}
    weq = {r["sym"]: r["weq"] for r in P["rows"]}
    idx = D["idx"]
    dates = idx.index
    A = sessions_per_year(idx)
    rf = rf_daily(dates, A, D["sched"])
    rm = idx.pct_change()
    TR, PX = _frame(D["tr"], syms, dates), _frame(D["px"], syms, dates)
    Rtr, Rpx = TR.pct_change(fill_method=None), PX.pct_change(fill_method=None)
    wv = np.array([w[s] for s in syms])
    cw = P["cash_w"]
    first = TR.apply(lambda c: c.first_valid_index())
    start = max(first.dropna())
    common = dates[(dates > start)]
    rp_tr = (Rtr.loc[common, syms].fillna(0) @ wv) + cw * rf.loc[common]
    rp_px = (Rpx.loc[common, syms].fillna(0) @ wv) + cw * rf.loc[common]
    R: dict = {"pos": P, "A": A, "start": start, "end": dates[-1], "weights": w, "weq": weq, "total": P["total"],
               "kse_w": D["kse_w"], "rp_tr": rp_tr, "rp_px": rp_px, "rm": rm.loc[common], "idx": idx}

    # ---------------- performance by window
    wins = {}
    for key, n in (("1y", int(A)), ("3y", int(3 * A)), ("5y", int(5 * A)), ("all", len(common))):
        if len(common) < n * 0.98 or n < 60:
            continue
        ix = common[-n:]
        a, b, f, m = rp_tr.loc[ix], rp_px.loc[ix], rf.loc[ix], rm.loc[ix]
        mo = mt.monthly(a)
        st = {"from": ix[0], "n": len(ix), "total": mt.total(a), "cagr": mt.cagr(a, A), "cagr_px": mt.cagr(b, A),
              "vol": mt.vol(a, A), "sharpe": mt.sharpe(a, f, A), "sortino": mt.sortino(a, f, A),
              "dd": mt.max_drawdown(a), "calmar": mt.calmar(a, A), "ulcer": mt.ulcer(a),
              "martin": mt.martin(a, float(((1 + f).prod()) ** (A / len(f)) - 1), A), "omega": mt.omega(a, f),
              **mt.shape(a), "best_m": float(mo.max()) if len(mo) else None, "worst_m": float(mo.min()) if len(mo) else None,
              "pos_m": float((mo > 0).mean()) if len(mo) else None,
              "rel": mt.relative(b, m, f, A), "cap": mt.capture(b, m),
              "k_cagr": mt.cagr(m, A), "k_vol": mt.vol(m, A), "k_dd": mt.max_drawdown(m).get("depth"),
              "k_sharpe": mt.sharpe(m, f, A), "rf": float(((1 + f).prod()) ** (A / len(f)) - 1)}
        wins[key] = st
    R["win"] = wins
    years = []
    for yr in sorted(set(common.year)):
        ix = common[common.year == yr]
        if len(ix) < 20:
            continue
        years.append({"year": yr, "tr": mt.total(rp_tr.loc[ix]), "px": mt.total(rp_px.loc[ix]),
                      "kse": mt.total(rm.loc[ix]), "partial": "from " + format(ix[0], "%d %b") if yr == common[0].year
                      else ("so far" if yr == common[-1].year else "")})
    R["years"] = years
    one = wins.get("1y") or {}
    R["vol_ratio"] = one["vol"] / one["k_vol"] if one.get("vol") and one.get("k_vol") else None

    # ---------------- value at risk (last two years of daily total returns)
    last = rp_tr.iloc[-500:]
    var = {}
    if len(last) >= 120:
        sd = float(last.std(ddof=1))
        sh = mt.shape(last)
        ew = mt.ewma_sd(rp_tr.values)
        for c in (0.95, 0.99):
            k = int(c * 100)
            var[k] = {"hist": mt.var_hist(last, c), "normal": mt.var_normal(sd, c),
                      "cf": mt.var_cf(sd, sh.get("skew", 0), sh.get("kurt", 0), c) if sh else None,
                      "ewma": mt.var_normal(ew, c) if ew else None,
                      "es_hist": mt.es_hist(last, c), "es_normal": mt.es_normal(sd, c)}
        var["sd"], var["ewma_sd"], var["n"] = sd, ew, len(last)
        var["kupiec99"] = mt.kupiec(rp_tr.values, 0.99)
        var["kupiec95"] = mt.kupiec(rp_tr.values, 0.95)
    R["var"] = var

    # ---------------- covariance, risk decomposition, betas
    win2 = Rtr.loc[dates[-500:], syms]
    inc = [s for s in syms if win2[s].notna().sum() >= 250]
    X = win2[inc].dropna()
    betas, betas_adj, r2 = {}, {}, {}
    for s in syms:
        rel = mt.relative(Rpx[s].iloc[-int(3 * A):], rm.iloc[-int(3 * A):], rf.iloc[-int(3 * A):], A)
        if rel:
            betas[s], betas_adj[s], r2[s] = rel["beta"], mt.blume(rel["beta"]), rel["r2"]
    R["betas"], R["betas_adj"], R["r2"] = betas, betas_adj, r2
    risk: dict = {"included": inc}
    if len(inc) >= 1 and len(X) >= 120:
        S = X.cov().values * A
        LW, delta = mt.shrink_cov(X.values)
        LW = LW * A
        wi = np.array([w[s] for s in inc])
        rc = mt.risk_contrib(wi, S)
        risk.update(sigma=rc["sigma"], pct=dict(zip(inc, rc["pct"].tolist())), cr=dict(zip(inc, rc["cr"].tolist())),
                    own_vol={s: float(math.sqrt(S[i, i])) for i, s in enumerate(inc)},
                    dr=mt.div_ratio(wi, S), enb=mt.enb(wi, S), shrink=delta, n_obs=len(X))
        corr = X.corr()
        risk["corr"] = corr
        pairs = []
        for i, a in enumerate(inc):
            for b in inc[i + 1:]:
                if corr.loc[a, b] > 0.8:
                    pairs.append((a, b, float(corr.loc[a, b])))
        R["pairs"] = sorted(pairs, key=lambda t: -t[2])
        tri = corr.values[np.triu_indices(len(inc), 1)]
        risk["avg_corr"] = float(tri.mean()) if len(tri) else None
        # systematic share of the portfolio's variance (from the 3-year regression on price returns)
        rel3 = (wins.get("3y") or wins.get("1y") or {}).get("rel") or {}
        risk["systematic"] = rel3.get("r2")
        # the alternatives (same stocks, other weights)
        bv = np.array([betas.get(s, 1.0) for s in inc])
        alts = {"Your weights": wi / wi.sum(), **optimize.alternatives(LW)}
        Rx = Rtr.loc[common, inc].fillna(0)
        comp = {}
        for name, ww in alts.items():
            d = optimize.describe(ww, LW, bv)
            ser = Rx @ ww
            n5 = min(len(ser), int(5 * A))
            d["cagr5"] = mt.cagr(ser.iloc[-n5:], A)
            d["dd5"] = mt.max_drawdown(ser.iloc[-n5:]).get("depth")
            d["w"] = dict(zip(inc, ww.tolist()))
            comp[name] = d
        R["alts"] = comp
        R["cap"] = optimize.cap_for(len(inc))
    R["risk"] = risk

    # ---------------- concentration, sectors, liquidity
    wq = np.array(list(weq.values()))
    srt = sorted(weq.values(), reverse=True)
    kse_w = {k: v for k, v in D["kse_w"].items() if v > 0}
    kt = sum(kse_w.values()) or 1
    kse_n = {k: v / kt for k, v in kse_w.items()}
    R["conc"] = {"hhi": mt.hhi(wq), "eff_n": 1 / mt.hhi(wq), "top1": srt[0], "top5": sum(srt[:5]),
                 "top10": sum(srt[:10]), "active": mt.active_share(weq, kse_n),
                 "in_kse": sum(v for s, v in weq.items() if s in kse_n)}
    sec = D["sector"]
    sw: dict = {}
    for s, x in weq.items():
        k = (sec.get(s) or ((D["snaps"].get(s) or {}).get("sector")) or "Other").strip().upper()
        sw[k] = sw.get(k, 0) + x
    ksw: dict = {}
    for s, x in kse_n.items():
        k = (sec.get(s) or "Other").strip().upper()
        ksw[k] = ksw.get(k, 0) + x
    R["sector_w"], R["kse_sector_w"] = dict(sorted(sw.items(), key=lambda kv: -kv[1])), ksw
    liq_rows, buckets = {}, {"1": 0.0, "5": 0.0, "20": 0.0, "more": 0.0}
    for r in P["rows"]:
        s = r["sym"]
        dd = mt.liquidity_days(r["value"], D["adv"].get(s))
        ff = ((D["snaps"].get(s) or {}).get("values") or {}).get("freefloat")
        liq_rows[s] = {"days": dd, "adv": D["adv"].get(s), "ff_pct": r["qty"] / (ff * 1e6) if ff else None}
        key = "1" if dd is not None and dd <= 1 else "5" if dd is not None and dd <= 5 else "20" if dd is not None and dd <= 20 else "more"
        buckets[key] += r["w"]
    R["liq"] = {"rows": liq_rows, "buckets": buckets, "illiquid": buckets["20"] + buckets["more"]}

    # ---------------- stress tests
    eps = stress.episodes(idx, 0.15)
    ep_res = []
    for e in eps:
        rft = float((1 + rf.loc[e["peak"]:e["trough"]]).prod() - 1)
        res = stress.window(D["px"], w, betas_adj, idx, e["peak"], e["trough"], cw, rft)
        if res:
            ep_res.append({**e, **res})
    R["episodes"] = ep_res
    if ep_res:
        R["stress_worst"] = min(ep_res, key=lambda e: e["ret"])
    R["worst_days"] = stress.worst_days(idx, D["px"], w, betas_adj)
    cyc = []
    for c in policy_cycles(D["sched"]):
        if pd.Timestamp(c["start"]) < idx.index[0]:
            continue
        rft = float((1 + rf.loc[c["start"]:c["end"]]).prod() - 1)
        res = stress.window(D["px"], w, betas_adj, idx, pd.Timestamp(c["start"]), pd.Timestamp(c["end"]), cw, rft)
        if res:
            cyc.append({**c, **res})
    R["cycles"] = cyc
    fm = stress.factor_model(D["px"], idx, D["factors"])
    R["fm"] = {"keys": fm["keys"], "n": fm["n"], "start": fm["start"], "B": fm["B"]}
    scen = []
    for name, shocks, desc in stress.SCENARIOS:
        res = stress.scenario(fm, w, shocks, betas_adj)
        if res:
            scen.append({"name": name, "desc": desc, **res})
    R["scenarios"] = scen
    rf_now = fairvalue.risk_free(D["sbp"]) or float((cfg.get("fair_value") or {}).get("risk_free", 12.0))
    erp = float((cfg.get("fair_value") or {}).get("erp", 5.0))
    beta_p = sum(w[s] * (betas_adj.get(s) or 1.0) for s in syms)
    exp_r = (rf_now + beta_p * erp) / 100 * (1 - cw) + cw * rf_now / 100
    R["mc"] = stress.monte_carlo(rp_tr.iloc[-int(10 * A):], A, exp_r, stress.crisis_days(idx, eps))
    R["mc"].update(rf_now=rf_now, erp=erp, beta_p=beta_p)

    # ---------------- fundamentals, income, tax, quality
    cost_e = fairvalue.coe(cfg, rf_now)
    frows = [fund.per_stock(r["sym"], D["snaps"].get(r["sym"]), r["price"], cost_e) for r in P["rows"]]
    R["fund"] = frows
    R["fund_agg"] = fund.aggregate(frows, weq)
    R["coe"] = cost_e
    inc_g = sum(r["qty"] * (f.get("dps") or 0) for r, f in zip(P["rows"], frows))
    R["income"] = {"gross": inc_g, "net": inc_g * (1 - fund.WHT_DIVIDEND), "yield": inc_g / P["total"] if P["total"] else None}
    costed = [r for r in P["rows"] if r.get("cost")]
    if costed:
        gain = sum((r["price"] - r["cost"]) * r["qty"] for r in costed)
        base = sum(r["cost"] * r["qty"] for r in costed)
        R["pnl"] = {"gain": gain, "base": base, "pct": gain / base if base else None,
                    "cgt": max(0.0, gain) * fund.CGT_LISTED, "covered": len(costed)}
    bad = [(f["sym"], weq[f["sym"]]) for f in frows if f.get("flags") and any(
        k in " ".join(f["flags"]) for k in ("loss-making", "negative book", "defaulters"))]
    R["quality"] = {"weight": sum(x for _, x in bad), "syms": [s for s, _ in bad],
                    "defaulter": any("defaulters" in " ".join(f.get("flags", [])) for f in frows)}
    R["kmi_share"] = sum(x for s, x in weq.items() if s in D["kmi"]) if D["kmi"] else None
    R["events"] = fund.events(set(syms), D["now"])
    try:
        from ..levelwatch import _report
        rep = {it["sym"]: it for it in (_report().get("items") or [])}
        R["lab"] = {s: rep[s] for s in syms if s in rep}
    except Exception:  # noqa: BLE001
        R["lab"] = {}
    R["rules"] = rules.book(R)
    R["findings"] = findings(R)
    return R


def _p(x, d=1, sign=False) -> str:
    return "n/a" if x is None or x != x else (f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%")


def _rs(x) -> str:
    if x is None:
        return "n/a"
    a = abs(x)
    s = f"Rs {a / 1e6:,.2f}m" if a >= 1e6 else f"Rs {a:,.0f}"
    return ("-" if x < 0 else "") + s


def findings(R: dict) -> list[str]:
    """The main points in plain sentences, most important first."""
    out = []
    one, three = R["win"].get("1y") or {}, R["win"].get("3y") or {}
    br = [r for r in R["rules"] if r["status"] == "breach"]
    wa = [r for r in R["rules"] if r["status"] == "watch"]
    out.append(f"Rule check: {len(br)} limit{'s' if len(br) != 1 else ''} exceeded and {len(wa)} to watch, out of "
               f"{len(R['rules'])} checks" + (f" (exceeded: {', '.join(r['rule'].lower() for r in br[:3])})." if br else "."))
    if one.get("vol") and one.get("k_vol"):
        rel = one.get("rel") or {}
        out.append(f"Over the last year the portfolio's volatility was {_p(one['vol'])} a year against {_p(one['k_vol'])} "
                   f"for the KSE-100" + (f"; beta {rel['beta']:.2f} (moves about {rel['beta']:.2f}x the market)" if rel.get("beta") else "") + ".")
    st = three or one
    if st.get("cagr") is not None:
        out.append(f"Held at today's weights, these stocks returned {_p(st['cagr'])} a year over the last "
                   f"{'3 years' if three else 'year'} with dividends ({_p(st.get('cagr_px'))} on price alone) vs "
                   f"{_p(st.get('k_cagr'))} for the KSE-100 price index; the risk-free rate averaged {_p(st.get('rf'))}.")
    rk = R["risk"].get("pct") or {}
    if rk:
        s, p = max(rk.items(), key=lambda kv: kv[1])
        out.append(f"{s} is {_p(R['weights'][s], 0)} of the money but {_p(p, 0)} of the risk"
                   + (f"; diversification ratio {R['risk']['dr']:.2f} (1.00 = no benefit from spreading)." if R['risk'].get('dr') else "."))
    v = R["var"]
    if v.get(95):
        out.append(f"Value at Risk: on 1 day in 20 expect a loss of at least {_rs(v[95]['hist'] * R['total'])} "
                   f"({_p(v[95]['hist'])}); on 1 day in 100 at least {_rs(v[99]['hist'] * R['total'])} ({_p(v[99]['hist'])}), "
                   f"and {_p(v[99]['es_hist'])} on average when that happens.")
    ws = R.get("stress_worst")
    if ws:
        nm = ws.get("name") or f"the fall of {ws['peak']:%b %Y} to {ws['trough']:%b %Y}"
        out.append(f"Worst past crisis for these holdings: {nm}, {_p(ws['ret'], 0)} vs {_p(ws['idx'], 0)} for the KSE-100"
                   + (f" ({_p(ws['coverage'], 0)} of the portfolio had real prices then)." if ws["coverage"] < 0.999 else "."))
    mc = (R.get("mc") or {}).get("normal")
    if mc:
        out.append(f"Next 12 months (10,000 simulations): median {_p(mc['pct'][50], 0, True)}, 1 in 20 chance of "
                   f"{_p(mc['pct'][5], 0, True)} or worse; chance of losing over 20%: {_p(mc['p_loss20'], 0)}.")
    il = R["liq"]["illiquid"]
    if il > 0.0001:
        out.append(f"{_p(il, 0)} of the portfolio would need more than 5 sessions to sell at 20% of daily trading.")
    fa = R["fund_agg"]
    if fa.get("pe") or fa.get("dy"):
        out.append(f"Valuation: portfolio P/E {fa['pe']:.1f}x" if fa.get("pe") else "Valuation:")
        out[-1] += (f", P/B {fa['pb']:.2f}x" if fa.get("pb") else "") + (f", dividend yield {_p(fa['dy'])}" if fa.get("dy") else "") + \
            (f"; about {_rs(R['income']['net'])} a year in dividends after 15% tax." if R["income"]["gross"] else ".")
    return out
