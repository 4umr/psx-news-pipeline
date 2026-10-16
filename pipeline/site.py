"""PAKISTAN INVESTORS data hub: a static website rebuilt every evening (GitHub Pages).

Pages (public data only; the private swing signals are never published here):
  index.html     screener of every listed company (search, filter by sector/size, sort any column)
  s/SYMBOL.html  one page per company: price chart, returns, results vs a year ago, upcoming board meeting
  results.html   results calendar (next 14 days) and recent results with the market's reaction
  about.html     where the numbers come from, and the disclaimer

Built from the same price database and results history as the swing research, so numbers agree
everywhere. If anything fails the build stops and the previous version of the site stays online.
"""
from __future__ import annotations

import html
import json
import shutil


import numpy as np
import pandas as pd

from . import earnings, prices, research
from .common import ROOT, log, now_pkt
from .signals import features
from .sources import scs

OUT = ROOT / "out" / "site"
H = html.escape

CSS = """
:root{--bg:#f3efe4;--panel:#fff;--edge:#e1dccd;--text:#14281c;--muted:#6b766e;--green:#1d6b3c;--gold:#a87b22;
--up:#1a8a4a;--down:#c8372f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}a{color:var(--green)}header{background:#fdfcf8;border-bottom:1px solid var(--edge)}
.wrap{max-width:1100px;margin:0 auto;padding:0 16px}.top{display:flex;align-items:center;gap:14px;padding:14px 16px;flex-wrap:wrap}
.brand{font:700 20px/1.1 Georgia,"Times New Roman",serif;color:#1d5c36;letter-spacing:.06em;text-decoration:none}
.brand small{display:block;font:600 10px system-ui;color:var(--muted);letter-spacing:.12em;margin-top:4px}
nav{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap}nav a{text-decoration:none;font-weight:600}
h1{font:700 30px/1.15 Georgia,"Times New Roman",serif;margin:22px 0 4px}h1 span{color:var(--gold)}
h2{font:700 14px system-ui;letter-spacing:.12em;text-transform:uppercase;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 18px}.panel{background:var(--panel);border:1px solid var(--edge);border-radius:16px;
padding:18px;margin:0 0 16px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.tile{background:#f7f5ef;border:1px solid var(--edge);border-radius:12px;padding:10px 12px}.tile b{display:block;font-size:19px}
.tile small{color:var(--muted);font-size:12px}.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:8px 10px;text-align:right;
white-space:nowrap;border-bottom:1px solid #eee9dc}th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--muted);cursor:pointer;user-select:none;position:sticky;top:0;background:#fff}th:first-child,td:first-child,
th.l,td.l{text-align:left}td a{font-weight:700;text-decoration:none}.controls{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
input,select{font:inherit;padding:8px 10px;border:1px solid var(--edge);border-radius:10px;background:#fff;min-width:0}
input[type=search]{flex:1 1 220px}label{display:flex;gap:6px;align-items:center;color:var(--muted)}
footer{color:var(--muted);font-size:13px;padding:24px 0 40px}svg{width:100%;height:auto;display:block}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;background:#1d6b3c;color:#fff;
border-radius:6px;padding:2px 7px;margin-left:6px;vertical-align:middle}
@media (max-width:640px){.hm{display:none}h1{font-size:25px}.panel{padding:14px}}
"""


def _page(title: str, body: str, depth: int = 0, asof: str = "") -> str:
    up = "../" * depth
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{H(title)} | PAKISTAN INVESTORS</title>
<link rel="stylesheet" href="{up}style.css"></head><body>
<header><div class="wrap top"><a class="brand" href="{up}index.html">PAKISTAN INVESTORS<small>DATA HUB</small></a>
<nav><a href="{up}index.html">Screener</a><a href="{up}sectors.html">Sectors</a><a href="{up}results.html">Results</a><a href="{up}about.html">About</a></nav>
</div></header><main class="wrap">{body}</main>
<footer class="wrap">Data: PSX via SCS Trade and dps.psx.com.pk. {('Updated ' + H(asof) + '. ') if asof else ''}
Information only, not investment advice. Check the official filing before acting.</footer></body></html>"""


def _pct(x, dec: int = 1) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return '<span class="muted">-</span>'
    cls = "up" if x > 0 else ("down" if x < 0 else "muted")
    return f'<span class="{cls}">{x * 100:+.{dec}f}%</span>'


def _chart(dates: list, values: list, w: int = 900, h: int = 260) -> str:
    """Simple line chart as inline SVG (no scripts needed)."""
    if len(values) < 2:
        return '<p class="muted">Not enough price history yet.</p>'
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or hi * 0.02 or 1
    lo, hi = lo - pad, hi + pad
    n = len(values) - 1
    pts = " ".join(f"{80 + i * (w - 100) / n:.1f},{20 + (hi - v) * (h - 50) / (hi - lo):.1f}" for i, v in enumerate(values))
    col = "#1a8a4a" if values[-1] >= values[0] else "#c8372f"
    area = f"80,{h - 30} " + pts + f" {w - 20},{h - 30}"
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Price chart">'
            f'<polygon points="{area}" fill="{col}" opacity=".08"/>'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.2"/>'
            f'<text x="74" y="24" font-size="12" text-anchor="end" fill="#6b766e">{hi - pad:,.2f}</text>'
            f'<text x="74" y="{h - 34}" font-size="12" text-anchor="end" fill="#6b766e">{lo + pad:,.2f}</text>'
            f'<text x="80" y="{h - 8}" font-size="12" fill="#6b766e">{dates[0]:%d %b %Y}</text>'
            f'<text x="{w - 20}" y="{h - 8}" font-size="12" text-anchor="end" fill="#6b766e">{dates[-1]:%d %b %Y}</text></svg>')


def _result_rows(h: pd.DataFrame, n: int = 12) -> str:
    rows = []
    for _, r in h.sort_values("date", ascending=False).head(n).iterrows():
        annual = r["kind"] in ("FY", "CY")
        eps, ly = (r["eps_cum"], r["eps_cum_ly"]) if annual else (r["eps_q"], r["eps_q_ly"])
        yoy = r["yoy_cum"] if annual else r["yoy_q"]
        note = ""
        if pd.isna(yoy) and pd.notna(eps) and pd.notna(ly):
            note = "back to profit" if eps > 0 > ly else ("loss" if eps < 0 else "")
        change = note or _pct(None if pd.isna(yoy) else yoy, 0)
        eps_s = "" if pd.isna(eps) else f"{eps:,.2f}"
        ly_s = "" if pd.isna(ly) else f"{ly:,.2f}"
        div_s = "" if pd.isna(r["div"]) else f"{r['div']:g}%"
        rows.append(f"<tr><td class=l>{r['date']:%d %b %Y}</td><td class=l>{H(str(r['label']))}"
                    f"{' (full year)' if annual else ''}</td><td>{eps_s}</td><td>{ly_s}</td><td>{change}</td>"
                    f"<td>{div_s}</td></tr>")
    if not rows:
        return '<p class="muted">No results found for this company.</p>'
    return ('<div class="scroll"><table><tr><th class=l>Date</th><th class=l>Period</th><th>EPS (Rs)</th>'
            '<th>Year earlier</th><th>Change</th><th>Dividend</th></tr>' + "".join(rows) + "</table></div>"
            '<p class="muted">Quarterly results compare the quarter with the same quarter a year earlier; '
            'annual results compare the full year. Dividend as % of face value, as announced.</p>')


def build(cfg: dict) -> int:
    """Build the site into out/site. Returns the number of company pages."""
    now = now_pkt()
    min_adtv = float(cfg.get("swing", {}).get("min_adtv", 10e6))
    raw = prices.update_live(now)
    if raw.empty or raw.attrs.get("stale"):
        raise RuntimeError("price data not available or incomplete; keeping the previous site")
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    syms = sorted(raw["sym"].unique())
    meta = prices.meta(syms)
    df = features(prices.adjust(raw), meta)
    last_d = df["date"].max()
    piv = df.pivot_table(index="date", columns="sym", values="ac").ffill()
    ret = {n: piv.iloc[-1] / piv.iloc[-1 - n] - 1 for n in (5, 21, 63, 250) if len(piv) > n}
    today = df[df["date"] == last_d].set_index("sym")
    kse = {r.get("company_code") for r in scs.kse100_view()}
    res = earnings.classify(earnings.load_all(syms))
    res = res.sort_values("date")
    v = research.valuation(df, res)
    v_last = v[v["date"] == last_d].set_index("sym")
    by_sym = {s: g for s, g in res.groupby("sym")}
    meet: dict[str, pd.Timestamp] = {}
    meet_lab: dict[str, str] = {}
    for b in scs.board_meetings():
        s = (b.get("company_code") or "").strip()
        if not b.get("date"):
            continue
        d = pd.Timestamp(b["date"].strftime("%Y-%m-%d"))
        if d >= pd.Timestamp(now.strftime("%Y-%m-%d")) and (s not in meet or d < meet[s]):
            meet[s], meet_lab[s] = d, (b.get("bm_quarter_number") or "").strip()
    closures = {}
    for b in scs.book_closures():
        s = (b.get("company_code") or "").strip()
        if b.get("date") and b["date"].date() >= now.date():
            closures.setdefault(s, []).append(b)

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "s").mkdir(parents=True)
    (OUT / "style.css").write_text(CSS, encoding="utf-8")
    asof = f"{last_d:%d %b %Y} close"
    rows = []
    for s in syms:
        a = act.get(s, {})
        name = (a.get("company_name") or s).strip()
        sector = (a.get("sector_name") or "").strip().title()
        hist = df[df["sym"] == s].tail(250)
        if hist.empty:
            continue
        last = hist.iloc[-1]
        size = last["tier"] if isinstance(last["tier"], str) else "n/a"
        rs = {n: (float(ret[n].get(s)) if n in ret and pd.notna(ret[n].get(s)) else None) for n in (5, 21, 63, 250)}
        h = by_sym.get(s)
        last_res = None
        if h is not None and not h.empty:
            r = h.iloc[-1]
            annual = r["kind"] in ("FY", "CY")
            y = r["yoy_cum"] if annual else r["yoy_q"]
            last_res = {"label": str(r["label"]), "yoy": None if pd.isna(y) else float(y)}
        chg1 = float(last["chg"]) if last["date"] == last_d and pd.notna(last["chg"]) else 0.0
        row = {"s": s, "n": name, "sec": sector, "z": size, "k": s in kse, "p": round(float(last["c"]), 2),
               "d1": round(chg1, 4), "w1": rs[5], "m1": rs[21], "m3": rs[63], "y1": rs[250],
               "v": round(float(last["adtv20"]) / 1e6, 1) if pd.notna(last["adtv20"]) else None,
               "liq": bool(pd.notna(last["adtv20"]) and last["adtv20"] >= min_adtv),
               "r": last_res["label"] if last_res else "", "ry": last_res["yoy"] if last_res else None,
               "bm": f"{meet[s]:%d %b}" if s in meet else "",
               "pe": round(float(v_last.at[s, "pe"]), 1) if s in v_last.index and pd.notna(v_last.at[s, "pe"]) else None}
        rows.append(row)
        # company page
        tiles = "".join(f'<div class="tile"><small>{lab}</small><b>{_pct(v) if v is not None else "-"}</b></div>'
                        for lab, v in (("Today", chg1), ("1 week", rs[5]), ("1 month", rs[21]),
                                       ("3 months", rs[63]), ("1 year", rs[250])))
        facts = [f"Size: <b>{H(size)} cap</b>" + (" (KSE-100)" if s in kse else "")]
        m = meta.get(s, {})
        if pd.notna(last.get("mcap")):
            facts.append(f"Market value: <b>Rs {last['mcap'] / 1e9:,.1f} bn</b>")
        if m.get("free_float_pct"):
            facts.append(f"Free float: <b>{m['free_float_pct']:.0f}%</b>")
        if row["v"] is not None:
            facts.append(f"Average traded: <b>Rs {row['v']:,.1f} m a day</b>")
        if row["pe"] is not None:
            facts.append(f"P/E (last 12 months): <b>{row['pe']:.1f}</b> on EPS of Rs {v_last.at[s, 'ttm']:,.2f}")
        elif s in v_last.index and pd.notna(v_last.at[s, "ttm"]):
            facts.append("P/E: not shown (loss over the last 12 months)" if v_last.at[s, "ttm"] <= 0 else
                         "P/E: not shown (share split or bonus issue in the last two years makes reported EPS not comparable)")
        events = []
        if s in meet:
            events.append(f"Board meeting on <b>{meet[s]:%a %d %b %Y}</b>"
                          + (f" ({H(meet_lab[s])} results expected)" if meet_lab.get(s) else ""))
        for b in closures.get(s, [])[:3]:
            pay = " ".join(x for x in (f"Dividend {b.get('bm_dividend')}" if b.get("bm_dividend") else "",
                                        f"Bonus {b.get('bm_bonus')}" if b.get("bm_bonus") else "",
                                        f"Right {b.get('bm_right_per')}" if b.get("bm_right_per") else "") if x)
            events.append(f"Payout ex-date <b>{b['date']:%d %b %Y}</b>" + (f": {H(pay)}" if pay else "")
                          + " (buy before this date to receive it)")
        body = (f'<h1>{H(s)} <span>{H(name)}</span>{"<span class=tag>KSE-100</span>" if s in kse else ""}</h1>'
                f'<p class="sub">{H(sector)} · Rs {row["p"]:,.2f} at the {asof}</p>'
                f'<div class="panel"><div class="tiles">{tiles}</div></div>'
                f'<div class="panel"><h2>Price, last 12 months</h2>{_chart(list(hist["date"]), list(hist["ac"]))}'
                f'<p class="muted">Adjusted for dividends and bonus shares, so returns are comparable over time.</p></div>'
                f'<div class="panel"><h2>Key facts</h2><p>{" · ".join(facts)}</p>'
                + (f'<p>{"<br>".join(events)}</p>' if events else "") +
                f'<p><a href="https://dps.psx.com.pk/company/{H(s)}">All filings on PSX</a></p></div>'
                f'<div class="panel"><h2>Results vs a year earlier</h2>'
                f'{_result_rows(h) if h is not None else "<p class=muted>No results found for this company.</p>"}</div>')
        (OUT / "s" / f"{s}.html").write_text(_page(f"{s} {name}", body, 1, asof), encoding="utf-8")
    if len(rows) < 0.6 * len(syms):
        raise RuntimeError(f"only {len(rows)} company pages built; keeping the previous site")
    _index(rows, asof)
    _sectors(v[v["date"] == last_d], {s: (act.get(s, {}).get("sector_name") or "") for s in syms}, min_adtv, asof)
    _results_page(by_sym, meet, meet_lab, today, act, asof, now)
    _about(asof)
    log.info("site: %d company pages built", len(rows))
    return len(rows)


def _index(rows: list[dict], asof: str) -> None:
    sectors = sorted({r["sec"] for r in rows if r["sec"]})
    data = json.dumps(rows, separators=(",", ":"))
    body = f"""<h1>Every PSX company <span>in one table</span></h1>
<p class="sub">{len(rows)} companies · prices at the {H(asof)} · returns include dividends and bonus shares</p>
<div class="panel"><div class="controls">
<input type="search" id="q" placeholder="Search symbol or company">
<select id="sec"><option value="">All sectors</option>{''.join(f'<option>{H(s)}</option>' for s in sectors)}</select>
<select id="size"><option value="">All sizes</option><option>Large</option><option>Mid</option><option>Small</option>
<option>Micro</option></select><label><input type="checkbox" id="liq" checked> Rs 10m+ traded a day</label></div>
<div class="scroll"><table id="t"><thead><tr>
<th data-k="s" class=l>Symbol</th><th data-k="n" class="l hm">Company</th><th data-k="z" class=l>Size</th><th data-k="p">Price</th>
<th data-k="d1">Today</th><th data-k="w1">1W</th><th data-k="m1">1M</th><th data-k="m3">3M</th><th data-k="y1">1Y</th>
<th data-k="v">Rs m/day</th><th data-k="pe">P/E</th><th data-k="ry">Last result</th><th data-k="bm">Board meeting</th></tr></thead><tbody></tbody></table></div>
<p class="muted" id="count"></p></div>
<script>
const D={data};let key="v",asc=false;
const pc=x=>x==null?'<span class=muted>-</span>':'<span class="'+(x>0?'up':x<0?'down':'muted')+'">'+(x>0?'+':'')+(x*100).toFixed(1)+'%</span>';
function draw(){{const q=document.getElementById('q').value.trim().toLowerCase(),sec=document.getElementById('sec').value,
size=document.getElementById('size').value,liq=document.getElementById('liq').checked;
let r=D.filter(x=>(!q||x.s.toLowerCase().includes(q)||x.n.toLowerCase().includes(q))&&(!sec||x.sec==sec)&&(!size||x.z==size)&&(!liq||x.liq));
r.sort((a,b)=>{{let p=a[key],o=b[key];if(p==null||p==='')return 1;if(o==null||o==='')return -1;
return (p>o?1:p<o?-1:0)*(asc?1:-1)}});
document.querySelector('#t tbody').innerHTML=r.map(x=>'<tr><td><a href="s/'+x.s+'.html">'+x.s+'</a>'+(x.k?' <span class=tag>100</span>':'')+'</td><td class="l hm">'+x.n+
'</td><td class=l>'+x.z+'</td><td>'+x.p.toLocaleString(undefined,{{minimumFractionDigits:2,maximumFractionDigits:2}})+
'</td><td>'+pc(x.d1)+'</td><td>'+pc(x.w1)+'</td><td>'+pc(x.m1)+'</td><td>'+pc(x.m3)+'</td><td>'+pc(x.y1)+'</td><td>'+
(x.v==null?'-':x.v.toLocaleString())+'</td><td>'+(x.pe==null?'-':x.pe.toFixed(1))+'</td><td>'+(x.r?x.r+' '+pc(x.ry):'-')+'</td><td>'+(x.bm||'')+'</td></tr>').join('');
document.getElementById('count').textContent=r.length+' companies shown. Tap a column to sort.'}}
document.querySelectorAll('#t th').forEach(th=>th.onclick=()=>{{const k=th.dataset.k;asc=(key==k)?!asc:(k=='s'||k=='n');key=k;draw()}});
['q','sec','size','liq'].forEach(i=>document.getElementById(i).addEventListener('input',draw));draw();
</script>"""
    (OUT / "index.html").write_text(_page("Screener", body, 0, asof), encoding="utf-8")


def _results_page(by_sym, meet, meet_lab, today, act, asof, now) -> None:
    lim = pd.Timestamp(now.strftime("%Y-%m-%d")) + pd.Timedelta(days=14)
    up = sorted((d, s) for s, d in meet.items() if d <= lim and s in today.index)
    cal = "".join(f'<tr><td class=l>{d:%a %d %b}</td><td class=l><a href="s/{H(s)}.html">{H(s)}</a></td>'
                  f'<td class=l>{H((act.get(s, {}).get("company_name") or "").strip())}</td><td class=l>{H(meet_lab.get(s, ""))}</td></tr>'
                  for d, s in up)
    since = pd.Timestamp(now.strftime("%Y-%m-%d")) - pd.Timedelta(days=14)
    rec = []
    for s, h in by_sym.items():
        for _, r in h[h["date"] >= since].iterrows():
            annual = r["kind"] in ("FY", "CY")
            y = r["yoy_cum"] if annual else r["yoy_q"]
            rec.append((r["date"], s, str(r["label"]), None if pd.isna(y) else float(y)))
    rec.sort(key=lambda x: x[0], reverse=True)
    rr = "".join(f'<tr><td class=l>{d:%d %b}</td><td class=l><a href="s/{H(s)}.html">{H(s)}</a></td>'
                 f'<td class=l>{H(lab)}</td><td>{_pct(y, 0)}</td></tr>' for d, s, lab, y in rec[:150])
    body = (f'<h1>Results <span>calendar</span></h1><p class="sub">Board meetings in the next 14 days and results of '
            f'the last 14 days</p><div class="panel"><h2>Coming up</h2><div class="scroll"><table><tr><th class=l>Day</th>'
            f'<th class=l>Symbol</th><th class=l>Company</th><th class=l>Period</th></tr>{cal or "<tr><td class=l>None announced yet</td></tr>"}'
            f'</table></div></div><div class="panel"><h2>Recent results</h2><div class="scroll"><table><tr><th class=l>Date</th>'
            f'<th class=l>Symbol</th><th class=l>Period</th><th>EPS vs a year earlier</th></tr>{rr}</table></div></div>')
    (OUT / "results.html").write_text(_page("Results calendar", body, 0, asof), encoding="utf-8")


def _about(asof: str) -> None:
    body = """<h1>About <span>this data hub</span></h1><div class="panel">
<p>Prices, volumes, results and board meetings come from the Pakistan Stock Exchange (via SCS Trade and dps.psx.com.pk).
The site is rebuilt automatically every evening after the market closes.</p>
<p><b>Returns</b> include dividends and bonus shares: on each ex-date the price history is adjusted using the reference
price PSX itself sets, so a payout does not look like a fall.</p>
<p><b>Size</b> groups rank companies by market value: Large = top 30, Mid = 31 to 100, Small = 101 to 200, Micro = the rest.</p>
<p><b>Results</b> compare each quarter with the same quarter a year earlier, and annual results with the previous year.</p>
<p>Everything here is information only, not investment advice. Numbers can contain errors from the sources:
always check the official PSX filing before acting.</p></div>"""
    (OUT / "about.html").write_text(_page("About", body, 0, asof), encoding="utf-8")


def _sectors(v_now: pd.DataFrame, sectors: dict[str, str], min_adtv: float, asof: str) -> None:
    now_pe = research.sector_pe(v_now, sectors, min_adtv, weekly=False)
    t = research.sector_table(now_pe[["sector", "median", "count"]], research.load_sector_history())
    rows = []
    for _, r in t.iterrows():
        usual = "-" if pd.isna(r["usual"]) else f"{r['usual']:.1f}"
        if pd.isna(r["cheaper_share"]):
            note = '<span class="muted">history building</span>'
        else:
            pct = r["cheaper_share"] * 100
            note = (f'<span class="up">cheaper than {100 - pct:.0f}% of weeks</span>' if pct <= 30 else
                    f'<span class="down">dearer than {pct:.0f}% of weeks</span>' if pct >= 70 else "around its usual level")
        rows.append(f"<tr><td class=l>{H(r['sector'])}</td><td>{r['pe']:.1f}</td><td>{usual}</td><td class=l>{note}</td>"
                    f"<td>{r['n']}</td></tr>")
    body = (f'<h1>Sector <span>valuations</span></h1><p class="sub">Median P/E (price / last 12 months EPS) of liquid '
            f'stocks in each sector, compared with the same sector since 2018</p><div class="panel"><div class="scroll"><table>'
            f'<tr><th class=l>Sector</th><th>P/E now</th><th>Usual P/E</th><th class=l>Compared with its history</th>'
            f'<th>Stocks</th></tr>{"".join(rows)}</table></div><p class="muted">Only stocks trading Rs 10m+ a day with '
            f'positive earnings. Companies with a split or bonus issue in the last two years are left out, because their '
            f'reported EPS mixes old and new share counts. A low P/E can mean cheap, or that profits are expected to fall.</p></div>')
    (OUT / "sectors.html").write_text(_page("Sector valuations", body, 0, asof), encoding="utf-8")
