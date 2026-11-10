"""Official data watchers: one infographic each time a publisher releases a new number.

  fuel     petrol / diesel prices (PSO price list)            checked every 20 minutes
  remit    workers' remittances (SBP)                         hourly
  bop      current account and trade (SBP balance of payments) hourly
  autos    vehicle sales by company (PAMA)                    every 2 hours

The first time a source is seen its numbers are recorded quietly, unless the file was published in
the last 3 days (then it is still news). After that, every new release is posted once. A source that
keeps failing produces one private note; numbers that fail a sanity check are never posted.
"""
from __future__ import annotations

import time
from datetime import datetime

from .common import Alert, esc, link, log, now_utc
from .sources import official as src
from .style import header

CHECK_MINUTES = {"fuel": 20, "remit": 60, "bop": 60, "autos": 120}
FRESH_DAYS = 3
FAILS_BEFORE_NOTE = 8
NAMES = {"fuel": "Fuel prices (PSO)", "remit": "Remittances (SBP)", "bop": "Balance of payments (SBP)",
         "autos": "Vehicle sales (PAMA)"}
FUEL_LABEL = {"petrol": "Petrol", "diesel": "High-speed diesel", "kerosene": "Kerosene", "ldo": "Light diesel oil"}
FUEL_SHORT = {"kerosene": "Kerosene", "ldo": "Light diesel"}


def _usd(m: float) -> str:
    return f"{'-' if m < 0 else ''}${abs(m) / 1000:,.2f}bn" if abs(m) >= 1000 else f"{'-' if m < 0 else ''}${abs(m):,.0f}m"


def _pct(now: float | None, base: float | None) -> float | None:
    if now is None or base is None or base <= 0:
        return None
    return (now / base - 1) * 100


def _p(x: float | None, digits: int = 0) -> str:
    return "" if x is None else f"{x:+.{digits}f}%"


def _fresh(d: dict) -> bool:
    m = d.get("modified")
    return bool(m) and (now_utc() - m).total_seconds() < FRESH_DAYS * 86400


def _note(what: str, detail: str) -> Alert:
    return Alert(f"<b>Data check, not posted to channel</b>\n{esc(what)}: {esc(detail)}", 0, f"od:{what}", admin=True)


# ---------------------------------------------------------------- fuel
def _fuel(now: datetime, st: dict) -> list[Alert] | None:
    d = src.fuel_prices()
    if d is None:
        return None
    old = st.get("data")
    if not old:
        st["data"] = d
        return []
    if d["effective"] == old["effective"] and d["prices"] == old["prices"]:
        return []
    if d["effective"] < old["effective"]:
        return []  # the page briefly showed an older list
    chg = {k: d["prices"][k] - old["prices"][k] for k in d["prices"] if k in old["prices"]}
    if any(abs(c) > 0.3 * old["prices"][k] for k, c in chg.items()):
        if st.get("sus") == d["effective"]:
            return []
        st["sus"] = d["effective"]
        return [_note(NAMES["fuel"], f"price change above 30% ({old['prices']} -> {d['prices']})")]
    st["data"] = d
    eff = datetime.strptime(d["effective"], "%Y-%m-%d")
    day = f"{eff.day} {eff:%b}"
    was = datetime.strptime(old["effective"], "%Y-%m-%d")

    def move(k: str) -> str:
        c = chg.get(k)
        return "unchanged" if not c else f"{'up' if c > 0 else 'down'} Rs {abs(c):,.2f}"

    p, hsd = d["prices"]["petrol"], d["prices"]["diesel"]
    if not any(chg.values()):
        title = f"Petrol and diesel prices unchanged from {day}: petrol Rs {p:,.2f}, diesel Rs {hsd:,.2f}"
    else:
        title = f"Petrol Rs {p:,.2f} ({move('petrol')}), diesel Rs {hsd:,.2f} ({move('diesel')}) from {day}"
    why = ("Fuel prices feed into transport costs and inflation. A price rise usually brings inventory gains for "
           "oil marketing companies and refineries in the short run; a cut does the opposite.")
    rows = [(FUEL_SHORT[k], f"Rs {d['prices'][k]:,.2f} per litre (was Rs {old['prices'][k]:,.2f})",
             f"{chg[k]:+,.2f}" if chg.get(k) else "same", None)
            for k in ("kerosene", "ldo") if k in d["prices"] and k in old["prices"]]
    card = {"theme": "commodity", "kicker": f"Fuel prices · effective {day} {eff:%Y}", "title": title,
            "stats": [("Petrol, per litre", f"Rs {p:,.2f}", None, move("petrol")),
                      ("Diesel, per litre", f"Rs {hsd:,.2f}", None, move("diesel")),
                      ("Previous list", f"{was.day} {was:%b}", None,
                       f"petrol Rs {old['prices']['petrol']:,.2f}")],
            "rows_title": "Other products", "rows": rows, "why": why,
            "sectors": ["Oil marketing", "Refineries", "Transport", "Autos"],
            "source": "Pakistan State Oil price list (before freight)"}
    text = (header("⛽", title, f"Fuel prices · effective {day} {eff:%Y}") + "\n"
            + "\n".join(f"{FUEL_LABEL[k]}: <b>Rs {v:,.2f}</b> per litre ({move(k)})" for k, v in d["prices"].items())
            + f"\n\n💡 {why}\n🔗 {link(src.FUEL_URL, 'Pakistan State Oil')}")
    return [Alert(text, 9, f"fuel:{d['effective']}", tags=["FuelPrices"], extra={"card": card})]


# ---------------------------------------------------------------- remittances
def _remit(now: datetime, st: dict) -> list[Alert] | None:
    d = src.remittances()
    if d is None:
        return None
    key = f"{d['year']}-{d['month']:02d}"
    old = st.get("key")
    if old == key:
        return []
    if old and key < old:
        return []
    mom = _pct(d["value"], d["prev"])
    if mom is not None and abs(mom) > 45:
        if st.get("sus") == key:
            return []
        st["sus"] = key
        return [_note(NAMES["remit"], f"{d['label']} is {mom:+.0f}% on the previous month ({d['value']:,.0f}m)")]
    st["key"] = key
    if not old and not _fresh(d):
        return []
    yoy, ytd = _pct(d["value"], d["ly"]), _pct(d["fytd"], d["fytd_ly"])
    title = f"Remittances {_usd(d['value'])} in {d['label']}" + (
        f", {'up' if yoy >= 0 else 'down'} {abs(yoy):.0f}% on last year" if yoy is not None else "")
    why = ("Remittances are Pakistan's largest source of dollars after exports. Strong inflows support the "
           "rupee, SBP reserves and the current account.")
    stats = [(d["label"], _usd(d["value"]), None, f"{_usd(d['ly'])} a year ago" if d["ly"] else ""),
             ("On last year", _p(yoy, 1) or "n/a", yoy, f"{_p(mom, 1)} on last month" if mom is not None else ""),
             (f"{d['fy']} so far ({d['n_months']} months)", _usd(d["fytd"]), None,
              f"{_p(ytd, 1)} on last year" if ytd is not None else "")]
    rows = []
    for c in d["countries"][:5]:
        g = _pct(c["value"], c["ly"])
        rows.append((c["name"][:14], f"{_usd(c['value'])}" + (f" vs {_usd(c['ly'])} a year ago" if c["ly"] else ""),
                     _p(g), g))
    card = {"theme": "economy", "kicker": f"Workers' remittances · {d['label']}", "title": title, "stats": stats,
            "rows_title": "Where it came from", "rows": rows, "why": why, "source": "State Bank of Pakistan"}
    text = (header("💵", title, f"Workers' remittances · {d['label']}") + "\n"
            + f"{d['label']}: <b>{_usd(d['value'])}</b>" + (f" ({_p(yoy, 1)} on last year)" if yoy is not None else "")
            + f"\n{d['fy']} so far: <b>{_usd(d['fytd'])}</b>" + (f" ({_p(ytd, 1)})" if ytd is not None else "")
            + f"\n\n💡 {why}\n🔗 {link(src.SBP_DATA_PAGE, 'State Bank of Pakistan')}")
    return [Alert(text, 9, f"remit:{key}", tags=["Remittances"], extra={"card": card})]


# ---------------------------------------------------------------- balance of payments
def _bop(now: datetime, st: dict) -> list[Alert] | None:
    d = src.bop()
    if d is None:
        return None
    key = f"{d['year']}-{d['month']:02d}"
    old = st.get("key")
    if old == key or (old and key < old):
        return []
    st["key"] = key
    if not old and not _fresh(d):
        return []
    r = d["rows"]
    ca = r["ca"]
    word = "surplus" if ca["now"] >= 0 else "deficit"
    title = f"Current account {word} {_usd(abs(ca['now']))} in {d['label']}"
    if ca["ly"] is not None:
        title += f" ({'surplus' if ca['ly'] >= 0 else 'deficit'} {_usd(abs(ca['ly']))} a year ago)"
    why = ("The current account is the country's dollar income minus its dollar spending. A deficit draws on "
           "reserves and can pressure the rupee; a surplus does the opposite.")

    def signed(v):
        return "n/a" if v is None else (f"+{_usd(v)}" if v >= 0 else _usd(v))

    def worded(v):
        return f"{'surplus' if v >= 0 else 'deficit'} {_usd(abs(v))}"

    stats = [(d["label"], signed(ca["now"]), ca["now"], f"{worded(ca['ly'])} a year ago" if ca["ly"] is not None else "")]
    if ca["fytd"] is not None:
        stats.append(("Fiscal year so far", signed(ca["fytd"]), ca["fytd"],
                      f"{worded(ca['fytd_ly'])} last year" if ca["fytd_ly"] is not None else ""))
    if tr := r.get("trade"):
        stats.append(("Trade gap (goods)", _usd(abs(tr["now"])), None,
                      f"{_usd(abs(tr['ly']))} a year ago" if tr["ly"] is not None else ""))
    rows = []
    for k, name, good_up in (("exports", "Exports", True), ("imports", "Imports", None),
                             ("remit", "Remittances", True), ("fdi", "FDI", True)):
        x = r.get(k)
        if not x:
            continue
        g = _pct(x["now"], x["ly"])
        rows.append((name, f"{_usd(x['now'])}" + (f" vs {_usd(x['ly'])} a year ago" if x["ly"] is not None else ""),
                     _p(g), g if good_up else None))
    card = {"theme": "economy", "kicker": f"Balance of payments · {d['label']}", "title": title, "stats": stats,
            "rows_title": "The main flows", "rows": rows, "why": why, "source": "State Bank of Pakistan"}
    text = (header("🌍", title, f"Balance of payments · {d['label']}") + "\n"
            + "\n".join(f"{a}: <b>{b}</b> {c}" for a, b, c, _ in rows)
            + f"\n\n💡 {why}\n🔗 {link(src.SBP_DATA_PAGE, 'State Bank of Pakistan')}")
    return [Alert(text, 9, f"bop:{key}", tags=["CurrentAccount"], extra={"card": card})]


# ---------------------------------------------------------------- vehicle sales
def _autos(now: datetime, st: dict) -> list[Alert] | None:
    url = src.auto_link()
    if url is None:
        return None
    old = st.get("key")
    if url == old:
        return []
    d = src.auto_sales(url)
    if d is None:
        return None
    st["key"] = url
    if not old and not _fresh(d):
        return []
    t = d["totals"]
    cars = t["cars"]
    yoy, mom = _pct(cars["now"], cars["ly"]), _pct(cars["now"], cars["prev"])
    title = f"Car sales {cars['now']:,.0f} units in {d['label']}" + (
        f", {'up' if yoy >= 0 else 'down'} {abs(yoy):.0f}% on last year" if yoy is not None else "")
    stats = [("Cars", f"{cars['now']:,.0f}", yoy, f"{_p(yoy)} on last year" if yoy is not None else "")]
    for k, lab in (("suv", "SUVs, pickups, vans"), ("tractors", "Tractors")):
        if k in t:
            g = _pct(t[k]["now"], t[k]["ly"])
            stats.append((lab, f"{t[k]['now']:,.0f}", g, f"{_p(g)} on last year" if g is not None else ""))
    rows = []
    for g in d["groups"]:
        ch = _pct(g["now"], g["ly"])
        rows.append((g["sym"] or g["name"].split()[0 if g["name"].startswith("Hyundai") else -1], f"{g['name']}: {g['now']:,.0f} vs {g['ly']:,.0f} a year ago", _p(ch), ch))
    for k, lab, left in (("bikes", "All makers", "Bikes"), ("trucks_buses", "Trucks and buses", "Trucks")):
        if k in t:
            ch = _pct(t[k]["now"], t[k]["ly"])
            rows.append((left, f"{lab}: {t[k]['now']:,.0f} vs {t[k]['ly']:,.0f} a year ago", _p(ch), ch))
    ytd = _pct(cars["fytd"], cars["fytd_ly"])
    fy = f"FY{(d['year'] + (1 if d['month'] >= 7 else 0)) % 100:02d}"
    why = ((f"Car sales {'rose' if mom >= 0 else 'fell'} {abs(mom):.0f}% from the month before. " if mom is not None else "")
           + f"{fy} so far ({d['months']} months): {cars['fytd']:,.0f} cars vs {cars['fytd_ly']:,.0f} last year"
           + (f" ({_p(ytd)})" if ytd is not None else "") + ". PAMA members only: Kia, Changan and MG are not included.")
    card = {"theme": "psx", "kicker": f"Vehicle sales · {d['label']}", "title": title, "stats": stats[:3],
            "rows_title": "By company · units sold · on last year", "rows": rows[:11], "why": why,
            "sectors": ["Autos", "Auto parts", "Tractors"], "source": "Pakistan Automotive Manufacturers Association"}
    text = (header("🚗", title, f"Vehicle sales · {d['label']}") + "\n"
            + "\n".join(f"{m}: <b>{right}</b>" for _, m, right, _ in rows)
            + f"\n\n💡 {why}\n🔗 {link(url, 'PAMA')}")
    return [Alert(text, 8, f"autos:{url}", tags=["AutoSales"], extra={"card": card})]


WATCHERS = (("fuel", _fuel), ("remit", _remit), ("bop", _bop), ("autos", _autos))


def official_data(now: datetime, state, cfg: dict) -> list[Alert]:
    conf = cfg.get("official_data") or {}
    if not conf.get("enabled", True):
        return []
    od = state.data.setdefault("official", {})
    alerts: list[Alert] = []
    for name, fn in WATCHERS:
        if not conf.get(name, True):
            continue
        st = od.setdefault(name, {})
        if time.time() - st.get("checked", 0) < CHECK_MINUTES[name] * 60:
            continue
        st["checked"] = int(time.time())
        try:
            got = fn(now, st)
        except Exception:  # noqa: BLE001  one source must never stop the others
            log.exception("official data: %s crashed", name)
            got = None
        if got is None:
            st["fails"] = st.get("fails", 0) + 1
            if st["fails"] == FAILS_BEFORE_NOTE:
                alerts.append(Alert(f"<b>Source problem:</b> {esc(NAMES[name])} could not be read {FAILS_BEFORE_NOTE} "
                                    "times in a row. Its card is paused; everything else continues. The page may "
                                    "have changed its layout.", 0, f"od:fail:{name}", admin=True))
            continue
        if st.get("fails", 0) >= FAILS_BEFORE_NOTE:
            alerts.append(Alert(f"<b>Recovered:</b> {esc(NAMES[name])} can be read again.", 0, f"od:ok:{name}", admin=True))
        st["fails"] = 0
        if got:
            log.info("official data: %s -> %d alert(s)", name, len(got))
        alerts += got
    return alerts
