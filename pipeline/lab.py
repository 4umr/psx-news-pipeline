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

from . import analysis, earnings
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
    out = [(r["date"].strftime("%Y-%m-%d"), r.get("trading_open"), r.get("trading_close"), r.get("trading_vol") or 0)
           for r in rows if r.get("date") and r.get("trading_close")]
    return sorted(out)


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
         "tech": shared["tech"].get(sym) or {}, "signal": scs_deep.signal(sym), "record": scs_deep.signal_record(sym),
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
    from . import fairvalue
    d["fv"] = fairvalue.compute(snap, shared.get("coe") or fairvalue.coe(cfg, None))
    d["text"] = _text(d, shared) + "\nFAIR VALUE AND BALANCE SHEET: " + " ".join(fairvalue.lines(d["fv"])) + (
        " Flags: " + "; ".join(d["fv"]["flags"]) if d["fv"].get("flags") else "")
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
        L.append(f"TECHNICALS (SCS): MACD daily {t.get('macddaily')}, weekly {t.get('macdweekly')}; RSI {_f(t.get('rsidaily'), 0)}; "
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
def run(cfg: dict, dry: bool = False, force: bool = False, symbols: list[str] | None = None) -> None:
    from . import context
    from .sources import brokers
    from .state import State
    from .telegram import Sender
    conf = _cfg(cfg)
    syms = symbols or conf.get("symbols") or ["FFC", "UBL", "MEBL", "ENGROH", "OGDC"]
    st = _load()
    idx = _index()
    if not idx:
        log.warning("lab: no index data")
        return
    session = max(idx)
    if st.get("last") == session and not force:
        log.info("lab: session %s already done", session)
        return
    if session < (now_pkt() - timedelta(days=4)).strftime("%Y-%m-%d") and not force:
        log.info("lab: latest session %s is old (holiday?)", session)
        return
    view = scs.kse100_view()
    analysis.init(State(), cfg)
    from . import fairvalue
    policy = (State().snap("sbp") or {}).get("policy_rate")
    end = now_pkt().replace(tzinfo=None)
    from . import flows
    shared = {"index": idx, "session": session, "tech": scs_deep.technicals(), "opinions": scs_deep.opinions(),
              "meetings": scs.board_meetings(), "xdates": scs.book_closures(), "brokers": brokers.reports(),
              "flows": flows.week(end - timedelta(days=28), end),
              "context": context.build(State(), view, (State().snap("mk_last") or {})),
              "coe": fairvalue.coe(cfg, policy)}
    with ThreadPoolExecutor(3) as ex:
        dossiers = dict(zip(syms, ex.map(lambda s: dossier(s, shared, cfg), syms)))
    # score the open calls with fresh prices before adding today's
    score(st, idx, {s: d["px"] for s, d in dossiers.items() if d})
    sb = scoreboard(st)
    cards, lines, pending = [], [], []
    from .labcard import lab_card
    for sym in syms:
        d = dossiers.get(sym)
        if not d:
            lines.append(f"{sym}: data could not be collected today")
            continue
        c = call(d, cfg)
        time.sleep(4)
        if not c:
            lines.append(f"{sym}: no call today (AI unavailable or its text failed the number check)")
            continue
        entry = {"id": f"{sym}:{session}", "sym": sym, "date": session, "price": d["snap"]["values"].get("price"),
                 "view": c["view"], "conf": c["confidence"], "headline": c["headline"], "base": _baselines(d)}
        if not any(x["id"] == entry["id"] for x in st["calls"]):
            st["calls"].append(entry)
        pending.append((sym, d, c))
        lines.append(f"<b>{sym}</b>: {c['view']} (confidence {c['confidence']}/5). {esc(c['headline'])}")
    st["last"] = session
    # ---- underdog radar (asset-backed small and mid caps) + watch stocks
    ud_cards, ud_lines = [], []
    if (conf.get("underdog") or {}).get("enabled", True):
        from . import underdog
        from .underdogcard import radar_card, watch_card
        try:
            r = underdog.radar(cfg, session, policy)
            ranks = {x["sym"]: x.get("upside_rank") for x in r["rows"]}
            for sym, d, _ in pending:
                if ranks.get(sym) is not None:
                    d["fv_rank"] = ranks[sym]
            ud_cards.append(("radar", radar_card(cfg, r)))
            for w in r["watch"]:
                ud_cards.append((w["sym"], watch_card(cfg, w, session)))
            for x in r["triggers"]:
                st.setdefault("underdog", []).append({"id": f"UD:{x['sym']}:{session}", "sym": x["sym"], "date": session,
                                                      "price": x["price"], "view": "Outperform", "pb": x["pb"]})
                ud_lines.append(f"<b>{x['sym']}</b>: {esc(x['eps_line'])}, {x['react']:+.1f}% today, P/B {x['pb']:.2f}")
            ud_lines.append(f"{r['n_backed']} liquid small and mid caps trade at 1.2x book or less; "
                            f"{len(r['triggers'])} met the tested trigger today.")
        except Exception:  # noqa: BLE001  the blue-chip part must still go out
            log.exception("lab: underdog radar failed")
            ud_lines.append("Underdog radar could not be built today.")
        # score earlier underdog triggers too
        open_ud = [c for c in st.get("underdog", []) if not all(f"r{h}" in c for h in HORIZONS)]
        if open_ud:
            score({"calls": open_ud}, idx, {c["sym"]: _history(c["sym"]) for c in open_ud})
    for sym, d, c in pending:
        try:
            cards.append((sym, lab_card(cfg, d, c, sb, session)))
        except Exception:  # noqa: BLE001
            log.exception("lab card %s failed", sym)
    cards += ud_cards
    s = Sender(dry)
    chat = __import__("os").environ.get("TELEGRAM_RESEARCH_CHAT_ID", "").strip() or s.admin
    if s.dry or not chat:
        from .common import ROOT as _R
        out = _R / "out" / "lab"
        out.mkdir(parents=True, exist_ok=True)
        for sym, png in cards:
            (out / f"{sym}.png").write_bytes(png)
        (out / "summary.txt").write_text("\n".join(lines) + "\n" + record_line(sb), encoding="utf-8")
        log.info("lab (dry run): %d cards in %s", len(cards), out)
    else:
        s._post(chat, f"<b>Research Lab · {session}</b> (experiment, not for the main channel)\n"
                      "One-month view versus the KSE-100 for the top 5 index stocks.\n\n" + "\n\n".join(lines)
                + f"\n\n<i>{record_line(sb)}</i>"
                + ("\n\n<b>Underdog radar</b>\n" + "\n".join(ud_lines) if ud_lines else ""), False)
        for sym, png in cards:
            s._photo(chat, png, f"{sym} · Research Lab · {session}", f"lab_{sym}")
    if not dry:
        _save(st)
    log.info("lab: %d calls today, %d cards, record: %s", len(lines), len(cards), record_line(sb))
