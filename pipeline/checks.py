"""Data checks before any number reaches a card. A stock that fails is skipped with the reason; nothing is
shown with numbers that disagree with another source.

  price       the snapshot price matches the exchange's closing price today (within 1%)
  quarters    the 4 quarters used for trailing EPS are consecutive (no gap, no duplicate)
  book        book value per share is positive; equity / assets between 0 and 100%; assets >= book value
  pb          our price / book matches SCS's own P/B (within 3%)
  results     the latest quarter EPS on the snapshot equals the company's latest results announcement
              (scstrade announcements, a separate dataset) within 2%  [only for stocks we show]
  bonus       no bonus issue or split in the last 12 months: per-share history would mix share counts,
              so trailing EPS and fair value are not computed then  [only for stocks we show]
"""
from __future__ import annotations

from datetime import datetime, timedelta


def _qkey(k: str) -> tuple[int, int]:
    return int(k[:4]), int(k[-1])


def last4(eps_q: dict[str, float]) -> tuple[list[str], bool]:
    """The last 4 quarter keys ('2026 Q3') and whether they are consecutive."""
    keys = sorted(eps_q, key=_qkey)[-4:]
    if len(keys) < 4:
        return keys, False
    seq = [_qkey(k) for k in keys]
    ok = all((b[0] * 4 + b[1]) - (a[0] * 4 + a[1]) == 1 for a, b in zip(seq, seq[1:]))
    return keys, ok


def market_open(now=None) -> bool:
    from .common import now_pkt
    now = now or now_pkt()
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (16, 45)


def basic(snap: dict, close: float | None) -> list[str]:
    """Checks that need no extra download. Returns the problems (empty = fine)."""
    v = snap["values"]
    out = []
    p = v.get("price")
    if not p or p <= 0:
        return ["no price"]
    # after the close both sources show the same final price (1% allowed). During the session the exchange figure
    # passed in is usually YESTERDAY's close (scstrade's all-stock table updates once, about 16:15), so the live
    # snapshot may differ by up to the day's price limit (10%, or Rs 1 for cheap stocks): only more than that is an
    # error. (13 Nov 2026: 40+ stocks that had simply moved 5-10% that day were wrongly rejected.)
    if market_open():
        bad = close and abs(p - close) > max(0.105 * close, 1.05)
    else:
        bad = close and abs(p / close - 1) > 0.01
    if bad:
        out.append(f"snapshot price {p:,.2f} differs from the exchange's {close:,.2f}")
    keys, ok = last4(snap.get("eps_quarters") or {})
    if len(keys) == 4 and not ok:
        out.append(f"the last 4 quarters are not consecutive ({', '.join(keys)})")
    bv, ear = v.get("bv"), v.get("ear")
    if bv is not None and bv <= 0:
        out.append(f"book value {bv:,.2f} is not positive")
    if ear is not None and not 0 < ear <= 100:
        out.append(f"equity to assets {ear}% is impossible")
    if bv and bv > 0 and v.get("pbv"):
        calc = p / bv
        # SCS rounds P/B to 2 decimals and, during the session, may use an earlier price
        if abs(calc - v["pbv"]) > 0.015 and abs(calc / v["pbv"] - 1) > (0.06 if market_open() else 0.03):
            out.append(f"price to book {calc:.2f} differs from SCS's {v['pbv']:.2f}")
    return out


QNUM = {"1Q": 1, "HY": 2, "3Q": 3, "FY": 4, "CY": 4}


def ann_quarters(h) -> dict[str, float]:
    """Quarter EPS from the results announcements, keyed like the snapshot ('2027 Q1' = 1Q27)."""
    out = {}
    for _, r in h.sort_values("date").iterrows():
        q, yy, e = QNUM.get(r.get("kind")), r.get("yy"), r.get("eps_q")
        if q and yy == yy and yy is not None and e == e and e is not None:
            out[f"{2000 + int(yy)} Q{q}"] = float(e)
    return out


def pick_quarter(snap_v: float | None, ann_v: float | None) -> float | None:
    """One quarter's EPS from the two scstrade datasets. Close to each other (within 15%): the announced figure,
    which is what the company published (restated where it restated). Far apart: the snapshot, which SCS
    restates for splits and bonus issues while old announcements keep the old share count (LUCK HY25: 73.17
    announced before its 5-for-1 split, 14.63 on the snapshot)."""
    if snap_v is None:
        return ann_v
    if ann_v is None:
        return snap_v
    return ann_v if abs(ann_v - snap_v) <= max(0.02, 0.15 * abs(snap_v)) else snap_v


def oneoff(quarters: list[tuple[str, float]]) -> dict | None:
    """An unusual quarter inside the trailing four: positive, at least 2x the median of the other three (which
    must be profitable) and not the latest one (a jump in the LATEST quarter may be the new normal: flagged only).
    OGDC FY26 Q4 (Rs 29.55 against 8-14 in the other quarters) made its trailing EPS 61 instead of about 41.
    Returns {key, eps, typical, latest} or None."""
    from statistics import median
    if len(quarters) != 4:
        return None
    best = None
    for i, (k, e) in enumerate(quarters):
        rest = [x for j, (_, x) in enumerate(quarters) if j != i]
        med = median(rest)
        if med > 0 and e > 2 * med and e - med >= 0.5 and (best is None or e / med > best["eps"] / best["typical"]):
            best = {"key": k, "eps": e, "typical": med, "latest": i == 3}
    return best


def trailing(sym: str, snap: dict) -> dict:
    """Trailing 12-month EPS from the last 4 consecutive quarters. The company's own results announcements come
    first (they carry the figures the company published, restated where it restated them); the snapshot fills
    quarters the announcements lack, and every quarter both have is cross-checked. Before 20 Nov 2026 the snapshot
    came first: its split of restated years can differ (FFC 2025 Q3: 16.63 on the snapshot, 17.24 announced), so a
    card could show +19% for a quarter the results post called +14%.
    Returns {ttm, quarters, all_quarters, oneoff, problems, notes}."""
    import pandas as pd

    from . import earnings
    out = {"ttm": None, "quarters": [], "problems": [], "notes": [], "all_quarters": {}, "oneoff": None}
    snap_q = dict(snap.get("eps_quarters") or {})
    try:
        h = earnings.company(sym)
    except Exception as e:  # noqa: BLE001
        h = None
        out["notes"].append(f"results history unavailable ({type(e).__name__})")
    ann = ann_quarters(h) if h is not None and len(h) else {}
    merged = {k: pick_quarter(snap_q.get(k), ann.get(k)) for k in set(snap_q) | set(ann)}
    out["all_quarters"] = merged
    used, _ = last4(merged)
    for k in used:  # only the quarters we use: older ones differ where the snapshot restated EPS for splits
        if k not in ann or k not in snap_q:
            continue
        a, b = snap_q[k], ann[k]
        if abs(a - b) > max(0.02, abs(b) * 0.02):
            gap = abs(a - b) / max(abs(b), 0.01) * 100
            msg = f"{k} EPS {a:.2f} on the snapshot vs {b:.2f} announced ({gap:.0f}% apart)"
            (out["problems"] if gap > 10 else out["notes"]).append(msg)
    behind = [k for k in ann if k not in snap_q and _qkey(k) > max((_qkey(x) for x in snap_q), default=(0, 0))]
    out["added"] = []
    if behind:
        out["notes"].append(f"snapshot is behind; added from announcements: {', '.join(sorted(behind, key=_qkey))}")
        # what those results did to book value: profit minus the cash dividend declared with them (% of face value)
        hh = h.assign(key=[f"{2000 + int(y)} Q{QNUM.get(k)}" if QNUM.get(k) and y == y else "" for k, y in zip(h["kind"], h["yy"])])
        for k in sorted(behind, key=_qkey):
            row = hh[hh["key"] == k]
            div = pd.to_numeric(row["div"], errors="coerce").fillna(0.0).iloc[-1] if len(row) and "div" in row else 0.0
            out["added"].append({"key": k, "eps": ann[k], "div_pct": float(div)})
    keys, ok = last4(merged)
    if len(keys) == 4 and ok:
        out["ttm"] = sum(merged[k] for k in keys)
        out["quarters"] = [(k, merged[k]) for k in keys]
        out["oneoff"] = oneoff(out["quarters"])
    else:
        out["problems"].append("no 4 consecutive quarters of EPS")
    if h is not None and len(h) and "bonus" in h:
        hb = h.assign(b=pd.to_numeric(h["bonus"], errors="coerce").fillna(0.0))
        recent = hb[(hb["date"] >= datetime.now() - timedelta(days=365)) & (hb["b"] > 0)]
        if len(recent):
            r = recent.iloc[-1]
            out["problems"].append(f"bonus issue of {r['b']:.0f}% announced {r['date']:%d %b %Y}: "
                                   "per-share history mixes share counts")
    return out
