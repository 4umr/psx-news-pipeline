"""Results history (scstrade, back to ~2010) and earnings surprise measures.

PSX results are announced with the period label (1Q26, HY26, 9M26, FY26 / CY25...),
the EPS of the latest quarter and the cumulative EPS for the year to date. The same
label one year earlier gives a like-for-like comparison (quarter vs same quarter).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .common import ROOT, log
from .sources import scs

HIST_PATH = ROOT / "data" / "results.csv.gz"

# Results-based rules. The signal day is the results-day session (the reaction day); entry is
# the next open. pre_results_risk fires 5 sessions before the results day (entry 4 sessions
# before, exit the close before results). PSX requires board meetings to be announced at
# least 7 days ahead, so the date is public by then.
EVENT_SIGNALS: dict[str, dict] = {
    "results_momentum": {"name": "Strong results, market agrees", "kind": "long", "h": 60, "live": True,
                         "tiers": ["Mid", "Small"],
                         "rule": "Quarterly EPS up 50%+ on last year (or back to profit) and the stock up 3%+ on results day; mid and small caps"},
    "results_reaction": {"name": "Big positive results reaction", "kind": "long", "h": 60, "live": True,
                         "rule": "Stock up 5%+ on results day"},
    "results_drop":     {"name": "Big negative results reaction", "kind": "risk", "h": 20,
                         "rule": "Stock down 5%+ on results day"},
    "pre_results_risk": {"name": "Results due after a bad quarter", "kind": "risk", "h": 4, "offset": 5, "live": True,
                         "rule": "Results due within 5 sessions and the previous result was a big EPS drop or a loss"},
}
GOOD = ("big_up", "turnaround")
BAD = ("big_down", "to_loss")
_LABEL = re.compile(r"^\s*([A-Za-z0-9]*?[A-Za-z])(\d{2})\s*$")


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("(", "-").replace(")", "").strip())
    except (TypeError, ValueError):
        return None


def _pct(s) -> float | None:
    # Free text such as "25% (F)", "82.50%" or typos like "82.50.50%": take the first clean number
    m = re.search(r"(\d+(?:\.\d+)?)(?:\.\d+)*\s*%", str(s or ""))
    return float(m.group(1)) if m else None


def rows_for(symbol: str) -> list[dict]:
    out = []
    for r in scs.results_history(symbol):
        lab = (r.get("bm_quarter_number") or "").strip().upper()
        m = _LABEL.match(lab)
        t = (r.get("bm_time") or "").strip().upper()
        out.append({"sym": symbol, "date": r["date"].strftime("%Y-%m-%d"), "time": t, "label": lab,
                    "kind": m.group(1) if m else "", "yy": int(m.group(2)) if m else None,
                    "eps_q": _num(r.get("bm_eps_quarter")), "eps_cum": _num(r.get("bm_eps_cum")),
                    "div": _pct(r.get("bm_dividend")), "bonus": _pct(r.get("bm_bonus"))})
    return out


def fetch_all(symbols: list[str], workers: int = 8) -> pd.DataFrame:
    rows: list[dict] = []
    with ThreadPoolExecutor(workers) as ex:
        for part in ex.map(rows_for, symbols):
            rows.extend(part)
    df = pd.DataFrame(rows)
    HIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(HIST_PATH, index=False, compression="gzip")
    log.info("results history: %d announcements for %d companies", len(df), df["sym"].nunique() if len(df) else 0)
    return df


def load_all(symbols: list[str], refresh: bool = False) -> pd.DataFrame:
    """Cached for a day; new results then need a fresh download."""
    import time
    if HIST_PATH.exists() and not refresh and time.time() - HIST_PATH.stat().st_mtime < 86400:
        return pd.read_csv(HIST_PATH)
    return fetch_all(symbols)


def after_hours(t: str) -> bool:
    """Announced after the market closed (reaction comes the next session)."""
    m = re.match(r"(\d{1,2})[:.](\d{2})\s*([AP])", t.replace(" ", ""))
    if not m:
        return False
    h = int(m.group(1)) % 12 + (12 if m.group(3) == "P" else 0)
    return h * 60 + int(m.group(2)) >= 15 * 60 + 30


KIND_ALIAS = {"1HY": "HY", "1H": "HY", "6M": "HY", "9M": "3Q", "3M": "1Q", "12M": "FY"}


def norm_label(lab: str) -> tuple[str, str, int | None]:
    """'CY-24' -> ('CY24', 'CY', 24); '9M-26' -> ('3Q26', '3Q', 26); '1HY26' -> ('HY26', 'HY', 26)."""
    lab = re.sub(r"[^A-Z0-9]", "", str(lab or "").upper())
    m = _LABEL.match(lab)
    if not m:
        return lab, "", None
    kind = KIND_ALIAS.get(m.group(1), m.group(1))
    return f"{kind}{m.group(2)}", kind, int(m.group(2))


def surprises(res: pd.DataFrame) -> pd.DataFrame:
    """Add same-period-last-year EPS, YoY growth and a simple class for each announcement."""
    res = res.copy()
    parts = [norm_label(x) for x in res["label"].fillna("")]
    res["label"] = [p[0] for p in parts]
    res["kind"] = [p[1] for p in parts]
    res["yy"] = [p[2] for p in parts]
    res["date"] = pd.to_datetime(res["date"])
    res = res.sort_values(["sym", "date"]).drop_duplicates(["sym", "label"], keep="first")
    prev = res[["sym", "kind", "yy", "eps_q", "eps_cum", "div"]].copy()
    prev["yy"] = prev["yy"] + 1
    res = res.merge(prev, on=["sym", "kind", "yy"], how="left", suffixes=("", "_ly"))

    def growth(a, b):
        if pd.isna(a) or pd.isna(b):
            return None
        if b > 0:
            return a / b - 1
        return None

    res["yoy_q"] = [growth(a, b) for a, b in zip(res["eps_q"], res["eps_q_ly"])]
    res["yoy_cum"] = [growth(a, b) for a, b in zip(res["eps_cum"], res["eps_cum_ly"])]
    res["turnaround"] = (res["eps_q"] > 0) & (res["eps_q_ly"] < 0)
    res["to_loss"] = (res["eps_q"] < 0) & (res["eps_q_ly"] > 0)
    res["after_hours"] = res["time"].fillna("").map(after_hours)
    return res


def label_class(r) -> str:
    y = r.get("yoy_q")
    if r.get("turnaround"):
        return "turnaround"
    if r.get("to_loss"):
        return "to_loss"
    if y is None or pd.isna(y):
        return "n/a"
    if y >= 0.5:
        return "big_up"
    if y >= 0.15:
        return "up"
    if y <= -0.5:
        return "big_down"
    if y <= -0.15:
        return "down"
    return "flat"


def classify(res: pd.DataFrame) -> pd.DataFrame:
    res = surprises(res)
    res["cls"] = res.apply(label_class, axis=1)
    res["prev_cls"] = res.groupby("sym")["cls"].shift(1)
    res["prev_yoy_q"] = res.groupby("sym")["yoy_q"].shift(1)
    # Trailing 12-month EPS: year-to-date EPS + last full year - the same year-to-date a year earlier
    annual = res[res["kind"].isin(["FY", "CY"])][["sym", "yy", "eps_cum"]].rename(columns={"eps_cum": "annual_prev"})
    annual = annual.drop_duplicates(["sym", "yy"], keep="last")
    annual["yy"] = annual["yy"] + 1
    res = res.merge(annual, on=["sym", "yy"], how="left")
    is_annual = res["kind"].isin(["FY", "CY"])
    res["ttm"] = res["eps_cum"].where(is_annual, res["eps_cum"] + res["annual_prev"] - res["eps_cum_ly"])
    return res.sort_values(["sym", "date"]).reset_index(drop=True)


def event_rows(res: pd.DataFrame, df: pd.DataFrame, min_adtv: float, rules: dict | None = None,
               react_min: dict | None = None) -> pd.DataFrame:
    """Map announcements to price rows and apply EVENT_SIGNALS (or the given rules: name -> mask on res).
    Returns rows like signals.events()."""
    res = res[res["date"] >= df["date"].min()].copy()
    rowid = pd.Series(range(len(df)), index=df.index)
    pos = {s: (sub["date"].values, rowid[sub.index].values) for s, sub in df.groupby("sym", sort=False)}
    R = []
    for sym, d, ah in zip(res["sym"], res["date"], res["after_hours"]):
        if sym not in pos:
            R.append(-1)
            continue
        dates, ids = pos[sym]
        k = dates.searchsorted(pd.Timestamp(d).to_datetime64(), side="right" if ah else "left")
        R.append(int(ids[k]) if k < len(ids) else -1)
    res["R"] = R
    res = res[res["R"] >= 0]
    res["react"] = df["chg"].values[res["R"].values]
    res["tier"] = df["tier"].values[res["R"].values]
    res["liquid"] = df["adtv20"].values[res["R"].values] >= min_adtv
    mid_small = res["tier"].isin(EVENT_SIGNALS["results_momentum"]["tiers"])
    if rules is None:
        rules = {
            "results_momentum": res["cls"].isin(GOOD) & (res["react"] >= 0.03) & mid_small,
            "results_reaction": res["react"] >= 0.05,
            "results_drop": res["react"] <= -0.05,
            "pre_results_risk": res["prev_cls"].isin(BAD),
        }
    else:
        rules = {k: v.reindex(res.index).fillna(False).astype(bool) for k, v in rules.items()}
    out = []
    for name, m in rules.items():
        m = m & res["liquid"]
        if react_min and name in react_min:
            m = m & (res["react"] >= react_min[name])
        rows, syms = res.loc[m, "R"].values, res.loc[m, "sym"].values
        if off := EVENT_SIGNALS.get(name, {}).get("offset"):  # signal earlier, same stock only
            rows = rows - off
            ok = rows >= 0
            rows, syms = rows[ok], syms[ok]
            rows = rows[df["sym"].values[rows] == syms]
        e = df.iloc[rows][["date", "sym", "si", "c", "chg", "vx", "adtv20", "tier", "ff_pct", "r10"]].copy()
        e["signal"] = name
        out.append(e)
    return pd.concat(out) if out else pd.DataFrame()


# ---------------------------------------------------------------- live use (one company at a time)
_COMPANY: dict[str, pd.DataFrame] = {}


def company(sym: str) -> pd.DataFrame | None:
    """Classified results history of one company (cached for this run)."""
    if sym not in _COMPANY:
        rows = rows_for(sym)
        _COMPANY[sym] = classify(pd.DataFrame(rows)) if rows else None
    return _COMPANY[sym]


def assess(sym: str, label: str = "") -> dict | None:
    """The announcement with this label (or the latest one) compared with the same period last year."""
    h = company(sym)
    if h is None or h.empty:
        return None
    label = norm_label(label)[0] if label else ""
    row = h[h["label"] == label] if label else h
    if row.empty:
        return None
    r = row.sort_values("date").iloc[-1]
    annual = r["kind"] in ("FY", "CY")
    now_, ly = (r["eps_cum"], r["eps_cum_ly"]) if annual else (r["eps_q"], r["eps_q_ly"])
    yoy = r["yoy_cum"] if annual else r["yoy_q"]
    both = pd.notna(now_) and pd.notna(ly)
    return {"label": r["label"], "date": r["date"], "annual": annual, "eps": now_, "eps_ly": ly,
            "yoy": None if pd.isna(yoy) else float(yoy), "cls": r["cls"], "div": r["div"],
            "turnaround": bool(both and now_ > 0 > ly), "to_loss": bool(both and now_ < 0 < ly)}


def eps_line(a: dict) -> str:
    """'quarterly EPS 1.89 vs 0.85 a year ago (+122%)' in plain words."""
    what = "full-year EPS" if a["annual"] else "quarterly EPS"
    if pd.isna(a["eps"]):
        return "EPS not given"
    if pd.isna(a["eps_ly"]):
        return f"{what} {a['eps']:.2f}"
    s = f"{what} {a['eps']:.2f} vs {a['eps_ly']:.2f} a year ago"
    if a["yoy"] is not None:
        s += f" ({a['yoy'] * 100:+.0f}%)"
    elif a["eps"] > 0 > a["eps_ly"]:
        s += " (back to profit)"
    elif a["eps"] < 0 < a["eps_ly"]:
        s += " (turned to a loss)"
    return s
