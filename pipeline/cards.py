"""Shareable image cards (PNG) for briefs and important alerts.

Designed to be forwarded to WhatsApp / social media: one glance shows the headline
numbers, the brand and the disclaimer. Gains are blue, losses red, and every number
also carries an explicit +/- sign and ▲/▼ so colour is never the only signal.

Layout uses pixel coordinates from the top-left of the card (1080 px wide).
"""
from __future__ import annotations

import io
import re
import textwrap
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False  # "$21bn · $26bn" must not become math text
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

SURFACE = "#fcfcfb"
TILE = "#f0efec"
GRID = "#e4e3df"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8984"
UP = "#2a78d6"
DOWN = "#e34948"
BAND = "#0d366b"
BAND_2 = "#86b6ef"
HIGH = "#e34948"     # impact: high
MED = "#c98a0a"      # impact: medium

_EMOJI = re.compile("[\U00010000-\U0010FFFF☀-➿️‍⬀-⯿←-⇿⌚-⏿]")


def clean(s: str) -> str:
    """Plain text for images: no HTML, no emoji (the card font can't draw them)."""
    import html as _html
    s = re.sub(r"<[^>]+>", "", s or "")
    s = _html.unescape(s)
    s = _EMOJI.sub("", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def _signed(x: float | None) -> str:
    if x is None or abs(x) < 0.05:
        return INK_2
    return UP if x > 0 else DOWN


def _tri(x: float | None) -> str:
    if x is None or abs(x) < 0.05:
        return "■"
    return "▲" if x > 0 else "▼"


class Card:
    W = 1080

    def __init__(self, cfg: dict, kicker: str, date: datetime, height: int = 1350):
        self.H = height
        self.fig = plt.figure(figsize=(self.W / 100, height / 100), dpi=100, facecolor=SURFACE)
        b = cfg.get("brand", {})
        self.rect(0, 0, self.W, 128, BAND, radius=0)
        self.text(54, 50, b.get("name", ""), 30, "white", bold=True)
        self.text(54, 98, f"{kicker}  ·  {date:%a %d %b %Y}".upper(), 15, BAND_2, bold=True)
        self.line(54, height - 84, self.W - 54, height - 84)
        by = f"By {b.get('author', '')}" + (f" · {b.get('title')}" if b.get("title") else "")
        self.text(54, height - 58, by, 14, INK, bold=True)
        self.text(self.W - 54, height - 58, "Information only — not investment advice", 11.5, INK_2, ha="right")
        self.text(54, height - 24, "Sources: PSX · SCS Trade · SBP · PBS · NCCPL · news media", 10, INK_3)

    # ------------------------------------------------------------ primitives
    def _x(self, x: float) -> float:
        return x / self.W

    def _y(self, y: float) -> float:
        return 1 - y / self.H

    def text(self, x, y, s, size, color=INK, bold=False, ha="left", va="center", italic=False):
        self.fig.text(self._x(x), self._y(y), s, fontsize=size, color=color, ha=ha, va=va,
                      fontweight="bold" if bold else "normal", fontstyle="italic" if italic else "normal")

    def rect(self, x, y, w, h, color, radius=10):
        self.fig.patches.append(FancyBboxPatch(
            (self._x(x), self._y(y + h)), w / self.W, h / self.H, transform=self.fig.transFigure,
            boxstyle=f"round,pad=0,rounding_size={radius / self.W}" if radius else "square,pad=0",
            facecolor=color, edgecolor="none"))

    def line(self, x1, y1, x2, y2, color=GRID, lw=1):
        self.fig.add_artist(plt.Line2D([self._x(x1), self._x(x2)], [self._y(y1), self._y(y2)],
                                       transform=self.fig.transFigure, color=color, lw=lw))

    def axes(self, x, y, w, h):
        ax = self.fig.add_axes([self._x(x), self._y(y + h), w / self.W, h / self.H])
        ax.set_facecolor(SURFACE)
        for s in ax.spines.values():
            s.set_visible(False)
        return ax

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=100, facecolor=SURFACE)
        plt.close(self.fig)
        return buf.getvalue()

    # ------------------------------------------------------------ blocks
    def title(self, y, s):
        self.text(54, y, s.upper(), 13.5, INK_2, bold=True)
        return y + 26

    def hero(self, y, label, value, change, sub=""):
        prev = value - change
        pct = change / prev * 100 if prev else 0
        self.text(54, y, label, 17, INK_2, bold=True)
        self.text(54, y + 62, f"{value:,.0f}", 60, INK, bold=True)
        self.text(self.W - 54, y + 40, f"{_tri(change)} {change:+,.0f} pts", 25, _signed(change), bold=True, ha="right")
        self.text(self.W - 54, y + 84, f"{pct:+.2f}%", 21, _signed(change), bold=True, ha="right")
        if sub:
            self.text(54, y + 122, sub, 12.5, INK_3)
        return y + (160 if sub else 124)

    def tiles(self, y, items, cols=3, tile_h=92, gap=14):
        """items: (label, value, signed % change or None)."""
        left, right = 54, self.W - 54
        tw = (right - left - gap * (cols - 1)) / cols
        for i, (label, value, chg) in enumerate(items):
            r, c = divmod(i, cols)
            x, yy = left + c * (tw + gap), y + r * (tile_h + gap)
            self.rect(x, yy, tw, tile_h, TILE)
            self.text(x + 18, yy + 24, label, 12.5, INK_2)
            self.text(x + 18, yy + 60, value, 20, INK, bold=True)
            if chg is not None:
                lbl = f"{_tri(chg)} {chg:+.1f}%" if abs(chg) >= 0.05 else "0.0%"
                self.text(x + tw - 14, yy + 60, lbl, 12.5, _signed(chg), bold=True, ha="right")
        rows = (len(items) + cols - 1) // cols
        return y + rows * tile_h + max(rows - 1, 0) * gap + 30

    def line_chart(self, y, h, labels, values, base=None, note=""):
        """KSE-100 path. Colour follows the result vs `base` (prev close / start)."""
        if len(values) < 2:
            self.text(54, y + h / 2, "Chart builds up as the session progresses", 13, INK_3)
            return y + h
        ax = self.axes(110, y, self.W - 54 - 110, h)
        base = base if base is not None else values[0]
        col = _signed(values[-1] - base)
        xs = list(range(len(values)))
        ax.plot(xs, values, color=col, lw=2.4, solid_capstyle="round")
        ax.fill_between(xs, values, base, color=col, alpha=0.08)
        ax.axhline(base, color=INK_3, lw=1, ls=(0, (4, 4)))
        lo, hi = min(values + [base]), max(values + [base])
        pad = (hi - lo) * 0.12 or hi * 0.002
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlim(-0.3, len(values) - 0.7)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.tick_params(colors=INK_3, labelsize=11, length=0)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        step = max(1, len(labels) // 6)
        ticks = list(range(0, len(labels), step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[i] for i in ticks])
        ax.scatter([xs[-1]], [values[-1]], s=40, color=col, zorder=3, edgecolors=SURFACE, linewidths=2)
        ax.annotate(f"{values[-1]:,.0f}", (xs[-1], values[-1]), xytext=(-8, 12), textcoords="offset points",
                    ha="right", fontsize=12, color=col, fontweight="bold")
        if note:
            ax.text(0, 1.02, note, transform=ax.transAxes, fontsize=11, color=INK_3)
        return y + h + 48  # room for the time/date labels under the chart

    def hbars(self, y, h, labels, values, fmt="{:+.0f}", signed=True, color=UP, x0=200):
        if not values:
            return y
        ax = self.axes(x0, y, self.W - 54 - x0 - 40, h)
        ys = list(range(len(values)))[::-1]  # first item on top
        cols = [_signed(v) for v in values] if signed else [color] * len(values)
        ax.barh(ys, values, height=0.62, color=cols, edgecolor=SURFACE, linewidth=2)
        ax.set_yticks(ys)
        ax.set_yticklabels(labels, fontsize=13, color=INK, fontweight="bold")
        m = max(abs(v) for v in values) or 1
        lo = -m * 1.3 if signed and min(values) < 0 else 0
        ax.set_xlim(lo, m * 1.3 if max(values) > 0 else m * 0.15)
        if signed and min(values) < 0 < max(values):
            ax.axvline(0, color=INK_3, lw=1)
        for yy, v in zip(ys, values):
            ax.text(v + (m * 0.03 if v >= 0 else -m * 0.03), yy, fmt.format(v), va="center",
                    ha="left" if v >= 0 else "right", fontsize=12, color=INK_2, fontweight="bold")
        ax.set_xticks([])
        ax.tick_params(left=False)
        return y + h + 26

    def news(self, y, items, max_items=5, width=62):
        """items: dicts with t (title), sc (score), s (source), topic label."""
        for it in items[:max_items]:
            high = it.get("sc", 0) >= 8
            tag, col = ("HIGH", HIGH) if high else ("MED", MED)
            self.rect(54, y - 14, 64, 28, col, radius=6)
            self.text(86, y, tag, 11, "white", bold=True, ha="center")
            lines = textwrap.wrap(clean(it["t"]), width)[:2]
            for j, ln in enumerate(lines):
                self.text(134, y + j * 26, ln + ("…" if j == 1 and len(textwrap.wrap(clean(it["t"]), width)) > 2 else ""),
                          14, INK, bold=(j == 0 and high))
            meta = " · ".join(x for x in (clean(it.get("topic_label", "")), clean(it.get("s", "")),
                                          clean(it.get("sectors", ""))) if x)
            self.text(134, y + len(lines) * 26, meta, 11.5, INK_3)
            y += len(lines) * 26 + 40
        return y

    def notes(self, y, lines, size=13):
        for ln in lines:
            self.text(54, y, clean(ln), size, INK_2, bold=True)
            y += 30
        return y


# ---------------------------------------------------------------- data helpers
def _rates_items(sbp: dict, cpi: dict | None = None) -> list:
    items = []
    if sbp.get("policy_rate") is not None:
        items.append(("SBP policy rate", f"{sbp['policy_rate']:.2f}%", None))
    g = (cpi or {}).get("general")
    if g:
        items.append((f"CPI inflation ({g['month'][:3]})", f"{g['yoy']:.1f}%", None))
        if sbp.get("policy_rate") is not None:
            items.append(("Real policy rate", f"{sbp['policy_rate'] - g['yoy']:+.1f}%", None))
    if k := sbp.get("kibor"):
        items.append(("6M KIBOR (offer)", f"{k['6M'][1]:.2f}%", None))
    if (m := sbp.get("mtb")) and m["yields"].get("3-M"):
        items.append(("3M T-bill cut-off", f"{m['yields']['3-M']:.2f}%", None))
    if u := sbp.get("usdpkr"):
        items.append(("USD/PKR (interbank)", f"{u['m2m']:.2f}", None))
    if r := sbp.get("reserves"):
        items.append(("SBP reserves", f"${r['sbp'] / 1000:.2f}bn", None))
    return items


def _global_items(mk: dict, syms) -> list:
    spec = {"BZ=F": ("Brent oil", "${:,.2f}"), "GC=F": ("Gold", "${:,.0f}"), "^GSPC": ("S&P 500", "{:,.0f}"),
            "DX-Y.NYB": ("Dollar index", "{:,.2f}"), "^TNX": ("US 10Y yield", "{:.2f}%"), "BTC-USD": ("Bitcoin", "${:,.0f}"),
            "ES=F": ("S&P futures", "{:,.0f}"), "^N225": ("Nikkei", "{:,.0f}"), "^HSI": ("Hang Seng", "{:,.0f}"),
            "CT=F": ("Cotton", "{:,.2f}"), "NG=F": ("Natural gas", "{:,.2f}"), "ZW=F": ("Wheat", "{:,.0f}")}
    out = []
    for s in syms:
        d = mk.get(s)
        if d and s in spec:
            out.append((spec[s][0], spec[s][1].format(d["last"]), d.get("pct")))
    return out


def _index_tiles(idx: list[dict]) -> list:
    rows = {r["kse_index_type"]: r for r in idx}
    out = []
    for name, label in (("KSE 30", "KSE-30"), ("KMI 30", "KMI-30"), ("KSE ALL", "All Share")):
        if r := rows.get(name):
            prev = r["kse_index_close"] - r["kse_index_change"]
            out.append((label, f"{r['kse_index_close']:,.0f}", r["kse_index_change"] / prev * 100 if prev else None))
    return out


def _contributors(view: list[dict], n: int = 5):
    rows = sorted([r for r in view if r.get("NetIndexPoint") is not None], key=lambda r: r["NetIndexPoint"])
    pos = [r for r in reversed(rows[-n:]) if r["NetIndexPoint"] > 0]
    neg = [r for r in rows[:n] if r["NetIndexPoint"] < 0]
    data = pos + neg
    return [r["company_code"] for r in data], [r["NetIndexPoint"] for r in data]


def _volume_leaders(act: list[dict], n: int = 8):
    rows = sorted([r for r in act if r.get("trading_vol")], key=lambda r: -r["trading_vol"])[:n]
    labels = []
    for r in rows:
        close, chg = r.get("trading_close") or 0, r.get("trading_change") or 0
        pct = chg / (close - chg) * 100 if close - chg else 0
        labels.append(f"{r['company_code']} {pct:+.1f}%")
    return labels, [r["trading_vol"] / 1e6 for r in rows]


def _sector_volume(act: list[dict], name_fn, n: int = 6):
    agg: dict[str, float] = {}
    for r in act:
        s = name_fn(r.get("sector_name", ""))
        agg[s] = agg.get(s, 0) + (r.get("trading_vol") or 0)
    tot = sum(agg.values()) or 1
    top = sorted(agg.items(), key=lambda kv: -kv[1])[:n]
    return [k[:20] for k, _ in top], [v / tot * 100 for _, v in top]


def _breadth(act: list[dict]):
    up = sum(1 for r in act if (r.get("trading_change") or 0) > 0)
    dn = sum(1 for r in act if (r.get("trading_change") or 0) < 0)
    vol = sum(r.get("trading_vol") or 0 for r in act)
    return up, dn, len(act) - up - dn, vol


def _pct_movers(view: list[dict], n: int = 4) -> tuple[str, str]:
    rows = [(r["company_code"], (r["CurrentPrice"] / r["LDCP"] - 1) * 100) for r in view
            if r.get("LDCP") and r.get("CurrentPrice")]
    rows.sort(key=lambda x: -x[1])
    up = "  ".join(f"{c} {p:+.1f}%" for c, p in rows[:n] if p > 0)
    dn = "  ".join(f"{c} {p:+.1f}%" for c, p in rows[::-1][:n] if p < 0)
    return up, dn


def _breadth_tiles(act: list[dict]) -> list:
    up, dn, unch, vol = _breadth(act)
    return [("Advancers", f"{up}", None), ("Decliners", f"{dn}", None), ("Volume (shares)", f"{vol / 1e6:,.0f}m", None)]


def _context_tiles(hist: list[dict], fipi: dict | None) -> list:
    if len(hist) < 5:
        return []
    closes = [r["kse_index_close"] for r in hist]
    vols = [r.get("kse_index_value") or 0 for r in hist]
    items = [(f"{len(hist)}-session high", f"{max(closes):,.0f}", None),
             (f"{len(hist)}-session low", f"{min(closes):,.0f}", None),
             ("From high", f"{(closes[-1] / max(closes) - 1) * 100:+.1f}%", None),
             ("Last session volume", f"{vols[-1] / 1e6:,.0f}m", None),
             (f"Avg volume ({len(hist)}d)", f"{sum(vols) / len(vols) / 1e6:,.0f}m", None)]
    f = (fipi or {}).get("summary", {}).get("FIPI", {})
    if f.get("net") is not None:
        items.append((f"Foreign flow ({fipi['date'][5:]})", f"{f['net']:+.2f}m $", None))
    else:
        items.append(("Volume vs average", f"{(vols[-1] / (sum(vols) / len(vols)) - 1) * 100:+.0f}%", None))
    return items


def _intraday(ctx):
    pts = (ctx.get("intraday") or {}).get("pts") or []
    return [p[0] for p in pts], [p[1] for p in pts], (ctx.get("intraday") or {}).get("prev")


# ---------------------------------------------------------------- cards
def alert_card(cfg: dict, kicker: str, title: str, body: list[str], source: str, when: datetime,
               breaking: bool = False, tiles: list | None = None) -> bytes:
    """Square card for an important alert/news item."""
    c = Card(cfg, "Market alert", when, height=1080)
    col = DOWN if breaking else BAND
    k = clean(kicker).upper()
    c.rect(54, 160, min(30 + 11.5 * len(k), 972), 40, col, radius=8)
    c.text(70, 180, k, 14, "white", bold=True)
    y = 250
    for ln in textwrap.wrap(clean(title), 36)[:4]:
        c.text(54, y, ln, 33, INK, bold=True)
        y += 50
    y += 16
    c.line(54, y, c.W - 54, y)
    y += 34
    limit = 760 if tiles else 930
    for raw in body:
        txt = clean(raw)
        if not txt:
            y += 12
            continue
        for ln in textwrap.wrap(txt, 56)[:4]:
            if y > limit:
                break
            c.text(54, y, ln, 20, INK_2)
            y += 36
    if tiles:
        c.title(806, "Market right now")
        c.tiles(832, tiles[:3], tile_h=92)
    if source:
        c.text(54, 975, clean(source)[:90], 13, INK_3, italic=True)
    return c.png()


def morning_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = []
    # Card 1 — market snapshot with 1-month trend
    c = Card(cfg, ctx.get("kicker", "Morning Brief"), now)
    idx = ctx.get("idx") or []
    k = next((r for r in idx if r["kse_index_type"] == "KSE 100"), None)
    y = 170
    if k:
        y = c.hero(y, "KSE-100 · LAST CLOSE", k["kse_index_close"], k["kse_index_change"])
    hist = ctx.get("hist") or []
    if len(hist) >= 5:
        vals = [r["kse_index_close"] for r in hist]
        labels = [r["date"].strftime("%d %b") for r in hist]
        chg = (vals[-1] / vals[0] - 1) * 100
        y = c.title(y + 4, f"KSE-100 · last {len(vals)} sessions ({chg:+.1f}%)")
        y = c.line_chart(y, 250, labels, vals, base=vals[0])
    y = c.tiles(y + 10, _index_tiles(idx))
    y = c.title(y, "Rates & money market")
    y = c.tiles(y, _rates_items(ctx.get("sbp") or {}, ctx.get("cpi"))[:6])
    ctxt = _context_tiles(hist, ctx.get("fipi"))
    if ctxt and y < 1000:
        y = c.title(y, "One-month market context")
        c.tiles(y, ctxt, tile_h=84)
    out.append(c.png())

    # Card 2 — today: global cues, commodities, overnight news with impact, agenda
    c = Card(cfg, "Today · Global cues & news", now)
    y = c.title(170, "Global markets")
    y = c.tiles(y, _global_items(ctx.get("mk") or {}, ["BZ=F", "GC=F", "^GSPC", "DX-Y.NYB", "^TNX", "BTC-USD"]))
    cm = _global_items(ctx.get("cmk") or {}, ["ES=F", "^N225", "^HSI", "CT=F", "NG=F", "ZW=F"])
    if cm:
        y = c.title(y, "Futures, Asia & sector commodities")
        y = c.tiles(y, cm, tile_h=84)
    news = ctx.get("headlines") or []
    if news:
        y = c.title(y + 4, "Top stories overnight · impact")
        y = c.news(y + 12, news, max_items=4)
    c.notes(max(y, 1130), ctx.get("agenda", [])[:4])
    out.append(c.png())
    return out


def midday_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = []
    view, act = ctx.get("view") or [], ctx.get("act") or []
    labels, vals, prev = _intraday(ctx)
    cur = view[0].get("CurrentIndex") if view else None
    pre = view[0].get("PreIndex") if view else None
    c = Card(cfg, "Midday Pulse", now)
    y = 170
    if cur and pre:
        up, dn, unch, vol = _breadth(act) if act else (0, 0, 0, 0)
        y = c.hero(y, f"KSE-100 · AT {now:%H:%M} PKT", cur, cur - pre,
                   f"{up} stocks up · {dn} down · volume so far {vol / 1e6:,.0f}m shares" if act else "")
    y = c.title(y, "Intraday path vs previous close")
    y = c.line_chart(y + 6, 260, labels, vals, base=prev or pre)
    vl, vv = _volume_leaders(act)
    if vv:
        y = c.title(y, "Where the volume is (million shares)")
        y = c.hbars(y + 4, 340, vl, vv, fmt="{:,.1f}m", signed=False, color=UP, x0=270)
    up_s, dn_s = _pct_movers(view)
    if up_s or dn_s:
        y = c.title(y + 4, "Top % movers in KSE-100")
        c.notes(y + 10, [f"Gainers:  {up_s}" if up_s else "", f"Losers:  {dn_s}" if dn_s else ""])
    out.append(c.png())
    out.append(_movers_card(cfg, now, ctx, "Midday · Movers, sectors & news"))
    return out


def _movers_card(cfg: dict, now: datetime, ctx: dict, kicker: str) -> bytes:
    view, act = ctx.get("view") or [], ctx.get("act") or []
    c = Card(cfg, kicker, now)
    y = c.title(170, "Biggest KSE-100 index movers (points)")
    lab, val = _contributors(view, 4)
    y = c.hbars(y + 4, 300, lab, val)
    sec = ctx.get("sectors") or []
    if sec:
        y = c.title(y + 4, "Sector performance (KSE-100, % change)")
        sec = sorted(sec, key=lambda s: -s[1])
        y = c.hbars(y + 4, 200, [s[0][:22] for s in sec], [s[1] for s in sec], fmt="{:+.2f}%", x0=270)
    if act and ctx.get("sector_name"):
        sl, sv = _sector_volume(act, ctx["sector_name"], 5)
        y = c.title(y + 4, "Share of market volume by sector")
        y = c.hbars(y + 4, 170, [x[:22] for x in sl], sv, fmt="{:.0f}%", signed=False, color=BAND_2, x0=270)
    news = ctx.get("headlines") or []
    if news and y < 1060:
        y = c.title(y + 4, "Today's news · impact")
        c.news(y + 12, news, max_items=max(1, min(3, int((1180 - y) // 70))))
    return c.png()


def close_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = []
    idx = ctx.get("idx") or []
    k = next((r for r in idx if r["kse_index_type"] == "KSE 100"), None)
    if not k:
        return out
    act = ctx.get("act") or []
    c = Card(cfg, "PSX Closing Wrap", now)
    sub = f"Range {k['kse_index_low']:,.0f} – {k['kse_index_high']:,.0f}"
    if act:
        up, dn, _, vol = _breadth(act)
        sub += f"  ·  {up} up / {dn} down  ·  volume {vol / 1e6:,.0f}m shares"
    y = c.hero(170, "KSE-100 INDEX", k["kse_index_close"], k["kse_index_change"], sub)
    labels, vals, prev = _intraday(ctx)
    y = c.title(y, "Today's path vs previous close")
    y = c.line_chart(y + 6, 250, labels, vals, base=prev or (k["kse_index_close"] - k["kse_index_change"]))
    y = c.tiles(y + 6, _index_tiles(idx))
    items = []
    f = (ctx.get("fipi") or {}).get("summary", {}).get("FIPI", {})
    if f.get("net") is not None:
        items.append(("Foreign flow (FIPI)", f"{f['net']:+.2f}m $", None))
    pool = {i[0]: i for i in _rates_items(ctx.get("sbp") or {}, ctx.get("cpi"))}
    items += [pool[n] for n in ("SBP policy rate", "Real policy rate", "3M T-bill cut-off", "USD/PKR (interbank)") if n in pool]
    items += _global_items(ctx.get("mk") or {}, ["BZ=F", "GC=F"])
    if act:
        y = c.title(y, "Market breadth & turnover")
        y = c.tiles(y, _breadth_tiles(act), tile_h=84)
    y = c.title(y, "Flows, rates & global")
    c.tiles(y, items[:6], tile_h=84)
    out.append(c.png())
    out.append(_movers_card(cfg, now, ctx, "Closing Wrap · Movers, sectors & news"))
    return out
