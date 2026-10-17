"""Stock of the Day: one company a day, a full research report from official sources.

Order: every KMI-30 company first, then the rest of the KSE-100; no repeats until the list is done, then a
new round. Inside the list: largest first, never the same sector two days running; a company that reported
results in the last 3 days goes next; a company with results due in the next 2 days waits.

Sources: PSX company page (profile, people, 4 years + 4 quarters, ratios), the latest annual report filed with
PSX (read by `reader.py`, every number checked), our price database (returns, P/E history, liquidity),
scstrade results history (EPS vs a year earlier, dividends), board meetings and ex-dates.
Published at 18:00 PKT every day (workflow stock.yml): an album of cards to the channel and a page on the
website. Information only: no buy or sell views.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pandas as pd

from . import earnings, prices, reader, research  # noqa: F401
from .common import ROOT, esc, log, now_pkt
from .signals import features
from .sources import psx, scs

DIR = ROOT / "state" / "sotd"
STATE = DIR / "state.json"
PUBLISHED = DIR / "published"
LOGOS = ROOT / "assets" / "logos"



# ---------------------------------------------------------------- state and picking
def _load() -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    st.setdefault("round", 1)
    st.setdefault("covered", [])     # [{sym, date, round}]
    st.setdefault("attempts", {})    # date -> tries
    return st


def _prune(keep_days: int = 30) -> None:
    """Reports older than a month keep only their cover card (the full text stays on the web page)."""
    cut = f"{now_pkt() - timedelta(days=keep_days):%Y-%m-%d}"
    for js in PUBLISHED.glob("*.json") if PUBLISHED.exists() else []:
        try:
            if json.loads(js.read_text(encoding="utf-8")).get("published", "9999") >= cut:
                continue
        except (OSError, json.JSONDecodeError):
            continue
        for i in range(2, 8):
            (PUBLISHED / f"{js.stem}-{i}.png").unlink(missing_ok=True)


def _save(st: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    _prune()
    cut = f"{now_pkt() - timedelta(days=10):%Y-%m-%d}"
    st["attempts"] = {d: n for d, n in st["attempts"].items() if d >= cut}
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def queue(st: dict, known: set[str], mcap: dict[str, float], sectors: dict[str, str]) -> list[str]:
    """Companies still to cover this round, in the order they would be picked."""
    done = {c["sym"] for c in st["covered"] if c.get("round") == st["round"]}
    kmi = [s for s in psx.index_members("KMI30", known) if s in known]
    kse = [s for s in psx.index_members("KSE100", known) if s in known]
    if not kmi and not kse:
        raise RuntimeError("index member lists unavailable from PSX")
    stage = [s for s in kmi if s not in done] or [s for s in kse if s not in done]
    if not stage:  # the whole KSE-100 is done: start the next round
        st["round"] += 1
        stage = list(dict.fromkeys(kmi + kse))
    stage.sort(key=lambda s: -(mcap.get(s) or 0))
    last_sector = sectors.get(st["covered"][-1]["sym"], "") if st["covered"] else ""
    first = [s for s in stage if sectors.get(s, "") != last_sector] or stage
    return first + [s for s in stage if s not in first]


def pick(st: dict, known: set[str], mcap: dict[str, float], sectors: dict[str, str], now) -> list[str]:
    """Candidates for today, best first (results just out first; results due within 2 days later)."""
    q = queue(st, known, mcap, sectors)
    today = pd.Timestamp(now.strftime("%Y-%m-%d"))
    fresh, due = set(), set()
    try:
        for r in scs.results():
            if r.get("date") and pd.Timestamp(r["date"].strftime("%Y-%m-%d")) >= today - pd.Timedelta(days=3):
                fresh.add((r.get("company_code") or "").strip())
        for b in scs.board_meetings():
            if b.get("date"):
                d = pd.Timestamp(b["date"].strftime("%Y-%m-%d"))
                if today <= d <= today + pd.Timedelta(days=2):
                    due.add((b.get("company_code") or "").strip())
    except Exception as e:  # noqa: BLE001  the plain queue still works
        log.warning("results calendar for the picker failed: %s", e)
    order = [s for s in q if s in fresh] + [s for s in q if s not in fresh and s not in due] + [s for s in q if s in due and s not in fresh]
    return list(dict.fromkeys(order))


# ---------------------------------------------------------------- the report data
def _bn(x) -> str:
    """Rs thousands (as PSX reports them) -> 'Rs 136.5 bn' / 'Rs 845 m'."""
    if x is None or x != x:
        return "n/a"
    rs = x * 1000
    return f"Rs {rs / 1e9:,.1f} bn" if abs(rs) >= 1e9 else f"Rs {rs / 1e6:,.0f} m"


def collect(sym: str, cfg: dict, df: pd.DataFrame, v: pd.DataFrame, res: pd.DataFrame, kse: pd.Series,
            sectors: dict[str, str], read_report: bool = True, kmi: set[str] | None = None) -> dict:
    """Everything the cards and the web page need, from official data. Raises if the basics are missing."""
    from .site import quick_facts
    prof = psx.profile(sym)
    if not prof or not prof.get("name"):
        raise RuntimeError(f"PSX profile for {sym} not available")
    min_adtv = float(cfg.get("swing", {}).get("min_adtv", 10e6))
    hist = df[df["sym"] == sym].tail(250)
    if hist.empty:
        raise RuntimeError(f"no price history for {sym}")
    last = hist.iloc[-1]
    end = hist["date"].iloc[-1]
    piv = df.pivot_table(index="date", columns="sym", values="ac").ffill()
    rets = {}
    for n, lab in ((5, "1 week"), (21, "1 month"), (63, "3 months"), (250, "1 year")):
        if len(piv) > n and sym in piv.columns and pd.notna(piv[sym].iloc[-1 - n]):
            rets[lab] = float(piv[sym].iloc[-1] / piv[sym].iloc[-1 - n] - 1)
    k = kse.reindex(hist["date"]).ffill()
    kse_1y = float(k.iloc[-1] / k.iloc[0] - 1) if len(k.dropna()) > 2 else None
    sector_name = (sectors.get(sym) or prof.get("sector") or "").strip().title()
    h = res[res["sym"] == sym] if res is not None else None
    # P/E on the company's own last full-year EPS as filed with PSX (same basis as the financials card).
    # Not shown if a bonus or split happened after that year ended (EPS and price would be on different share counts).
    pe, eps_year, eps = None, "", None
    ann = prof.get("annual") or {}
    if ann:
        eps_year = sorted(ann)[-1]
        eps = ann[eps_year].get("EPS")
        yem = next((m for k_, m in _MONTHS.items() if k_ in (prof.get("year_end") or "").lower()), 12)
        fy_end = pd.Timestamp(year=int(eps_year), month=yem, day=1) + pd.offsets.MonthEnd(0) if eps_year.isdigit() else end
        action_since = (df[(df["sym"] == sym) & (df["date"] > fy_end)]["exf"] < 0.92).any()
        if eps and eps > 0 and not action_since:
            pe = float(last["c"]) / eps
    # 12-month range in plain prices (what members see on their screens); dividend-adjusted only across a split/bonus
    big_action = (hist["exf"] < 0.92).any()
    hi = float(hist["ah"].max() if big_action else hist["h"].max())
    lo = float(hist["al"].min() if big_action else hist["l"].min())
    now_px = float(last["ac"] if big_action else last["c"])
    divs = []
    if h is not None and len(h):
        for yy, g in h.groupby("yy"):
            d = g["div"].fillna(0).sum()
            divs.append((int(2000 + yy) if yy == yy else 0, float(d)))
        divs = [(y, d) for y, d in divs if y][-5:]
    facts = _facts(prof, divs, hist, float(last["adtv20"]) if pd.notna(last["adtv20"]) else None, min_adtv, quick_facts)
    latest = earnings.assess(sym) if h is not None and len(h) else None
    result_lines, latest_label = _result_lines(prof)
    events = []
    today = pd.Timestamp(now_pkt().strftime("%Y-%m-%d"))
    try:
        for b in scs.board_meetings():
            if (b.get("company_code") or "").strip() == sym and b.get("date"):
                d = pd.Timestamp(b["date"].strftime("%Y-%m-%d"))
                if d >= today:
                    events.append(f"Board meeting on {d:%a %d %b %Y}" + (f" ({b.get('bm_quarter_number').strip()} results expected)"
                                                                         if (b.get("bm_quarter_number") or "").strip() else ""))
        for b in scs.book_closures():
            if (b.get("company_code") or "").strip() == sym and b.get("date") and b["date"].date() >= today.date():
                pay = " ".join(x for x in (f"Dividend {b['bm_dividend'].strip()}" if (b.get("bm_dividend") or "").strip() else "",
                                            f"Bonus {b['bm_bonus'].strip()}" if (b.get("bm_bonus") or "").strip() else "") if x)
                events.append(f"Payout ex-date {b['date']:%a %d %b %Y}: {pay} (buy before this date to receive it)")
    except Exception:  # noqa: BLE001
        pass
    report = None
    if read_report:
        try:
            report = reader.read(sym, prof["name"], cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("annual report reading failed for %s: %s", sym, e)
    logo = LOGOS / f"{sym}.png"
    return {
        "sym": sym, "name": prof["name"], "sector": sector_name, "profile": prof, "date": f"{end:%Y-%m-%d}",
        "price": float(last["c"]), "chg": float(last["chg"]) if pd.notna(last["chg"]) else 0.0,
        "mcap": float(last["mcap"]) if pd.notna(last.get("mcap")) else None, "tier": last["tier"],
        "adtv": float(last["adtv20"]) if pd.notna(last["adtv20"]) else None,
        "hi": hi, "lo": lo, "ac_last": now_px, "range_adjusted": bool(big_action),
        "eps": eps, "eps_year": eps_year, "published": f"{now_pkt():%Y-%m-%d}",
        "series": [(f"{d:%Y-%m-%d}", float(x)) for d, x in zip(hist["date"], hist["ac"])],
        "kse_series": [(f"{d:%Y-%m-%d}", float(x)) for d, x in k.dropna().items()],
        "returns": rets, "kse_1y": kse_1y, "pe": pe, "facts": facts, "latest": latest, "divs": divs,
        "result_lines": result_lines, "latest_label": latest_label,
        "events": events, "report": report, "logo": str(logo) if logo.exists() else "",
        "kmi": sym in (kmi or set()),
    }


def _facts(prof: dict, divs: list, hist: pd.DataFrame, adtv, min_adtv: float, quick_facts) -> list[tuple[str, str]]:
    """Plain facts from the PSX figures (one basis), plus price trend and liquidity."""
    out = []
    if "defaulter" in prof.get("name", "").lower():
        out.append(("down", "<b>On PSX's defaulter segment</b>: the company has not met listing requirements (extra risk)"))
    ann = prof.get("annual") or {}
    ys = sorted(ann)
    pat = [ann[y].get("Profit after Taxation") for y in ys]
    pat = [x for x in pat if x is not None]
    if len(pat) >= 3:
        ups = sum(1 for a, b in zip(pat[1:], pat[:-1]) if a > b)
        out.append(("up" if ups == len(pat) - 1 else ("down" if ups == 0 else "muted"),
                    f"Profit grew in <b>{ups} of the last {len(pat) - 1}</b> years"))
        loss = sum(1 for x in pat if x < 0)
        out.append(("up" if loss == 0 else "down", f"Made a profit in <b>{len(pat) - loss} of the last {len(pat)}</b> years"))
    if len(divs) >= 3:
        paid = sum(1 for _, d in divs if d > 0)
        out.append(("up" if paid >= len(divs) - 1 else ("down" if paid == 0 else "muted"),
                    f"Paid a dividend in <b>{paid} of the last {len(divs)}</b> years"))
    # price trend and liquidity lines from the shared quick check (no results or P/E lines from it)
    out += [f for f in quick_facts(None, hist, None, None, None, adtv, min_adtv, "") if "200-day" in f[1] or "Trades" in f[1]]
    return out


def _chg(a, b) -> str:
    if a is None or b is None or b == 0:
        return ""
    if b < 0 < a:
        return " (back to profit)"
    if a < 0 < b:
        return " (turned to a loss)"
    if b < 0:
        return ""
    c = (a / b - 1) * 100
    return " (unchanged)" if abs(c) < 0.5 else f" ({c:+.0f}%)"


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def _period_end(label: str, year_end_month: int) -> tuple[int, int]:
    """(year, month) a PSX period ends: '2026' = the financial year 2026; 'Q3 2026' = its third quarter."""
    parts = label.split()
    if len(parts) == 2 and parts[0].startswith("Q") and parts[1].isdigit():
        k, fy = int(parts[0][1:]), int(parts[1])
        m = year_end_month - 3 * (4 - k)
        y = fy
        while m <= 0:
            m += 12
            y -= 1
        return y, m
    return (int(label), year_end_month) if label.isdigit() else (0, 0)


def _result_lines(prof: dict) -> tuple[list[str], str]:
    """Latest year and latest quarter vs the same period a year earlier, all from the PSX company page
    (one source, so the numbers agree with the financials card)."""
    lines, label = [], ""
    yem = next((v for k, v in _MONTHS.items() if k in (prof.get("year_end") or "").lower()), 12)
    ann = prof.get("annual") or {}
    ys = sorted(ann)
    if len(ys) >= 2:
        a, b = ann[ys[-1]], ann[ys[-2]]
        pa, pb = a.get("Profit after Taxation"), b.get("Profit after Taxation")
        if pa is not None and pb is not None:
            lines.append(f"Year {ys[-1]}: profit after tax {_bn(pa)} vs {_bn(pb)} in {ys[-2]}{_chg(pa, pb)}")
        top = "Sales" if "Sales" in a else ("Total Income" if "Total Income" in a else None)
        if top and a.get(top) is not None and b.get(top) is not None:
            lines.append(f"Year {ys[-1]}: {top.lower()} {_bn(a[top])} vs {_bn(b[top])}{_chg(a[top], b[top])}")
        label = f"Year {ys[-1]}"
    q = prof.get("quarterly") or {}
    if q:
        latest = list(q)[0]  # PSX lists the newest quarter first
        qn, yr = latest.split() if len(latest.split()) == 2 else (latest, "")
        prev = f"{qn} {int(yr) - 1}" if yr.isdigit() else ""
        if prev in q:
            pa, pb = q[latest].get("Profit after Taxation"), q[prev].get("Profit after Taxation")
            if pa is not None and pb is not None:
                newer = not ys or _period_end(latest, yem) > _period_end(ys[-1], yem)
                line = f"{latest}: profit after tax {_bn(pa)} vs {_bn(pb)} in {prev}{_chg(pa, pb)}"
                if newer:  # the quarter is more recent than the last full year
                    lines.insert(0, line)
                    label = latest
                else:
                    lines.append(line)
    return lines, label


# ---------------------------------------------------------------- text
def caption(d: dict) -> str:
    what = ""
    rp = d.get("report") or {}
    if (rp.get("what_they_do") or {}).get("text"):
        what = rp["what_they_do"]["text"].split(". ")[0].rstrip(".") + "."
    elif d["profile"].get("description"):
        what = d["profile"]["description"].split(". ")[0][:220].rstrip(".") + "."
    lines = [f"<b>Stock of the Day: {esc(d['sym'])}</b> · {esc(d['name'])}",
             f"{esc(d['sector'])} · Rs {d['price']:,.2f}" + (f" · P/E {d['pe']:.1f}" if d.get("pe") else ""),
             "", esc(what), "",
             # no links (owner's choice): everything is in the note itself
             "Full research note in the pages above (PDF below): key data, the business, latest results, "
             "four years of financials, outlook and risks.",
             "Research from official filings, not investment advice."]
    return "\n".join(lines)


# ---------------------------------------------------------------- daily run
def run(cfg: dict, dry: bool = False, force: str | None = None, preview: bool = False) -> None:
    """Pick today's company, build the report, post it (channel; admin only with preview), record it."""
    from .note import build
    from .telegram import Sender
    now = now_pkt()
    st = _load()
    today = f"{now:%Y-%m-%d}"
    if not force and any(c["date"] == today for c in st["covered"]):
        log.info("stock of the day already published for %s", today)
        return
    tries = st["attempts"].get(today, 0)
    st["attempts"][today] = tries + 1
    s = Sender(dry)
    raw = prices.update_live(now)
    if raw.empty:
        s.send_admin("<b>Stock of the Day</b>\nNo price data today; it will retry at the next run.")
        _save(st)
        return
    meta = prices.meta(sorted(raw["sym"].unique()))
    df = features(prices.adjust(raw), meta)
    syms = sorted(df["sym"].unique())
    res = earnings.classify(earnings.load_all(syms))
    v = research.valuation(df, res)
    _, sectors = prices.universe()
    kse = prices.index_history(now.replace(tzinfo=None) - timedelta(days=400))
    end = df["date"].max()
    last = df[df["date"] == end].set_index("sym")
    mcap = last["mcap"].dropna().to_dict()
    sec = {k: (x or "").strip().title() for k, x in sectors.items()}
    cands = [force] if force else pick(st, set(syms), mcap, sec, now)
    final_try = tries >= 1 or force  # the 20:00 run publishes even if the annual report could not be read
    for sym in cands[:3]:
        try:
            d = collect(sym, cfg, df, v, res, kse, sectors, kmi=set(psx.index_members("KMI30", set(syms))))
        except Exception as e:  # noqa: BLE001
            log.warning("stock of the day: %s skipped (%s)", sym, e)
            continue
        if not d.get("report") and not final_try:
            s.send_admin(f"<b>Stock of the Day</b>\n{esc(sym)}: the annual report could not be read yet "
                         "(Gemini or PSX). Trying again at 20:00; then it is published with the official numbers only.")
            _save(st)
            return
        d["published"] = today
        pages, pdf = build(d)  # brokerage-style research note: page images + the same note as a PDF
        dest = (DIR / "preview") if preview else PUBLISHED  # a preview never reaches the public website
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{sym}.json").write_text(json.dumps(d, ensure_ascii=False, default=str), encoding="utf-8")
        for old in dest.glob(f"{sym}-*.png"):  # an earlier version may have had more pages
            old.unlink()
        for i, png in enumerate(pages, 1):
            (dest / f"{sym}-{i}.png").write_bytes(png)
        (dest / f"{sym}.pdf").write_bytes(pdf)
        pdf_name = f"{sym} research note {today}.pdf"
        if preview:  # print the annual report summary in the run log so it can be checked before going public
            print("ANNUAL REPORT SUMMARY", json.dumps(d.get("report"), ensure_ascii=False, indent=1))
        if preview:
            ok = s.send_admin_photo(pages[0], caption(d), name=f"sotd_{sym}")
            s.send_document(pdf, pdf_name, f"{esc(d['name'])} ({esc(sym)}): research note preview", admin_only=True)
        else:
            ok = s.send_album(pages, caption(d), name=f"sotd_{sym}")
            if ok:
                s.send_document(pdf, pdf_name, f"<b>{esc(d['name'])} ({esc(sym)})</b>: research note (PDF)")
        if ok and not preview:
            st["covered"].append({"sym": sym, "date": today, "round": st["round"], "name": d["name"]})
        if not d.get("report"):
            s.send_admin(f"<b>Stock of the Day</b>\n{esc(sym)} published without the annual report summary "
                         "(it could not be read). The official numbers were used.")
        _save(st)
        log.info("stock of the day: %s %s", sym, "published" if ok else "send failed")
        return
    s.send_admin("<b>Stock of the Day</b>\nNo report could be built today (checked " + ", ".join(cands[:3]) +
                 "). It retries at the next run.")
    _save(st)


def published() -> list[dict]:
    """Reports published so far (for the website archive)."""
    out = []
    for p in sorted(PUBLISHED.glob("*.json")) if PUBLISHED.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out

