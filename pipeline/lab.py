"""Research Lab (experiment, private test chat only): one AI call per blue chip per trading day, from a
dossier that puts together everything we can see about the company, then scored against the market.

The question this answers honestly: does an AI that reads ALL the data beat simple rules?
  - the call: Outperform / Neutral / Underperform versus the KSE-100 over the next 20 sessions, confidence 1-5
  - scored automatically after 5 and 20 sessions (entry = next session's open, relative to the KSE-100)
  - compared on the same days with two baselines: SCS's own trading signal and plain 1-month momentum
Nothing here reaches the main channel. Results so far are printed on every card.

Dossier sources (past, present and expected):
  scstrade   snapshot (valuation, expected P/E / dividend / growth, profitability, debt, EPS by year and
             quarter, insiders, announcements), technical report, SCS trading signal + its 3-year record,
             SCS company reports, SCS analyst notes, sector flows (foreign investors, mutual funds),
             price and index history, results history, board meetings, ex-dates
  PSX        latest company filings
  news       Google News (14 days), broker research list (ksealert)
  macro      market context pack of the news pipeline (policy rate, CPI, rupee, reserves, oil, index trend)
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import numpy as np

from . import analysis, earnings, levels
from .common import ROOT, clean_ws, esc, log, now_pkt
from .sources import scs, scs_deep

LOG_PATH = ROOT / "state" / "lab" / "calls.json"
HORIZONS = (5, 20)
VIEWS = ("Outperform", "Neutral", "Underperform")

PROMPT = """You are the head of research at a Pakistani brokerage. Using ONLY the DOSSIER below (it contains every
fact we have: past, present and what is expected), decide how {sym} is likely to do RELATIVE TO THE KSE-100
INDEX over the next 20 trading sessions (about one month), starting from tomorrow's open.

Think like a professional: weigh valuation (now vs its own history, vs expectations), earnings momentum
(latest quarters vs a year ago), dated catalysts inside the next month (results, ex-dates, policy), price trend
and technical position, who is buying the sector (foreign investors, mutual funds), news flow, and the
market backdrop. Price moves already in the past month may already reflect known news. If the evidence is
mixed or weak, say Neutral: a wrong confident call is worse than an honest Neutral.

Return JSON with exactly these keys:
"view": "Outperform", "Neutral" or "Underperform" (relative to the KSE-100, next 20 sessions).
"confidence": 1 to 5 (5 = several independent strong reasons point the same way).
"headline": one sentence with the call and the main reason, with numbers from the dossier.
"thesis": 3 short points supporting the call, each with a number from the dossier.
"risks": 2 short points that could make the call wrong.
"catalysts": up to 3 dated events in the next month from the dossier (e.g. "12 Nov: board meeting, 3Q results"); [] if none.
"valuation": one sentence on valuation using the dossier numbers.
"momentum": one sentence on earnings and price momentum.
"technical": one sentence on the technical position (trend, support, resistance, RSI).
"change_view_if": one sentence: what would change this view.

Rules: use only numbers that appear in the dossier; never invent targets, prices or dates; no emojis, no dashes,
no exclamation marks; plain English for investors; do not tell anyone to buy or sell.

DOSSIER:
{dossier}
"""


def _cfg(cfg: dict) -> dict:
    return cfg.get("lab") or {}


def _n(v):
    """NaN / None -> None."""
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def _f(v, d=2):
    v = _n(v)
    return "n/a" if v is None else f"{v:,.{d}f}"


# ---------------------------------------------------------------- data
def _history(sym: str, days: int = 420) -> list[tuple]:
    end = datetime.now()
    rows = scs.price_history(sym, end - timedelta(days=days), end)
    out = [(r["date"].strftime("%Y-%m-%d"), r.get("trading_open"), r.get("trading_close"), r.get("trading_vol") or 0,
            r.get("trading_high"), r.get("trading_low"))
           for r in rows if r.get("date") and r.get("trading_close")]
    return adjust_jumps(sorted(out))


def adjust_jumps(px: list[tuple]) -> list[tuple]:
    """scstrade prices are not adjusted for bonus issues and splits. PSX limits a day's move to 10% (or Rs 1), so a
    bigger jump between two sessions is a corporate action: earlier prices are scaled by it (BNL fell 96% in a day)."""
    out = list(px)
    for i in range(len(out) - 1, 0, -1):
        c0, c1 = out[i - 1][2], out[i][2]
        if not c0 or not c1 or abs(c1 - c0) <= max(0.105 * c0, 1.05):
            continue
        f = (out[i][1] or c1) / c0  # the first open after the action is the cleanest ratio
        sc = lambda x: x * f if x else x  # noqa: E731
        out[:i] = [(p[0], sc(p[1]), sc(p[2]), (p[3] / f if p[3] else p[3]), *(sc(x) for x in p[4:])) for p in out[:i]]
        log.info("lab: price history adjusted by %.4f before %s (corporate action)", f, out[i][0])
    return out


def _index(days: int = 420) -> dict[str, float]:
    return {r["date"].strftime("%Y-%m-%d"): r["kse_index_close"] for r in scs.index_history(days)
            if r.get("date") and r.get("kse_index_close")}


def _rel(px: list[tuple], idx: dict, n: int) -> tuple[float | None, float | None]:
    """Stock and KSE-100 return over the last n sessions (in %)."""
    if len(px) <= n:
        return None, None
    a, b = px[-n - 1], px[-1]
    s = (b[2] / a[2] - 1) * 100
    i = (idx[b[0]] / idx[a[0]] - 1) * 100 if a[0] in idx and b[0] in idx else None
    return s, i


def _news(query: str, days: int = 14, n: int = 8) -> list[dict]:
    import feedparser

    from .common import http
    url = f"https://news.google.com/rss/search?q={quote_plus(query + f' when:{days}d')}&hl=en-PK&gl=PK&ceid=PK:en"
    try:
        feed = feedparser.parse(http().get(url, timeout=25).content)
    except Exception as e:  # noqa: BLE001
        log.warning("lab news for %s failed: %s", query, e)
        return []
    out = []
    for e in feed.entries[:n]:
        t = clean_ws(e.get("title", ""))
        src = (e.get("source") or {}).get("title", "") if isinstance(e.get("source"), dict) else ""
        out.append({"date": time.strftime("%Y-%m-%d", e.published_parsed) if e.get("published_parsed") else "",
                    "title": re.sub(r"\s+-\s+[^-]{2,60}$", "", t), "source": src})
    return out


def dossier(sym: str, shared: dict, cfg: dict) -> dict | None:
    """Everything about one company, as structured data plus the text the AI reads."""
    from . import flows
    from .sources import psx
    snap = scs_deep.snapshot(sym)
    if not snap:
        return None
    px = _history(sym)
    idx = shared["index"]
    d = {"sym": sym, "name": snap["name"], "sector": snap["sector"], "snap": snap, "px": px,
         "tech": shared["tech"].get(sym) or levels.technicals(px), "signal": scs_deep.signal(sym), "record": scs_deep.signal_record(sym),
         "reports": scs_deep.research(sym)[:4], "opinions": shared["opinions"].get(sym, [])[:2]}
    d["rel"] = {n: _rel(px, idx, n) for n in (5, 20, 60, 120, 250)}
    d["index_map"] = idx
    closes = [p[2] for p in px]
    if len(closes) > 60:
        r = np.diff(np.log(closes[-121:]))
        d["vol"] = float(np.std(r) * np.sqrt(250) * 100)
        d["vol20"] = float(np.mean([p[3] for p in px[-20:]]))
        d["vol120"] = float(np.mean([p[3] for p in px[-120:]]))
    # results history (same quarter last year)
    try:
        h = earnings.company(sym)
        d["results"] = [] if h is None else [
            {"label": r["label"], "date": f"{r['date']:%Y-%m-%d}", "eps": _n(r["eps_q"]), "eps_ly": _n(r["eps_q_ly"]),
             "cum": _n(r["eps_cum"]), "cum_ly": _n(r["eps_cum_ly"]),
             "yoy": None if _n(r["yoy_q"]) is None else round(r["yoy_q"] * 100, 1), "div": _n(r["div"])}
            for _, r in h.sort_values("date").tail(6).iterrows()]
    except Exception as e:  # noqa: BLE001
        log.warning("lab results history %s: %s", sym, e)
        d["results"] = []
    today = now_pkt().strftime("%Y-%m-%d")
    d["meetings"] = [f"{b['date']:%Y-%m-%d}" + (f" ({b.get('bm_quarter_number')})" if b.get("bm_quarter_number") else "")
                     for b in shared["meetings"] if (b.get("company_code") or "").strip() == sym and b.get("date")
                     and f"{b['date']:%Y-%m-%d}" >= today]
    # NOT taken from the snapshot's announcement titles: SCS appends today's date to old board notices
    # ("NOTICE OF BOARD MEETING at <today>"), which would invent a catalyst. The board-meeting list only.
    d["exdates"] = [f"{x['date']:%Y-%m-%d}: " + ", ".join(p for p in (
        f"cash dividend {x['bm_dividend']}" if x.get("bm_dividend") else "", f"bonus {x['bm_bonus']}" if x.get("bm_bonus") else "",
        f"rights {x['bm_right_per']}" if x.get("bm_right_per") else "") if p)
        for x in shared["xdates"] if (x.get("company_code") or "").strip() == sym and x.get("date")
        and f"{x['date']:%Y-%m-%d}" >= today]
    try:
        d["filings"] = [f"{f['date']}: {f['title'][:110]}" for f in (psx.company(sym) or {}).get("filings", [])[:6]]
    except Exception:  # noqa: BLE001
        d["filings"] = []
    terms = (_cfg(cfg).get("news_terms") or {}).get(sym) or f'"{sym}"'
    d["news"] = _news(f"{terms} Pakistan")
    words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", terms)] + [sym.lower()]
    d["brokers"] = [f"{str(b['date'])[:10]} {b['broker']}: {b['title']}" for b in shared["brokers"]
                    if any(w in b["title"].lower() for w in words)][:4]
    sec = flows.sector_key(snap["sector"])
    d["flows"] = {r["who"]: r["net"] for r in shared["flows"] if r["sector"] == sec}
    from . import fairvalue, underdog
    ver = underdog.verify(sym, snap, ((shared.get("act") or {}).get(sym) or {}).get("trading_close"),
                          shared.get("coe") or fairvalue.coe(cfg, None))
    d["fv"], d["ver"] = ver["fv"], ver
    extra = "\nFAIR VALUE AND BALANCE SHEET: " + " ".join(fairvalue.lines(d["fv"]))
    if ver["quarters"]:
        extra += "\nTRAILING 4 QUARTERS EPS (checked against the results announcements): " + ", ".join(
            f"{k} {x:.2f}" for k, x in ver["quarters"].items())
    if d["fv"].get("flags"):
        extra += "\nBALANCE SHEET FLAGS: " + "; ".join(d["fv"]["flags"])
    if ver["problems"]:
        extra += "\nDATA CHECK PROBLEMS (do not rely on the affected figures): " + "; ".join(ver["problems"])
    try:
        from . import ta
        d["chart"] = ta.read(px, idx, (shared.get("rs") or {}).get(sym))
        cl = ta.lines(d["chart"])
        if cl:
            extra += ("\nCHART READ (computed from adjusted daily prices): " + " | ".join(cl)
                      + "\nCHART EVIDENCE ON PSX 2012-2026: " + ta.TESTED["alone"] + "; " + ta.TESTED["confirm"] + "; "
                      + ta.TESTED["cancel"] + "; " + ta.TESTED["stage4"] + "; " + ta.TESTED["spring"])
    except Exception:  # noqa: BLE001
        log.exception("chart read %s failed", sym)
        d["chart"] = {}
    d["text"] = _text(d, shared) + extra
    return d


def _text(d: dict, shared: dict) -> str:
    s, v, p = d["snap"], d["snap"]["values"], d["snap"]["period"]
    L = [f"COMPANY: {d['sym']} {d['name']} | sector: {d['sector']} | as of {shared['session']}",
         f"PRICE: Rs {_f(v.get('price'))} ({_f(v.get('chg_pct'))}% today) | market cap Rs {_f(v.get('mktcap'))} bn | "
         f"beta {_f(v.get('beta'))} | free float {_f(v.get('freefloat'))} mn of {_f(v.get('nos'))} mn shares"]
    rel = d["rel"]
    parts = []
    for n, lab in ((5, "1 week"), (20, "1 month"), (60, "3 months"), (120, "6 months"), (250, "12 months")):
        a, b = rel.get(n) or (None, None)
        if a is not None:
            parts.append(f"{lab}: stock {a:+.1f}%" + (f" vs KSE-100 {b:+.1f}%" if b is not None else ""))
    L.append("PERFORMANCE: " + "; ".join(parts))
    L.append(f"RANGES: 52-week Rs {_f(v.get('52low'))} to {_f(v.get('52high'))}; 6-month {_f(v.get('6mlow'))} to "
             f"{_f(v.get('6mhigh'))}; 1-month {_f(v.get('1mlow'))} to {_f(v.get('1mhigh'))}")
    if d.get("vol"):
        L.append(f"RISK: annualised volatility {d['vol']:.0f}%; average volume 20 days {d['vol20']:,.0f} vs 120 days {d['vol120']:,.0f}")
    L.append(f"VALUATION (SCS): P/E {_f(v.get('pe'))}x ({p.get('pe', '')}); expected P/E {_f(v.get('epe'))}x ({p.get('epe', '')}); "
             f"expected earnings growth {_f(v.get('eeg'))}%; price to book {_f(v.get('pbv'))}x, expected {_f(v.get('epbv'))}x; "
             f"EV/EBITDA {_f(v.get('eve'))}x; price to sales {_f(v.get('ps'))}x")
    L.append(f"QUALITY: ROE {_f(v.get('roe'))}%, expected ROE {_f(v.get('eroe'))}%; ROA {_f(v.get('roa'))}%; net margin "
             f"{_f(v.get('npm'))}%; debt to equity {_f(v.get('tde'))}%; current ratio {_f(v.get('cr'))}x")
    L.append(f"DIVIDENDS: last full year Rs {_f(v.get('d'))} (yield {_f(v.get('dy'))}%); year to date Rs {_f(v.get('id'))}; "
             f"expected Rs {_f(v.get('ed'))} (yield {_f(v.get('edy'))}%); payout {_f(v.get('pr'))}%")
    if s.get("eps_annual"):
        L.append("EPS BY YEAR: " + ", ".join(f"{y} {e:.2f}" for y, e in s["eps_annual"].items()))
    if s.get("eps_quarters"):
        q = sorted(s["eps_quarters"].items(), key=lambda kv: (kv[0][:4], kv[0][-1]))[-8:]
        L.append("EPS BY QUARTER (fiscal year, quarter): " + ", ".join(f"{k} {e:.2f}" for k, e in q))
    if d["results"]:
        parts = []
        for r in d["results"]:
            if r["eps"] is not None:
                txt = f"{r['date']} {r['label']}: quarter EPS {_f(r['eps'])}"
                if r["eps_ly"] is not None:
                    txt += f" vs {_f(r['eps_ly'])} a year ago" + (f" ({r['yoy']:+.0f}%)" if r["yoy"] is not None else "")
            elif r["cum"] is not None:
                txt = f"{r['date']} {r['label']}: year-to-date EPS {_f(r['cum'])}" + (
                    f" vs {_f(r['cum_ly'])} a year ago" if r["cum_ly"] is not None else "")
            else:
                continue
            parts.append(txt + (f", dividend {r['div']:.0f}% of face value" if r["div"] else ""))
        if parts:
            L.append("RESULTS ANNOUNCED: " + "; ".join(parts))
    t = d["tech"]
    if t:
        L.append(f"TECHNICALS ({t.get('src', 'SCS')}): MACD daily {t.get('macddaily')}, weekly {t.get('macdweekly')}; RSI {_f(t.get('rsidaily'), 0)}; "
                 f"support {_f(t.get('support1'))} and {_f(t.get('support2'))}; resistance {_f(t.get('resistance1'))} and "
                 f"{_f(t.get('resistance2'))}; 50-day average {_f(t.get('sma50'))}; 100-day average {_f(t.get('sma100'))}")
    if sg := d["signal"]:
        L.append(f"SCS TRADING SIGNAL: {sg['signal']} since {sg['since']} at Rs {_f(sg['at'])}; {sg['flip']}")
    if rc := d["record"]:
        L.append(f"SCS SIGNAL RECORD ON THIS STOCK ({rc['years']} years): {rc['trades']} buy-to-sell trades, "
                 f"{rc['win']}% profitable, average {rc['avg']:+.2f}% per trade")
    if d["flows"]:
        L.append("SECTOR FLOWS LAST 4 WEEKS (USD million, net): " + ", ".join(
            f"{'foreign investors' if k == 'foreign' else 'mutual funds'} {x:+.1f}" for k, x in d["flows"].items()))
    if d["meetings"]:
        L.append("UPCOMING BOARD MEETINGS: " + "; ".join(d["meetings"]))
    if d["exdates"]:
        L.append("UPCOMING EX-DATES (buy before to receive): " + "; ".join(d["exdates"]))
    if s.get("insiders"):
        L.append("INSIDER TRADES: " + "; ".join(f"{i['date']} {i['who']} {i['action']} {i['qty']:,.0f} at {_f(i['rate'])}"
                                                for i in s["insiders"][:4]))
    if d["filings"]:
        L.append("LATEST PSX FILINGS: " + "; ".join(d["filings"]))
    if d["reports"]:
        L.append("SCS COMPANY REPORTS: " + "; ".join(f"{r['date']} {r['title']}" for r in d["reports"]))
    for o in d["opinions"]:
        L.append(f"SCS ANALYST NOTE {o['date']} ({o['title']}): {o['text'][:700]}")
    if d["brokers"]:
        L.append("BROKER RESEARCH TITLES: " + "; ".join(d["brokers"]))
    if d["news"]:
        L.append("NEWS, LAST 14 DAYS (headlines, unverified): " + "; ".join(
            f"{n['date']} {n['title']} ({n['source']})" for n in d["news"]))
    if shared.get("context"):
        L.append("MARKET BACKDROP:\n" + shared["context"])
    return "\n".join(L)


# ---------------------------------------------------------------- the call
def call(d: dict, cfg: dict) -> dict | None:
    if not analysis.available(cfg):
        return None
    out = analysis._call(cfg, PROMPT.format(sym=d["sym"], dossier=d["text"]), timeout=120)
    if not out:
        return None
    src = d["text"]
    view = out.get("view") if out.get("view") in VIEWS else None
    try:
        conf = max(1, min(5, int(out.get("confidence", 2))))
    except (TypeError, ValueError):
        conf = 2
    chk = lambda s: analysis._checked(s, src)  # noqa: E731  numbers must be in the dossier; advice removed
    res = {"view": view, "confidence": conf, "headline": chk(out.get("headline")),
           "thesis": [x for x in (chk(t) for t in (out.get("thesis") or [])[:3]) if x],
           "risks": [x for x in (chk(t) for t in (out.get("risks") or [])[:2]) if x],
           "catalysts": [x for x in (chk(t) for t in (out.get("catalysts") or [])[:3]) if x],
           "valuation": chk(out.get("valuation")), "momentum": chk(out.get("momentum")),
           "technical": chk(out.get("technical")), "change_view_if": chk(out.get("change_view_if"))}
    if not view or not res["headline"] or len(res["thesis"]) < 2:
        log.warning("lab: %s call dropped (view %s, headline %s, %d thesis points passed the number check)",
                    d["sym"], view, bool(res["headline"]), len(res["thesis"]))
        return None
    return res


# ---------------------------------------------------------------- log and scoring
def _load() -> dict:
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"calls": []}


def _save(st: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    tmp.replace(LOG_PATH)


def _baselines(d: dict) -> dict:
    sg = (d.get("signal") or {}).get("signal")
    a, b = d["rel"].get(20) or (None, None)
    mom = None if a is None or b is None else ("Outperform" if a - b > 0 else "Underperform")
    return {"scs": {"Buy": "Outperform", "Sell": "Underperform"}.get(sg), "momentum": mom}


def score(st: dict, idx: dict, px_cache: dict) -> None:
    """Fill in 5- and 20-session results for open calls (entry = next session's open, vs KSE-100 close)."""
    for c in st["calls"]:
        if all(f"r{h}" in c for h in HORIZONS):
            continue
        px = px_cache.get(c["sym"]) or []
        dates = [p[0] for p in px]
        if c["date"] not in dates:
            continue
        i = dates.index(c["date"])
        if i + 1 >= len(px) or c["date"] not in idx:
            continue
        entry = px[i + 1][1] or px[i + 1][2]
        for h in HORIZONS:
            if f"r{h}" in c or i + h >= len(px):
                continue
            ex = px[i + h]
            if ex[0] not in idx:
                continue
            stock = (ex[2] / entry - 1) * 100
            market = (idx[ex[0]] / idx[c["date"]] - 1) * 100
            c[f"r{h}"] = round(stock - market, 2)


def _hit(view: str | None, rel: float, band: float = 2.0) -> bool | None:
    if view == "Outperform":
        return rel > 0
    if view == "Underperform":
        return rel < 0
    if view == "Neutral":
        return abs(rel) <= band
    return None


def scoreboard(st: dict, h: int = 20) -> dict:
    done = [c for c in st["calls"] if f"r{h}" in c]
    out = {"n": len(done), "h": h}
    if not done:
        return out
    for who, key in (("ai", "view"), ("scs", "scs"), ("momentum", "momentum")):
        hits = [_hit(c.get(key) if who == "ai" else (c.get("base") or {}).get(key), c[f"r{h}"]) for c in done]
        hits = [x for x in hits if x is not None]
        out[who] = round(sum(hits) / len(hits) * 100) if hits else None
    ow = [c[f"r{h}"] for c in done if c["view"] == "Outperform"]
    uw = [c[f"r{h}"] for c in done if c["view"] == "Underperform"]
    out["ow_avg"] = round(float(np.mean(ow)), 2) if ow else None
    out["uw_avg"] = round(float(np.mean(uw)), 2) if uw else None
    out["spread"] = round(out["ow_avg"] - out["uw_avg"], 2) if ow and uw else None
    out["first"] = min(c["date"] for c in done)
    return out


def record_line(sb: dict) -> str:
    if not sb.get("n"):
        return "Scoring starts once the first calls are 20 sessions old."
    s = f"{sb['n']} calls scored over 20 sessions: AI right {sb.get('ai')}%"
    if sb.get("scs") is not None:
        s += f" · SCS signals {sb['scs']}%"
    if sb.get("momentum") is not None:
        s += f" · momentum {sb['momentum']}%"
    if sb.get("spread") is not None:
        s += f" · Outperform minus Underperform {sb['spread']:+.1f}%"
    return s


# ---------------------------------------------------------------- run
def _call_retry(d: dict, cfg: dict) -> dict | None:
    """Gemini's free tier is shared with the news pipeline: wait and retry when it is busy or a reply fails
    the number check (a second reading usually passes)."""
    for attempt in range(3):
        c = call(d, cfg)
        if c or not analysis.available(cfg):
            return c
        time.sleep(30 * (attempt + 1))
    return None


def _sessions_until(day: str, session: str) -> int:
    from datetime import date
    a, b = date.fromisoformat(session), date.fromisoformat(day)
    return sum(1 for k in range((b - a).days) if (a + timedelta(days=k + 1)).weekday() < 5)


def _bluechip_reasons(d: dict, c: dict, prev: dict | None, session: str) -> list[str]:
    """Why this card is worth sending today ([] = stay quiet)."""
    why = []
    if not prev:
        why.append("first view")
    elif prev.get("view") != c["view"]:
        why.append(f"view changed from {prev['view']} to {c['view']}")
    elif c["confidence"] >= 4 and (prev.get("conf") or 0) < 4:
        why.append(f"confidence rose to {c['confidence']}/5")
    sent_ev = set((prev or {}).get("events", []))
    for m in d.get("meetings") or []:
        day = m[:10]
        if 0 <= _sessions_until(day, session) <= 5 and f"bm:{day}" not in sent_ev:
            why.append(f"board meeting on {day}")
    for x in d.get("exdates") or []:
        day = x[:10]
        if 0 <= _sessions_until(day, session) <= 5 and f"ex:{day}" not in sent_ev:
            why.append(f"ex-date on {day}")
    return why


def _underdog_reasons(w: dict, prev: dict | None) -> list[str]:
    from .underdog import zone_state
    why = []
    z = zone_state(w)
    if w.get("trigger"):
        why.append("tested trigger: good results, up 3%+, at 1.2x book or less")
    if not prev:
        why.append("added to the watch list")
    elif prev.get("zone") != z and z != "none":
        why.append(f"asset zone: {prev.get('zone')} -> {z}")
    if (w.get("ignition") or {}).get("on"):
        why.append(f"volume burst: {w['ignition']['x']:.1f}x normal value traded, price up 3%+")
    if prev and w.get("ttm") is not None and prev.get("ttm") is not None and abs(w["ttm"] - prev["ttm"]) > 1e-6:
        why.append("new quarterly results")
    return why


NO_CALL = {"view": "No view", "confidence": 0, "thesis": [], "risks": [], "catalysts": [],
           "headline": "AI view not available today; the levels and the tested checks on this card are rule-based."}


def _scan_picks(sc: dict, st: dict, sessions: list[str], skip: list[str], n: int) -> list[dict]:
    """Stocks of today's scan that get the full research card: best-tested sections first, warnings last;
    a stock researched in the last 5 sessions for the same reason is not repeated (it stays in Following)."""
    from .scan import TESTED
    done = st.setdefault("scan_research", {})
    recent = set(sessions[-6:-1])
    queues = []
    for key in ("breakouts", "backed", "volume", "warnings"):
        q = [x for x in sorted(sc.get(key) or [], key=lambda x: x["kind"] != "breakout_backed")
             if x["sym"] not in skip and not ((done.get(x["sym"]) or {}).get("date") in recent
                                               and (done.get(x["sym"]) or {}).get("kind") == x["kind"])]
        queues.append(q)
    out: list[dict] = []
    while len(out) < n and any(queues):  # take turns, so every section is researched, breakouts first
        for q in queues:
            while q and any(o["sym"] == q[0]["sym"] for o in out):
                q.pop(0)
            if q and len(out) < n:
                x = q.pop(0)
                out.append({**x, "tested": TESTED.get(x["kind"], "")})
    return out


def _rs_ranks() -> dict:
    from . import ta
    return ta.rs_ranks()


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        log.exception("lab: %s(%s) failed", fn.__name__, a[0] if a else "")
        return None


def _levels_line(su: dict) -> str:
    parts = [f"setup {su['label']} ({su['score']:+d})"]
    if su.get("side") == "sell":
        if su.get("sell_zone"):
            parts.append(f"sell zone Rs {su['sell_zone'][0]:,.2f} to {su['sell_zone'][1]:,.2f}")
        if su.get("exit"):
            parts.append(f"support breaks below {su['exit']:,.2f}")
        return ", ".join(parts)
    if su.get("zone"):
        parts.append(f"buy zone Rs {su['zone'][0]:,.2f} to {su['zone'][1]:,.2f}")
    if su.get("risk"):
        parts.append(f"fails below {su['risk']:,.2f}")
    if su.get("r1"):
        parts.append(f"target {su['r1']:,.2f}" + (f" / {su['r2']:,.2f}" if su.get("r2") else ""))
    return ", ".join(parts)


def chunks(text: str, limit: int = 3900) -> list[str]:
    """Telegram refuses messages over 4096 characters: split at blank lines, then at lines."""
    if not text:
        return []
    out, cur = [], ""
    for block in text.split("\n\n"):
        parts = [block] if len(block) <= limit else block.split("\n")
        for part in parts:
            part = part[:limit]
            sep = "\n\n" if part is block else "\n"
            if cur and len(cur) + len(sep) + len(part) > limit:
                out.append(cur)
                cur = part
            else:
                cur = cur + sep + part if cur else part
    if cur:
        out.append(cur)
    return out


def scan_record(st: dict) -> str:
    """The daily scan's own record so far: every flag scored against the KSE-100 after 20 sessions
    (5 sessions until the first ones are 20 sessions old). Warnings are right when the stock lagged."""
    calls = st.get("calls_scan", [])
    for h in (20, 5):
        done = [c for c in calls if f"r{h}" in c]
        if done:
            break
    else:
        first = min((c["date"] for c in calls), default=None)
        return (f"Record: the first flags ({datetime.strptime(first, '%Y-%m-%d'):%d %b}) are scored after 5 and 20 sessions."
                if first else "Record: scoring starts with the first flags.")
    names = {"breakout": "breakouts", "breakout_backed": "breakouts at 1.2x book", "backed": "asset-backed",
             "volume": "volume with results", "warning": "warnings"}
    parts = []
    for k, nm in names.items():
        xs = [c[f"r{h}"] for c in done if c["kind"] == k]
        if not xs:
            continue
        right = sum((x < 0) if k == "warning" else (x > 0) for x in xs) / len(xs) * 100
        parts.append(f"{nm} {len(xs)}, average {np.mean(xs):+.1f}% vs KSE-100, {right:.0f}% right")
    return f"Record so far ({h} sessions after each flag): " + "; ".join(parts) + "."


def _report_item(d: dict, c: dict | None, it: dict | None) -> dict:
    su = d.get("setup") or {}
    return {"sym": d["sym"], "name": d.get("name", ""), "kind": (it or {}).get("kind", "bluechip"),
            "close": d["snap"]["values"].get("price"), "label": su.get("label"), "score": su.get("score"),
            "side": su.get("side"), "zone": su.get("zone"), "risk": su.get("risk"), "r1": su.get("r1"),
            "r2": su.get("r2"), "sell_zone": su.get("sell_zone"), "exit": su.get("exit"),
            "view": (c or {}).get("view"), "conf": (c or {}).get("confidence"),
            "fair": (d.get("fv") or {}).get("fair")}


def morning(cfg: dict, dry: bool = False, force: bool = False) -> None:
    """Pre-open (about 08:45 on trading days): last night's levels for every stock researched, plus what is
    new since the close: PSX filings, headlines, board meetings and ex-dates today. No AI call, nothing is
    re-valued: the evening report stays the analysis; this is the checklist for the open."""
    import os

    from .sources import psx
    from .telegram import Sender
    from .underdogcard import preopen_card
    st = _load()
    rep_ = st.get("report") or {}
    today = now_pkt().strftime("%Y-%m-%d")
    if not rep_.get("items"):
        log.info("morning: no evening report to follow up")
        return
    if rep_["session"] < (now_pkt() - timedelta(days=5)).strftime("%Y-%m-%d"):
        log.info("morning: the last report (%s) is too old", rep_["session"])
        return
    if st.get("morning_sent") == today and not force:
        log.info("morning: already sent today")
        return
    meetings = {(b.get("company_code") or "").strip(): b for b in scs.board_meetings()
                if b.get("date") and f"{b['date']:%Y-%m-%d}" == today}
    xdates = {(x.get("company_code") or "").strip(): x for x in scs.book_closures()
              if x.get("date") and today <= f"{x['date']:%Y-%m-%d}" <= (now_pkt() + timedelta(days=1)).strftime("%Y-%m-%d")}
    news_lines: list[str] = []
    for it in rep_["items"]:
        sym, new = it["sym"], []
        try:
            new += [f"PSX filing {f['date']}: {f['title'][:90]}" for f in (psx.company(sym) or {}).get("filings", [])
                    if str(f.get("date", "")) > rep_["session"]][:2]
        except Exception:  # noqa: BLE001
            pass
        core = " ".join(w for w in re.sub(r"\b(Ltd|Limited|Company|Co|Corporation|Corp)\b\.?", "", it.get("name") or "").split()[:2])
        terms = (_cfg(cfg).get("news_terms") or {}).get(sym) or (f'"{core}"' if core else f'"{sym}"')
        for n in _news(terms + " Pakistan", days=1, n=4):
            words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", core)]
            seen = {re.sub(r"[^a-z]", "", x.lower())[:30] for x in new}
            key = re.sub(r"[^a-z]", "", n["title"].lower())[:30]
            if n["date"] >= rep_["session"] and key not in seen and not any(
                    len(set(n["title"].lower().split()) & set(x.lower().split())) >= 4 for x in new) and (
                    sym in n["title"] or (words and all(w in n["title"].lower() for w in words))):
                new.append(f"News {n['date']}: {n['title'][:100]} ({n['source']}, unverified)")
        if sym in meetings:
            b = meetings[sym]
            new.append("Board meeting today" + (f" ({b.get('bm_quarter_number')})" if b.get("bm_quarter_number") else "")
                       + ": results can move the price; the levels were set before them")
        if sym in xdates:
            new.append(f"Ex-date {xdates[sym]['date']:%d %b}: the price drops by about the payout; levels are before it")
        it["new"] = new[:3]
        if new:
            news_lines.append(f"<b>{sym}</b>: " + "; ".join(esc(x) for x in new[:3]))
    head = f"<b>Pre-open · {now_pkt():%a %d %b}</b>\nLevels from last night's research (close of {rep_['session']}):"
    rows = []
    for it in rep_["items"]:
        lv = (f"sell zone {it['sell_zone'][0]:,.2f} to {it['sell_zone'][1]:,.2f}, breaks below {it['exit']:,.2f}"
              if it.get("side") == "sell" and it.get("sell_zone") and it.get("exit") else
              (f"buy {it['zone'][0]:,.2f} to {it['zone'][1]:,.2f}, fails below {it['risk']:,.2f}"
               + (f", target {it['r1']:,.2f}" if it.get("r1") else "") if it.get("zone") and it.get("risk") else "no levels"))
        rows.append(f"<b>{it['sym']}</b> {it.get('label') or ''} (closed {it['close']:,.2f}): {lv}")
    text = (head + "\n" + "\n".join(rows) + "\n\n<b>New since the close</b>\n"
            + ("\n".join(news_lines) if news_lines else "No new PSX filing, headline, board meeting or ex-date for these stocks.")
            + "\n\n<i>Levels are from chart support and resistance (not tested); the setup comes from tested signals. "
              "Not investment advice.</i>")
    card = preopen_card(cfg, rep_, now_pkt().strftime("%Y-%m-%d"))
    s = Sender(dry)
    chat = os.environ.get("TELEGRAM_RESEARCH_CHAT_ID", "").strip() or s.admin
    if dry or not chat:
        out = ROOT / "out" / "lab"
        out.mkdir(parents=True, exist_ok=True)
        (out / "preopen.png").write_bytes(card)
        (out / "preopen.txt").write_text(text, encoding="utf-8")
        log.info("morning (dry run): written to %s", out)
    else:
        s._photo(chat, card, f"Pre-open levels · {now_pkt():%a %d %b}", "lab_preopen")
        for part in chunks(text):
            s._post(chat, part, False)
        st["morning_sent"] = today
        _save(st)
    log.info("morning: %d stocks, %d with news since the close", len(rows), len(news_lines))


def run(cfg: dict, dry: bool = False, force: bool = False, symbols: list[str] | None = None,
        only: bool = False) -> None:
    """Daily (after the close): calls for the blue chips and the underdog scan every trading day, logged and
    scored; a card is SENT only when something matters (see _bluechip_reasons / _underdog_reasons), plus a
    Friday roundup. only=True with symbols: full analysis of those stocks now, always sent."""
    import os

    from . import context, fairvalue, flows, selftest, underdog
    from .labcard import lab_card
    from .sources import brokers
    from .state import State
    from .telegram import Sender
    from .underdogcard import radar_card, watch_card
    conf = _cfg(cfg)
    s = Sender(dry)
    chat = os.environ.get("TELEGRAM_RESEARCH_CHAT_ID", "").strip() or s.admin

    def send(text: str | None, cards: list[tuple[str, bytes, str]]) -> None:
        if s.dry or not chat:
            out = ROOT / "out" / "lab"
            out.mkdir(parents=True, exist_ok=True)
            for name, png, _ in cards:
                (out / f"{name}.png").write_bytes(png)
            if text:
                (out / "summary.txt").write_text(text, encoding="utf-8")
            log.info("lab (dry run): %d cards in %s", len(cards), out)
            return
        for part in chunks(text or ""):
            s._post(chat, part, False)
        for name, png, cap in cards:
            s._photo(chat, png, cap[:1000], f"lab_{name}")

    bad = selftest.run()
    if bad:
        log.error("lab: self-test failed, nothing sent")
        if not dry and s.admin:
            s.send_admin("<b>Research Lab paused today: self-test failed</b>\n" + "\n".join(esc(b) for b in bad[:8])
                         + "\nNothing was sent, so no wrong number reached you.")
        return
    st = _load()
    idx = _index()
    if not idx:
        log.warning("lab: no index data")
        return
    session = max(idx)
    if only:  # on request during the day: today's live figures, dated today
        session = now_pkt().strftime("%Y-%m-%d")
    if not only:
        if st.get("last") == session and not force:
            log.info("lab: session %s already done", session)
            return
        if session < (now_pkt() - timedelta(days=4)).strftime("%Y-%m-%d") and not force:
            log.info("lab: latest session %s is old (holiday?)", session)
            return
    syms = symbols or conf.get("symbols") or ["FFC", "UBL", "MEBL", "ENGROH", "OGDC"]
    view = scs.kse100_view()
    analysis.init(State(), cfg)
    policy = fairvalue.risk_free(State().snap("sbp"))  # 10-year PIB yield, else the policy rate
    end = now_pkt().replace(tzinfo=None)
    act = {r["company_code"].strip(): r for r in scs.daily_activity() if r.get("company_code")}
    shared = {"index": idx, "session": session, "tech": scs_deep.technicals(), "opinions": scs_deep.opinions(),
              "meetings": scs.board_meetings(), "xdates": scs.book_closures(), "brokers": brokers.reports(),
              "flows": flows.week(end - timedelta(days=28), end), "act": act,
              "context": context.build(State(), view, (State().snap("mk_last") or {})),
              "coe": fairvalue.coe(cfg, policy), "rs": _try(_rs_ranks)}
    with ThreadPoolExecutor(3) as ex:
        dossiers = dict(zip(syms, ex.map(lambda x: _try(dossier, x, shared, cfg), syms)))
    if not only:
        score(st, idx, {x: d["px"] for x, d in dossiers.items() if d})
    sb = scoreboard(st)
    sent = st.setdefault("sent", {})
    out_cards: list[tuple[str, bytes, str]] = []
    notes: list[str] = []
    views: list[str] = []
    report: list[dict] = []
    for sym in syms:
        d = dossiers.get(sym)
        if not d:
            notes.append(f"{sym}: data could not be collected")
            continue
        c = _call_retry(d, cfg)
        if not c:
            notes.append(f"{sym}: no view (AI unavailable or its text failed the number check)")
            continue
        entry = {"id": f"{sym}:{session}", "sym": sym, "date": session, "price": d["snap"]["values"].get("price"),
                 "view": c["view"], "conf": c["confidence"], "headline": c["headline"], "base": _baselines(d)}
        if only:
            entry["on_demand"] = True
        if not any(x["id"] == entry["id"] for x in st["calls"]):
            st["calls"].append(entry)
        views.append(f"<b>{sym}</b>: {c['view']} ({c['confidence']}/5). {esc(c['headline'])}")
        try:
            from .scan import _last_result
            d["setup"] = levels.setup(d, c, None, _last_result(sym, session))
            report.append(_report_item(d, c, None))
        except Exception:  # noqa: BLE001
            log.exception("lab setup %s failed", sym)
        why = ["on request"] if only else _bluechip_reasons(d, c, sent.get(sym), session)
        if not why or not d.get("setup"):
            continue
        d["fv_rank"] = None
        try:
            out_cards.append((sym, lab_card(cfg, d, c, sb, session), f"{sym} · {'; '.join(why)}"))
        except Exception:  # noqa: BLE001
            log.exception("lab card %s failed", sym)
            continue
        if not only:
            prev = sent.get(sym) or {}
            evs = set(prev.get("events", [])) | {f"bm:{w.split()[-1]}" for w in why if w.startswith("board meeting")} | {
                f"ex:{w.split()[-1]}" for w in why if w.startswith("ex-date")}
            sent[sym] = {"view": c["view"], "conf": c["confidence"], "date": session, "events": sorted(evs)[-10:]}
        notes.append(f"<b>{sym}</b>: {c['view']} ({c['confidence']}/5), {'; '.join(why)}"
                     + (f". {_levels_line(d['setup'])}" if d.get("setup") else ""))
    # ---- underdogs: scan every day, send only on a trigger or a change in a watched stock
    ud_conf = conf.get("underdog") or {}
    scan_text = None
    friday = now_pkt().weekday() == 4
    if ud_conf.get("enabled", True):
        try:
            if only:
                r = {"watch": underdog.details_for(cfg, session, policy, syms), "skipped": {}}
            else:
                r = underdog.radar(cfg, session, policy)
            ud = st.setdefault("ud_state", {})
            for w in r["watch"]:
                why = ["on request"] if only else _underdog_reasons(w, ud.get(w["sym"]))
                if w.get("trigger"):
                    st.setdefault("underdog", []).append({"id": f"UD:{w['sym']}:{session}", "sym": w["sym"], "date": session,
                                                          "price": w["price"], "view": "Outperform", "pb": w["fv"].get("pb")})
                if not only:
                    ud[w["sym"]] = {"zone": underdog.zone_state(w), "ttm": w.get("ttm"), "date": session}
                if why:
                    out_cards.append((f"ud_{w['sym']}", watch_card(cfg, w, session), f"{w['sym']} · {'; '.join(why)}"))
                    notes.append(f"<b>{w['sym']}</b> (underdog): {'; '.join(why)}")
            if (friday or force) and not only:
                out_cards.append(("radar", radar_card(cfg, r), "Underdog radar · weekly"))
            if not only and ud_conf.get("daily_scan", True):
                from . import scan
                from .underdogcard import scan_card
                open_sc = [x for x in st.get("calls_scan", []) + st.get("calls_scan_ai", [])
                           if not all(f"r{h}" in x for h in HORIZONS)]
                if open_sc:
                    score({"calls": open_sc}, idx, {x["sym"]: _history(x["sym"]) for x in open_sc[-40:]})
                sc = scan.build(cfg, session, r, st, idx)
                sc["record"] = scan_record(st)
                out_cards.insert(0, ("scan", scan_card(cfg, sc), f"Daily stock scan · {session}"))
                scan_text = scan.text(sc) + f"\n\n<i>{esc(sc['record'])}</i>"
                picks = _scan_picks(sc, st, sorted(idx), syms, int(ud_conf.get("research_max", 6)))
                if picks:
                    def _safe_dossier(x):  # one stock's bad data must not stop the others
                        try:
                            return dossier(x["sym"], shared, cfg)
                        except Exception:  # noqa: BLE001
                            log.exception("scan research dossier %s failed", x["sym"])
                            return None
                    with ThreadPoolExecutor(3) as ex:
                        sd = dict(zip([x["sym"] for x in picks], ex.map(_safe_dossier, picks)))
                    rlines, pos = [], 1
                    for it in picks:
                        sym, d = it["sym"], sd.get(it["sym"])
                        if not d:
                            rlines.append(f"{sym}: data could not be collected")
                            continue
                        c = _call_retry(d, cfg)
                        d["kicker"], d["fv_rank"] = "Research Lab · daily scan", None
                        d["setup"] = levels.setup(d, c, it, scan._last_result(sym, session))
                        try:
                            card = lab_card(cfg, d, c or NO_CALL, sb, session)
                        except Exception:  # noqa: BLE001
                            log.exception("scan research card %s failed", sym)
                            continue
                        out_cards.insert(pos, (f"scan_{sym}", card, f"{sym} · {levels.SECTION.get(it['kind'])}"))
                        pos += 1
                        st["scan_research"][sym] = {"date": session, "kind": it["kind"]}
                        report.append(_report_item(d, c, it))
                        if c:
                            st.setdefault("calls_scan_ai", []).append(
                                {"id": f"SA:{sym}:{session}", "sym": sym, "date": session, "price": d["snap"]["values"].get("price"),
                                 "view": c["view"], "conf": c["confidence"], "kind": it["kind"], "setup": d["setup"]["score"],
                                 "base": _baselines(d)})
                        rlines.append(f"<b>{sym}</b> ({levels.SECTION.get(it['kind'])}): "
                                      + (f"AI {c['view']} {c['confidence']}/5; " if c else "no AI view; ")
                                      + _levels_line(d["setup"]))
                    if rlines:
                        scan_text += "\n\n<b>Full research on today's scan stocks</b> (one card each)\n" + "\n".join(rlines)
            open_ud = [x for x in st.get("underdog", []) if not all(f"r{h}" in x for h in HORIZONS)]
            if open_ud and not only:
                score({"calls": open_ud}, idx, {x["sym"]: _history(x["sym"]) for x in open_ud})
            if r["skipped"]:
                log.info("lab: %d stocks not shown because a data check failed", len(r["skipped"]))
        except Exception:  # noqa: BLE001  the blue-chip part must still go out
            log.exception("lab: underdog radar failed")
            notes.append("Underdog scan could not be completed today.")
    if not only:
        st["last"] = session
        st["report"] = {"session": session, "made": now_pkt().strftime("%Y-%m-%d %H:%M"), "items": report}
    # ---- what goes out: only the cards that matter; Friday adds the roundup
    head = f"<b>Research Lab · {session}</b>" + (" · on request" if only else "")
    if (friday or force) and not only:
        text = (head + " · weekly roundup\nOne-month views versus the KSE-100 (made every day, sent when they change):\n\n"
                + ("\n\n".join(views) or "No views could be made today (the AI was unavailable).")
                + f"\n\n<i>{record_line(sb)}</i>"
                + ("\n\nToday: " + "\n".join(notes) if notes else ""))
    elif out_cards or only:
        text = head + "\n" + "\n".join(notes) if notes else head
    else:
        text = None
    if scan_text:  # the daily scan goes out every trading day; other cards only when something matters
        text = scan_text + ("\n\n" + text if text and text != head else "")
    if text or out_cards:
        send(text, out_cards)
        log.info("lab: sent %d cards", len(out_cards))
    else:
        log.info("lab: nothing changed today, nothing sent (views logged for scoring)")
    if not dry:
        _save(st)
