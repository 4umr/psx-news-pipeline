"""Shareable image cards (1080x1350 PNG) for the morning brief and closing wrap.

Designed to be forwarded to WhatsApp / social media: one glance shows the
headline numbers, the brand and the disclaimer. Gains are blue, losses red,
and every number also carries an explicit +/- sign and ▲/▼ so colour is
never the only signal.
"""
from __future__ import annotations

import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False  # "$21bn · $26bn" must not become math text
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

SURFACE = "#fcfcfb"
TILE = "#f0efec"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8984"
UP = "#2a78d6"
DOWN = "#e34948"
BAND = "#0d366b"
BAND_2 = "#86b6ef"

W, H = 10.8, 13.5  # inches @100dpi -> 1080x1350


def _signed_color(x: float | None) -> str:
    if x is None or x == 0:
        return INK_2
    return UP if x > 0 else DOWN


def _tri(x: float | None) -> str:
    if x is None or x == 0:
        return "■"
    return "▲" if x > 0 else "▼"


def _new_card(cfg: dict, kicker: str, date: datetime):
    fig = plt.figure(figsize=(W, H), dpi=100, facecolor=SURFACE)
    # brand band
    fig.patches.append(FancyBboxPatch((0, 0.905), 1, 0.095, boxstyle="square,pad=0",
                                      transform=fig.transFigure, facecolor=BAND, edgecolor="none"))
    b = cfg.get("brand", {})
    fig.text(0.05, 0.962, b.get("name", ""), color="white", fontsize=30, fontweight="bold", va="center")
    fig.text(0.05, 0.925, f"{kicker}  ·  {date:%a %d %b %Y}".upper(), color=BAND_2, fontsize=15,
             fontweight="bold", va="center")
    # footer
    fig.add_artist(plt.Line2D([0.05, 0.95], [0.062, 0.062], transform=fig.transFigure, color="#d9d8d4", lw=1))
    by = f"By {b.get('author', '')}" + (f" · {b.get('title')}" if b.get("title") else "")
    fig.text(0.05, 0.038, by, color=INK, fontsize=14, fontweight="bold", va="center")
    fig.text(0.95, 0.038, "Information only — not investment advice", color=INK_2, fontsize=11.5,
             va="center", ha="right")
    fig.text(0.05, 0.014, "Sources: PSX via SCS Trade · SBP · NCCPL · Yahoo Finance", color=INK_3,
             fontsize=10, va="center")
    return fig


def _hero(fig, y: float, label: str, value: float, change: float, sub: str = ""):
    prev = value - change
    pct = change / prev * 100 if prev else 0
    fig.text(0.05, y + 0.045, label, color=INK_2, fontsize=18, fontweight="bold", va="center")
    fig.text(0.05, y - 0.012, f"{value:,.0f}", color=INK, fontsize=64, fontweight="bold", va="center")
    fig.text(0.95, y + 0.0, f"{_tri(change)} {change:+,.0f} pts", color=_signed_color(change),
             fontsize=26, fontweight="bold", va="center", ha="right")
    fig.text(0.95, y - 0.04, f"{pct:+.2f}%", color=_signed_color(change), fontsize=22,
             fontweight="bold", va="center", ha="right")
    if sub:
        fig.text(0.05, y - 0.068, sub, color=INK_3, fontsize=12.5, va="center")


def _tiles(fig, top: float, items: list[tuple[str, str, float | None]], cols: int = 3,
           tile_h: float = 0.075, gap: float = 0.012) -> float:
    """items: (label, value, signed-change or None). Returns the y below the grid."""
    left, right = 0.05, 0.95
    tw = (right - left - gap * (cols - 1)) / cols
    for i, (label, value, chg) in enumerate(items):
        r, c = divmod(i, cols)
        x = left + c * (tw + gap)
        y = top - (r + 1) * tile_h - r * gap
        fig.patches.append(FancyBboxPatch((x, y), tw, tile_h, boxstyle="round,pad=0,rounding_size=0.008",
                                          transform=fig.transFigure, facecolor=TILE, edgecolor="none"))
        fig.text(x + 0.018, y + tile_h - 0.02, label, color=INK_2, fontsize=12.5, va="center")
        fig.text(x + 0.018, y + 0.027, value, color=INK, fontsize=21, fontweight="bold", va="center")
        if chg is not None:
            fig.text(x + tw - 0.015, y + 0.027, f"{_tri(chg)} {chg:+.1f}%", color=_signed_color(chg),
                     fontsize=13, fontweight="bold", va="center", ha="right")
    rows = (len(items) + cols - 1) // cols
    return top - rows * tile_h - (rows - 1) * gap


def _title(fig, y: float, text: str):
    fig.text(0.05, y, text.upper(), color=INK_2, fontsize=13.5, fontweight="bold", va="center")


def _contrib_chart(fig, bottom: float, height: float, view: list[dict]):
    rows = [r for r in view if r.get("NetIndexPoint") is not None]
    rows.sort(key=lambda r: r["NetIndexPoint"])
    neg = [r for r in rows[:5] if r["NetIndexPoint"] < 0]
    pos = [r for r in rows[-5:] if r["NetIndexPoint"] > 0]
    data = neg + pos  # bottom -> top: biggest drag at the bottom, biggest lift at the top
    if not data:
        return
    ax = fig.add_axes([0.2, bottom, 0.62, height])
    ax.set_facecolor(SURFACE)
    vals = [r["NetIndexPoint"] for r in data]
    ys = range(len(data))
    ax.barh(list(ys), vals, height=0.62, color=[_signed_color(v) for v in vals], edgecolor=SURFACE, linewidth=2)
    ax.axvline(0, color=INK_3, lw=1)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([r["company_code"] for r in data], fontsize=14, color=INK, fontweight="bold")
    m = max(abs(v) for v in vals) or 1
    ax.set_xlim(-m * 1.35, m * 1.35)
    for y, v in zip(ys, vals):
        ax.text(v + (m * 0.04 if v >= 0 else -m * 0.04), y, f"{v:+.0f}", va="center",
                ha="left" if v >= 0 else "right", fontsize=13, color=INK_2, fontweight="bold")
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(left=False)


def _fmt_global(mk: dict, sym: str, prefix: str = "", suffix: str = "", dec: int = 2):
    d = mk.get(sym)
    if not d:
        return None
    return f"{prefix}{d['last']:,.{dec}f}{suffix}", d.get("pct")


def _rates_items(sbp: dict, mk: dict, cpi: dict | None = None) -> list[tuple[str, str, float | None]]:
    items: list[tuple[str, str, float | None]] = []
    if sbp.get("policy_rate") is not None:
        items.append(("SBP policy rate", f"{sbp['policy_rate']:.2f}%", None))
    g = (cpi or {}).get("general")
    if g:
        items.append((f"CPI inflation ({g['month'][:3]})", f"{g['yoy']:.1f}%", None))
        if sbp.get("policy_rate") is not None:
            items.append(("Real policy rate", f"{sbp['policy_rate'] - g['yoy']:+.1f}%", None))
    if k := sbp.get("kibor"):
        items.append(("6M KIBOR (offer)", f"{k['6M'][1]:.2f}%", None))
    if m := sbp.get("mtb"):
        y3 = m["yields"].get("3-M")
        if y3:
            items.append(("3M T-bill cut-off", f"{y3:.2f}%", None))
    if u := sbp.get("usdpkr"):
        items.append(("USD/PKR (SBP)", f"{u['m2m']:.2f}", None))
    if r := sbp.get("reserves"):
        items.append(("SBP reserves", f"${r['sbp'] / 1000:.2f}bn", None))
        items.append(("Total liquid reserves", f"${r['total'] / 1000:.2f}bn", None))
    for sym, label, pre, suf, dec in (("BZ=F", "Brent oil", "$", "", 2), ("GC=F", "Gold", "$", "", 0),
                                      ("^GSPC", "S&P 500", "", "", 0), ("DX-Y.NYB", "Dollar index", "", "", 2)):
        g = _fmt_global(mk, sym, pre, suf, dec)
        if g:
            items.append((label, g[0], g[1]))
    return items


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def _index_tiles(idx: list[dict]) -> list[tuple[str, str, float | None]]:
    rows = {r["kse_index_type"]: r for r in idx}
    out = []
    for name, label in (("KSE 30", "KSE-30"), ("KMI 30", "KMI-30"), ("KSE ALL", "All Share")):
        r = rows.get(name)
        if r:
            prev = r["kse_index_close"] - r["kse_index_change"]
            out.append((label, f"{r['kse_index_close']:,.0f}", r["kse_index_change"] / prev * 100 if prev else None))
    return out


def close_card(cfg: dict, now: datetime, idx: list[dict], view: list[dict], sbp: dict, mk: dict,
               fipi: dict | None, act: list[dict] | None = None, cpi: dict | None = None) -> bytes | None:
    rows = {r["kse_index_type"]: r for r in idx}
    k = rows.get("KSE 100")
    if not k:
        return None
    fig = _new_card(cfg, "PSX Closing Wrap", now)
    sub = f"Day range {k['kse_index_low']:,.0f} – {k['kse_index_high']:,.0f}"
    if act:
        up = sum(1 for r in act if (r.get("trading_change") or 0) > 0)
        dn = sum(1 for r in act if (r.get("trading_change") or 0) < 0)
        vol = sum(r.get("trading_vol") or 0 for r in act)
        sub += f"   ·   {up} ▲ / {dn} ▼ stocks   ·   Volume {vol / 1e6:,.0f}m shares"
    _hero(fig, 0.82, "KSE-100 INDEX", k["kse_index_close"], k["kse_index_change"], sub)
    y = _tiles(fig, 0.725, _index_tiles(idx), cols=3)
    _title(fig, y - 0.03, "Biggest index movers (points)")
    _contrib_chart(fig, y - 0.335, 0.285, view)
    y = y - 0.36
    items = []
    if fipi:
        f = fipi["summary"].get("FIPI", {})
        if f.get("net") is not None:
            items.append(("Foreign flow (FIPI)", f"{f['net']:+.2f}m $", None))
    wanted = ["SBP policy rate", "Real policy rate", "3M T-bill cut-off", "USD/PKR (SBP)", "Brent oil", "Gold"]
    pool = {i[0]: i for i in _rates_items(sbp, mk, cpi)}
    items += [pool[n] for n in wanted if n in pool]
    _title(fig, y - 0.012, "Flows, rates & global")
    _tiles(fig, y - 0.03, items[:6], cols=3, tile_h=0.07)
    return _png(fig)


def morning_card(cfg: dict, now: datetime, idx: list[dict], sbp: dict, mk: dict,
                 n_board: int = 0, n_bc: int = 0, kicker: str = "Morning Brief",
                 cpi: dict | None = None, mpc_note: str = "") -> bytes | None:
    rows = {r["kse_index_type"]: r for r in idx}
    k = rows.get("KSE 100")
    fig = _new_card(cfg, kicker, now)
    if k:
        _hero(fig, 0.82, "KSE-100 · LAST CLOSE", k["kse_index_close"], k["kse_index_change"])
        y = _tiles(fig, 0.725, _index_tiles(idx), cols=3)
    else:
        y = 0.88
    _title(fig, y - 0.03, "Rates & money market")
    rates = [i for i in _rates_items(sbp, mk, cpi) if i[2] is None]
    y = _tiles(fig, y - 0.048, rates[:6], cols=3)
    if t := (sbp.get("mtb") or {}).get("yields"):
        fig.text(0.05, y - 0.02, "T-bill cut-offs: " + "  ·  ".join(f"{a} {b:.2f}%" for a, b in t.items() if b),
                 color=INK_2, fontsize=12.5, va="center")
        y -= 0.03
    if r := sbp.get("reserves"):
        fig.text(0.05, y - 0.02, f"FX reserves: SBP ${r['sbp'] / 1000:.2f}bn  ·  Total ${r['total'] / 1000:.2f}bn"
                 f"  (as on {r['as_on']})", color=INK_2, fontsize=12.5, va="center")
        y -= 0.03
    _title(fig, y - 0.03, "Global markets")
    glob = []
    for sym, label, pre, dec in (("BZ=F", "Brent oil", "$", 2), ("GC=F", "Gold", "$", 0), ("^GSPC", "S&P 500", "", 0),
                                 ("DX-Y.NYB", "Dollar index", "", 2), ("^TNX", "US 10Y yield", "", 2),
                                 ("BTC-USD", "Bitcoin", "$", 0)):
        g = _fmt_global(mk, sym, pre, "%" if sym == "^TNX" else "", dec)
        if g:
            glob.append((label, g[0], g[1]))
    y = _tiles(fig, y - 0.048, glob, cols=3)
    notes = []
    if mpc_note:
        notes.append(mpc_note)
    if n_board or n_bc:
        notes.append(f"Today: {n_board} board meeting(s) · {n_bc} payout book closure(s)")
    if sbp.get("upcoming_auctions"):
        from .briefs import upcoming_auctions
        if a := upcoming_auctions(sbp["upcoming_auctions"]):
            notes.append(f"Next govt securities auctions: {a}")
    for i, n in enumerate(notes):
        fig.text(0.05, y - 0.03 - i * 0.03, n, color=INK_2, fontsize=13, va="center", fontweight="bold")
    return _png(fig)
