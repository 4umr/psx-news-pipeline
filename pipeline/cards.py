"""Infographic cards (PNG) in the PAKISTAN INVESTORS brand style.

Every brief and important alert is posted as a dark, premium infographic:
logo + tagline on top, a big serif title, rounded panels with large numbers,
sparklines, gainers/losers, "Key highlights" and the brand footer.

Colour system (from the brand kit): dark green background, growth green,
gold accent, cream text. Each content category has its own accent:
PSX green · US blue · commodities gold · fixed income purple ·
economy slate · breaking news / alerts red. Gains are green with ▲, losses
red with ▼, so colour is never the only signal.

Layout uses pixel coordinates from the top-left of the card (1080 px wide).
Drop a transparent `assets/logo.png` into the repo to replace the drawn logo.
"""
from __future__ import annotations

import io
import re
import textwrap
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False  # "$21bn · $26bn" must not become math text
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402
from matplotlib.patches import Arc, Circle, FancyBboxPatch, Polygon, Rectangle  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"

CREAM = "#f4ecd8"
WORDMARK = "#ead9ae"
GOLD = "#d4a640"
GOLD_L = "#f0d38a"
UP = "#46d27f"
DOWN = "#ff5f5f"
FLAT = "#b8bfb6"
SERIF = "STIXGeneral"

# bg2 = top of the gradient, bg = bottom; accent = category colour
THEMES = {
    "psx":       {"bg": "#04110b", "bg2": "#0a2617", "accent": "#43c36c", "accent2": "#9be3b2",
                  "panel": "#0b2015", "edge": "#24553a", "muted": "#a9bbad", "nav": "MARKETS"},
    "us":        {"bg": "#040b18", "bg2": "#0a1a36", "accent": "#4d94f5", "accent2": "#a8caff",
                  "panel": "#0a1830", "edge": "#24426e", "muted": "#a6b6cc", "nav": "GLOBAL MARKETS"},
    "commodity": {"bg": "#110b04", "bg2": "#2b1d08", "accent": "#e5ad3e", "accent2": "#f6d68e",
                  "panel": "#1f160a", "edge": "#5e4519", "muted": "#c4b394", "nav": "COMMODITIES"},
    "fixed":     {"bg": "#0d0818", "bg2": "#1f1238", "accent": "#a37bff", "accent2": "#d3c1ff",
                  "panel": "#181030", "edge": "#43306e", "muted": "#b7abcf", "nav": "FIXED INCOME"},
    "economy":   {"bg": "#090d11", "bg2": "#18232d", "accent": "#93abc2", "accent2": "#cfdbe6",
                  "panel": "#131b23", "edge": "#33434f", "muted": "#a7b2bc", "nav": "ECONOMY"},
    "alert":     {"bg": "#140506", "bg2": "#330b0e", "accent": "#f04f4f", "accent2": "#ffaaaa",
                  "panel": "#240a0c", "edge": "#64202a", "muted": "#cfaaaa", "nav": "MARKETS"},
    "gold":      {"bg": "#0a0d07", "bg2": "#1f2310", "accent": "#d9b04e", "accent2": "#f3d995",
                  "panel": "#171a0c", "edge": "#4f4a1f", "muted": "#bdb798", "nav": "MARKETS"},
}

_EMOJI = re.compile("[\U00010000-\U0010FFFF☀-➿️‍⬀-⯿←-⇿⌚-⏿]")


def clean(s: str) -> str:
    """Plain text for images: no HTML, no emoji (the card font can't draw them)."""
    import html as _html

    from .style import tidy
    s = re.sub(r"<[^>]+>", "", s or "")
    s = tidy(_html.unescape(s))  # no emoji, no long dashes
    s = _EMOJI.sub("", s)
    return re.sub(r"[ \t]+", " ", s).strip()


LEVEL_COL = {"High": "#d64545", "Medium": "#b8862a", "Low": "#5d7387"}
TONE_COL = {"Positive": "#2f9e5b", "Negative": "#d64545", "Mixed": "#b8862a", "Neutral": "#5d7387"}


def sp(s: str) -> str:
    """Letter-spaced caps (the brand's wide-tracked look)."""
    return " ".join(s.upper())


def _col(x: float | None) -> str:
    if x is None or abs(x) < 1e-9:
        return FLAT
    return UP if x > 0 else DOWN


def _tri(x: float | None) -> str:
    if x is None or abs(x) < 1e-9:
        return "■"
    return "▲" if x > 0 else "▼"


def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def _wrap(s: str, width_px: float, size: float, bold: bool = False, serif: bool = False) -> list[str]:
    per_char = size * 1.389 * (0.50 if serif else (0.60 if bold else 0.55))
    return textwrap.wrap(clean(s), max(8, int(width_px / per_char)))


# ===================================================================== card
class Card:
    W = 1080
    L, R = 40, 1040

    def __init__(self, cfg: dict, theme: str = "psx", height: int = 1560):
        self.cfg = cfg
        self.t = THEMES.get(theme, THEMES["psx"])
        self.H = height
        self.fig = plt.figure(figsize=(self.W / 100, height / 100), dpi=100)
        self._background()
        self._header()

    # ------------------------------------------------------------ primitives
    def _x(self, x: float) -> float:
        return x / self.W

    def _y(self, y: float) -> float:
        return 1 - y / self.H

    def text(self, x, y, s, size, color=CREAM, bold=False, ha="left", va="center", serif=False,
             alpha=1.0, italic=False):
        self.fig.text(self._x(x), self._y(y), s, fontsize=size, color=color, ha=ha, va=va, alpha=alpha,
                      fontweight="bold" if bold else "normal", fontstyle="italic" if italic else "normal",
                      family=SERIF if serif else "DejaVu Sans", zorder=10)

    def rect(self, x, y, w, h, color, radius=18, edge=None, lw=1.2, alpha=1.0, z=1):
        self.fig.patches.append(FancyBboxPatch(
            (self._x(x), self._y(y + h)), w / self.W, h / self.H, transform=self.fig.transFigure,
            boxstyle=f"round,pad=0,rounding_size={radius / self.W}" if radius else "square,pad=0",
            facecolor=color, edgecolor=edge or "none", linewidth=lw if edge else 0, alpha=alpha, zorder=z))

    def line(self, x1, y1, x2, y2, color=None, lw=1.0, alpha=1.0):
        self.fig.add_artist(plt.Line2D([self._x(x1), self._x(x2)], [self._y(y1), self._y(y2)],
                                       transform=self.fig.transFigure, color=color or self.t["edge"],
                                       lw=lw, alpha=alpha, zorder=4))

    def axes(self, x, y, w, h, z=5):
        ax = self.fig.add_axes([self._x(x), self._y(y + h), w / self.W, h / self.H], zorder=z)
        ax.patch.set_alpha(0)
        ax.axis("off")
        return ax

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=100)
        plt.close(self.fig)
        return buf.getvalue()

    # ------------------------------------------------------------ frame
    def _background(self):
        ax = self.fig.add_axes([0, 0, 1, 1], zorder=-10)
        ax.axis("off")
        top, bot = np.array(to_rgb(self.t["bg2"])), np.array(to_rgb(self.t["bg"]))
        k = np.linspace(0, 1, 400)[:, None, None] ** 0.8
        grad = np.repeat(top * (1 - k) + bot * k, 2, axis=1)
        ax.imshow(grad, extent=[0, 1, 0, 1], aspect="auto", interpolation="bicubic", origin="upper")
        yy, xx = np.mgrid[0:1:220j, 0:1:220j]
        aspect = self.H / self.W
        glow = np.zeros((220, 220, 4))
        glow[..., :3] = to_rgb(self.t["accent"])
        d = np.sqrt((xx - 0.86) ** 2 + ((yy - 0.10) * aspect) ** 2)
        glow[..., 3] = np.clip(1 - d / 0.80, 0, 1) ** 2.2 * 0.30
        ax.imshow(glow, extent=[0, 1, 0, 1], aspect="auto", origin="upper", interpolation="bilinear")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

    def logo(self, x, y, s):
        ax = self.axes(x, y, s, s, z=8)
        p = ASSETS / "logo.png"
        if p.exists():
            ax.imshow(plt.imread(str(p)))
            ax.set_aspect("equal")
            return
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        # "P": cream stem + bowl, green inner stem with a minaret tip
        ax.add_patch(Rectangle((2, 4), 17, 86, color=CREAM, zorder=2))
        ax.add_patch(Rectangle((2, 86), 30, 8, color=CREAM, zorder=2))
        ax.add_patch(Rectangle((2, 50), 30, 8, color=CREAM, zorder=2))
        ax.add_patch(Arc((32, 72), 36, 36, theta1=-90, theta2=90, color=CREAM, lw=s * 0.058, zorder=2))
        ax.add_patch(Rectangle((7, 4), 7, 52, color="#2f8f4e", zorder=3))
        ax.add_patch(Polygon([(7, 56), (10.5, 66), (14, 56)], color="#2f8f4e", zorder=3))
        # rising bars (green -> gold) and the gold arrow
        for bx, bh, c in ((56, 26, "#2f8f4e"), (70, 40, "#7aa548"), (84, 56, GOLD)):
            ax.add_patch(Rectangle((bx, 4), 11, bh, color=c, zorder=2))
        ax.annotate("", xy=(99, 98), xytext=(46, 40), zorder=5,
                    arrowprops={"arrowstyle": "-|>,head_length=0.8,head_width=0.5", "color": GOLD_L, "lw": 3.2})

    def _header(self):
        b = self.cfg.get("brand", {})
        self.logo(40, 26, 100)
        words = (b.get("name") or "PAKISTAN INVESTORS").upper().split(" ", 1)
        self.text(158, 52, sp(words[0]), 23, WORDMARK, bold=True, serif=True)
        if len(words) > 1:
            self.text(158, 88, sp(words[1]), 23, WORDMARK, bold=True, serif=True)
        self.text(160, 118, "IDEAS  |  INSIGHTS  |  OPPORTUNITIES  |  COMMUNITY", 8.6, self.t["muted"], bold=True)
        self.text(self.R, 62, sp("Understand money."), 11, CREAM, ha="right")
        self.text(self.R, 86, sp("Understand markets."), 11, CREAM, ha="right")

    def title_block(self, t1: str, t2: str, sub: str, y: int = 160, series=None, base=None) -> int:
        s1 = min(80, 1000 / max(1, len(t1)) / 1.389 / 0.74)
        s2 = min(50, 1000 / max(1, len(t2)) / 1.389 / 0.74)
        if series and len(series) >= 2:
            # keep the decorative chart clear of the title text
            x0 = max(640, 40 + max(len(t1) * s1, len(t2) * s2) * 1.389 * 0.74 + 50)
            if x0 <= 1040 - 220:
                self._deco(series, base, x0)
        self.text(self.L, y + 70, t1.upper(), s1, CREAM, bold=True, serif=True)
        self.text(self.L + 2, y + 150, t2.upper(), s2, self.t["accent"], bold=True, serif=True)
        self.text(self.L + 4, y + 205, sp(sub), 14, CREAM, alpha=0.85)
        return y + 245

    def _deco(self, series, base=None, x0=640):
        """Faint real-data chart beside the title (top-right), like the brand templates."""
        ax = self.axes(x0, 150, 1040 - x0, 240, z=0)
        xs = np.arange(len(series))
        col = self.t["accent"]
        ax.plot(xs, series, color=col, lw=10, alpha=0.06, solid_capstyle="round")
        ax.plot(xs, series, color=col, lw=2.2, alpha=0.40)
        lo, hi = min(series), max(series)
        pad = (hi - lo) * 0.15 or 1
        ax.fill_between(xs, series, lo - pad, color=col, alpha=0.05)
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlim(0, len(series) - 1)

    def footer(self):
        y0 = self.H - 172
        self.line(self.L, y0, self.R, y0, color=GOLD, lw=1, alpha=0.45)
        navs = [(self.t.get("nav", "MARKETS"), "bars" if self.t.get("nav") != "GLOBAL MARKETS" else "globe"),
                ("INSIGHTS", "search"), ("EDUCATION", "book"), ("COMMUNITY", "people")]
        cw = (self.R - self.L) / 4
        for i, (label, icon) in enumerate(navs):
            cx = self.L + cw * (i + 0.5)
            self._icon(icon, cx, y0 + 52, 46)
            self.text(cx, y0 + 98, sp(label), 9.5, CREAM, ha="center", alpha=0.9)
            if i:
                self.line(self.L + cw * i, y0 + 24, self.L + cw * i, y0 + 112, alpha=0.6)
        b = self.cfg.get("brand", {})
        by = f"By {b.get('author', '')}" + (f" · {b.get('title')}" if b.get("title") else "")
        self.text(self.L, self.H - 30, by, 11, CREAM, bold=True, alpha=0.9)
        if b.get("channel_link"):
            self.text(self.W / 2, self.H - 30, clean(b["channel_link"]).replace("https://", ""), 10.5,
                      self.t["accent2"], ha="center")
        self.text(self.R, self.H - 30, "Information only · not investment advice", 10.5, self.t["muted"], ha="right")

    def _icon(self, kind: str, cx: float, cy: float, s: float):
        ax = self.axes(cx - s / 2, cy - s / 2, s, s, z=7)
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        kw = {"fill": False, "edgecolor": CREAM, "lw": 1.8, "alpha": 0.9}
        if kind == "bars":
            for bx, bh in ((1.2, 3.5), (4.2, 5.5), (7.2, 8)):
                ax.add_patch(Rectangle((bx, 1), 2, bh, **kw))
        elif kind == "globe":
            ax.add_patch(Circle((5, 5), 4.2, **kw))
            ax.add_patch(Arc((5, 5), 4, 8.4, **{k: v for k, v in kw.items() if k != "fill"}))
            ax.plot([0.8, 9.2], [5, 5], color=CREAM, lw=1.6, alpha=0.9)
            ax.plot([1.6, 8.4], [7.5, 7.5], color=CREAM, lw=1.2, alpha=0.9)
            ax.plot([1.6, 8.4], [2.5, 2.5], color=CREAM, lw=1.2, alpha=0.9)
        elif kind == "search":
            ax.add_patch(Circle((4.2, 5.8), 3.0, **kw))
            ax.plot([6.4, 9.2], [3.6, 0.8], color=CREAM, lw=2.6, alpha=0.9, solid_capstyle="round")
        elif kind == "book":
            ax.add_patch(Polygon([(5, 1.5), (0.6, 2.6), (0.6, 8.8), (5, 7.7)], **kw))
            ax.add_patch(Polygon([(5, 1.5), (9.4, 2.6), (9.4, 8.8), (5, 7.7)], **kw))
        elif kind == "people":
            for hx, hy, r, bw in ((5, 7.2, 1.35, 5.2), (1.9, 6.0, 1.0, 3.4), (8.1, 6.0, 1.0, 3.4)):
                ax.add_patch(Circle((hx, hy), r, **kw))
                ax.add_patch(Arc((hx, hy - r - 2.4), bw, 4.4, theta1=0, theta2=180,
                                 **{k: v for k, v in kw.items() if k != "fill"}))
        elif kind == "doc":
            ax.add_patch(Polygon([(1.5, 0.5), (1.5, 9.5), (6.5, 9.5), (8.5, 7.5), (8.5, 0.5)], **kw))
            for yy in (6.5, 4.8, 3.1):
                ax.plot([3, 7], [yy, yy], color=CREAM, lw=1.4, alpha=0.9)

    # ------------------------------------------------------------ blocks
    def panel(self, x, y, w, h, edge=None, fill=None, lw=1.3):
        self.rect(x, y, w, h, fill or self.t["panel"], radius=20, edge=edge or self.t["edge"], lw=lw, alpha=0.92)

    def label(self, x, y, s, size=13, color=None, ha="left"):
        self.text(x, y, sp(s), size, color or CREAM, bold=True, ha=ha)

    def spark(self, x, y, w, h, series, base=None, color=None, dot=True, times=None):
        if not series or len(series) < 2:
            return
        ax = self.axes(x, y, w, h)
        xs = np.arange(len(series))
        b = base if base is not None else series[0]
        col = color or _col(series[-1] - b)
        lo, hi = min(series + [b]), max(series + [b])
        pad = (hi - lo) * 0.12 or abs(hi) * 0.002 or 1
        ax.fill_between(xs, series, lo - pad, color=col, alpha=0.10, lw=0)
        ax.plot(xs, series, color=col, lw=7, alpha=0.13, solid_capstyle="round")
        ax.plot(xs, series, color=col, lw=2.3, solid_capstyle="round")
        if base is not None:
            ax.axhline(base, color=FLAT, lw=1, ls=(0, (3, 4)), alpha=0.45)
        if dot:
            ax.scatter([xs[-1]], [series[-1]], s=40, color=col, zorder=3, edgecolors="none")
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_xlim(0, len(series) - 1 + 0.4)
        if times:
            self.text(x, y + h + 14, times[0], 9.5, self.t["muted"])
            self.text(x + w, y + h + 14, times[-1], 9.5, self.t["muted"], ha="right")

    def hero(self, y, label, value, change, pct, series=None, base=None, times=None, unit="pts", h=210) -> int:
        self.panel(self.L, y, 1000, h)
        self.label(self.L + 30, y + 40, label, 17)
        self.text(self.L + 28, y + 108, _num(value), 58, CREAM, bold=True)
        if change is not None:
            chg = f"{_tri(change)} {change:+,.0f}" if abs(change) >= 10 else f"{_tri(change)} {change:+,.2f}"
            self.text(self.L + 30, y + 172, f"{chg} {unit}  ({pct:+.2f}%)".replace("  (", " ("), 22,
                      _col(change), bold=True)
        self.spark(self.L + 520, y + 30, 450, h - 75, series, base, times=times)
        return y + h + 16

    def strip(self, y, items, h=108) -> int:
        """items: (label, value, colour or None)."""
        if not items:
            return y
        self.panel(self.L, y, 1000, h)
        cw = 1000 / len(items)
        for i, (lab, val, col) in enumerate(items):
            x = self.L + cw * i + 28
            self.text(x, y + 36, sp(lab), 11, self.t["muted"], bold=True)
            self.text(x, y + 76, val, 21 if len(items) <= 4 else 18, col or CREAM, bold=True)
            if i:
                self.line(self.L + cw * i, y + 20, self.L + cw * i, y + h - 20, alpha=0.8)
        return y + h + 16

    def movers(self, y, left, right, left_title="Top gainers", right_title="Top losers", n=5) -> int:
        """left/right: (code, % change)."""
        if not left and not right:
            return y
        h = 70 + n * 44 + 14
        self.panel(self.L, y, 1000, h)
        self.line(self.L + 500, y + 22, self.L + 500, y + h - 22, alpha=0.8)
        for x0, title, rows in ((self.L + 30, left_title, left), (self.L + 530, right_title, right)):
            self.label(x0, y + 38, title, 14)
            for i, (code, pct) in enumerate(rows[:n]):
                yy = y + 88 + i * 44
                self.text(x0, yy, _tri(pct), 15, _col(pct))
                self.text(x0 + 34, yy, clean(code)[:16], 17, CREAM, bold=True)
                self.text(x0 + 440, yy, f"{pct:+.2f}%", 17, _col(pct), bold=True, ha="right")
            if not rows:
                self.text(x0, y + 88, "None today", 15, self.t["muted"])
        return y + h + 16

    def highlights(self, y, lines, title="Key highlights", h=None, max_lines=7) -> int:
        lines = [clean(x) for x in lines if clean(x)]
        if not lines:
            return y
        wrapped, used = [], 0
        for ln in lines:
            w = _wrap(ln, 860, 15)
            if used + len(w) > max_lines or (h and 92 + (used + len(w)) * 34 + (len(wrapped) + 1) * 8 > h):
                continue  # skip what doesn't fit; a shorter bullet further down may still fit
            wrapped.append(w)
            used += len(w)
        if not wrapped:
            return y
        n = sum(len(w) for w in wrapped)
        natural = 78 + n * 34 + len(wrapped) * 8
        h = h if h and h - natural < 260 else natural  # fill the space, but don't leave a huge empty box
        self.panel(self.L, y, 1000, h)
        self._icon("doc", self.L + 50, y + 44, 40)
        self.label(self.L + 92, y + 44, title, 15)
        yy = y + 92
        for w in wrapped:
            self.rect(self.L + 98, yy - 5, 10, 10, self.t["accent"], radius=5, z=6)
            for j, ln in enumerate(w):
                self.text(self.L + 124, yy + j * 34, ln, 15, CREAM)
            yy += len(w) * 34 + 8
        return y + h + 16

    def tiles(self, y, items, title=None, cols=3, tile_h=96) -> int:
        """items: (label, value, change % or None)."""
        if not items:
            return y
        rows = (len(items) + cols - 1) // cols
        top = 62 if title else 22
        h = top + rows * tile_h + (rows - 1) * 12 + 22
        self.panel(self.L, y, 1000, h)
        if title:
            self.label(self.L + 30, y + 38, title, 14)
        tw = (1000 - 44 - 12 * (cols - 1)) / cols
        for i, (lab, val, chg) in enumerate(items):
            r, c = divmod(i, cols)
            x, yy = self.L + 22 + c * (tw + 12), y + top + r * (tile_h + 12)
            self.rect(x, yy, tw, tile_h, self.t["bg2"], radius=14, edge=self.t["edge"], lw=0.8, alpha=0.75, z=2)
            self.text(x + 18, yy + 28, clean(lab), 11.5, self.t["muted"])
            self.text(x + 18, yy + 66, val, 20 if cols <= 3 else 17, CREAM, bold=True)
            if chg is not None:
                self.text(x + tw - 14, yy + 66, f"{_tri(chg)} {chg:+.1f}%", 12, _col(chg), bold=True, ha="right")
        return y + h + 16

    def bars(self, y, title, labels, values, fmt="{:+.0f}", signed=True, h=None, x0=250) -> int:
        if not values:
            return y
        h = h or 80 + len(values) * 40
        self.panel(self.L, y, 1000, h)
        self.label(self.L + 30, y + 38, title, 14)
        ax = self.axes(self.L + x0, y + 62, 1000 - x0 - 110, h - 80)
        ys = list(range(len(values)))[::-1]
        cols = [_col(v) for v in values] if signed else [self.t["accent"]] * len(values)
        ax.barh(ys, values, height=0.58, color=cols, alpha=0.95)
        m = max(abs(v) for v in values) or 1
        lo = -m * 1.05 if signed and min(values) < 0 else 0
        ax.set_xlim(lo, m * 1.05 if max(values) > 0 else m * 0.1)
        ax.set_ylim(-0.6, len(values) - 0.4)
        if signed and min(values) < 0 < max(values):
            ax.axvline(0, color=FLAT, lw=1, alpha=0.6)
        for yy, lab, v in zip(ys, labels, values):
            fy = y + 62 + (h - 80) * (1 - (yy + 0.6) / len(values))
            self.text(self.L + x0 - 18, fy, clean(lab)[:18], 13.5, CREAM, bold=True, ha="right")
            self.text(self.R - 24, fy, fmt.format(v), 13.5, _col(v) if signed else CREAM, bold=True, ha="right")
        return y + h + 16

    def chip(self, x, y, s, color, size=12.5) -> float:
        s = clean(s).upper()
        w = 26 + len(s) * size * 1.08
        self.rect(x, y - 19, w, 38, color, radius=10, z=6)
        self.text(x + 13, y, s, size, "white", bold=True)
        return x + w + 10


# ===================================================================== data helpers
def _idx(idx: list[dict], name: str) -> dict | None:
    return next((r for r in idx if r["kse_index_type"] == name), None)


def _index_tiles(idx: list[dict]) -> list:
    out = []
    for name, label in (("KSE 30", "KSE-30"), ("KMI 30", "KMI-30"), ("KSE ALL", "All Share")):
        if r := _idx(idx, name):
            prev = r["kse_index_close"] - r["kse_index_change"]
            out.append((label, f"{r['kse_index_close']:,.0f}", r["kse_index_change"] / prev * 100 if prev else None))
    return out


def _rates_items(sbp: dict, cpi: dict | None = None) -> list:
    items = []
    if sbp.get("policy_rate") is not None:
        items.append(("SBP policy rate", f"{sbp['policy_rate']:.2f}%", None))
    g = (cpi or {}).get("general")
    if g:
        items.append((f"CPI inflation ({g['month'][:3]})", f"{g['yoy']:.1f}%", None))
        if sbp.get("policy_rate") is not None:
            items.append(("Real policy rate", f"{sbp['policy_rate'] - g['yoy']:+.1f}%", None))
    if (m := sbp.get("mtb")) and m["yields"].get("3-M"):
        items.append(("3M T-bill cut-off", f"{m['yields']['3-M']:.2f}%", None))
    if u := sbp.get("usdpkr"):
        items.append(("USD/PKR interbank", f"{u['m2m']:.2f}", None))
    if r := sbp.get("reserves"):
        items.append(("SBP reserves", f"${r['sbp'] / 1000:.2f}bn", None))
    if (k := sbp.get("kibor")) and len(items) < 6:
        items.append(("6M KIBOR", f"{k['6M'][1]:.2f}%", None))
    return items


def _pct_movers(view: list[dict], n: int = 5):
    rows = [(r["company_code"], (r["CurrentPrice"] / r["LDCP"] - 1) * 100) for r in view
            if r.get("LDCP") and r.get("CurrentPrice")]
    rows.sort(key=lambda x: -x[1])
    return [x for x in rows[:n] if x[1] > 0], [x for x in rows[::-1][:n] if x[1] < 0]


def _contributors(view: list[dict], n: int = 5):
    rows = sorted([r for r in view if r.get("NetIndexPoint") is not None], key=lambda r: r["NetIndexPoint"])
    pos = [r for r in reversed(rows[-n:]) if r["NetIndexPoint"] > 0]
    neg = [r for r in rows[:n] if r["NetIndexPoint"] < 0]
    data = pos + neg
    return [r["company_code"] for r in data], [r["NetIndexPoint"] for r in data]


def _breadth(act: list[dict]):
    up = sum(1 for r in act if (r.get("trading_change") or 0) > 0)
    dn = sum(1 for r in act if (r.get("trading_change") or 0) < 0)
    vol = sum(r.get("trading_vol") or 0 for r in act)
    return up, dn, len(act) - up - dn, vol


def _intraday(ctx):
    pts = (ctx.get("intraday") or {}).get("pts") or []
    return [p[0] for p in pts], [p[1] for p in pts], (ctx.get("intraday") or {}).get("prev")


def _mk_line(mk: dict, sym: str, name: str, fmt: str = "{:,.2f}"):
    d = mk.get(sym)
    return (name, fmt.format(d["last"]), d.get("pct")) if d else None


# ===================================================================== brief cards
def psx_card(cfg: dict, now: datetime, ctx: dict, kind: str) -> bytes:
    """Main PSX card: kind = open | midday | close."""
    idx, view, act = ctx.get("idx") or [], ctx.get("view") or [], ctx.get("act") or []
    labels, vals, prev = _intraday(ctx)
    k = _idx(idx, "KSE 100")
    hist = ctx.get("hist") or []
    c = Card(cfg, "psx")
    if kind == "open":
        series = [r["kse_index_close"] for r in hist][-22:]
        y = c.title_block("PSX", ctx.get("title2", "Market open"), f"{now:%a, %d %b %Y}", series=series)
        if k:
            chg = k["kse_index_change"]
            y = c.hero(y, "KSE-100 · last close", k["kse_index_close"], chg, chg / (k["kse_index_close"] - chg) * 100,
                       series, base=series[0] if series else None,
                       times=[hist[-len(series)]["date"].strftime("%d %b"), hist[-1]["date"].strftime("%d %b")]
                       if len(series) >= 2 else None)
        strip = [(lab, val, _col(p)) for lab, val, p in _index_tiles(idx)]
        if len(series) >= 2:
            m = (series[-1] / series[0] - 1) * 100
            strip.append((f"{len(series)}-day change", f"{_tri(m)} {m:+.1f}%", _col(m)))
        y = c.strip(y, strip)
        y = c.tiles(y, _rates_items(ctx.get("sbp") or {}, ctx.get("cpi"))[:6], "Rates · PKR · Macro", cols=3, tile_h=92)
    else:
        if kind == "midday":
            cur = view[0].get("CurrentIndex") if view else None
            pre = view[0].get("PreIndex") if view else None
            hl, lo = (max(vals), min(vals)) if vals else (None, None)
        else:
            cur = k["kse_index_close"] if k else None
            pre = cur - k["kse_index_change"] if k else None
            hl, lo = (k["kse_index_high"], k["kse_index_low"]) if k else (None, None)
        if not cur or not pre:
            raise ValueError("no KSE-100 level")
        base = prev or pre
        times = [labels[0], labels[-1]] if len(labels) >= 2 else None
        if len(vals) < 3 and len(hist) >= 5:  # no intraday path recorded: show the 1-month trend instead
            vals = [r["kse_index_close"] for r in hist][-22:]
            base = vals[0]
            times = [hist[-len(vals)]["date"].strftime("%d %b"), hist[-1]["date"].strftime("%d %b")]
        y = c.title_block("PSX", "Midday pulse" if kind == "midday" else "Market close",
                          f"{now:%a, %d %b %Y}" + (f" · {now:%H:%M} PKT" if kind == "midday" else ""),
                          series=vals, base=base)
        y = c.hero(y, "KSE-100" + (f" · at {now:%H:%M}" if kind == "midday" else ""), cur, cur - pre,
                   (cur / pre - 1) * 100, vals, base=base, times=times)
        up, dn, _, vol = _breadth(act) if act else (0, 0, 0, 0)
        strip = []
        if hl:
            strip += [("High", f"{hl:,.0f}", None), ("Low", f"{lo:,.0f}", None)]
        if act:
            strip += [("Volume", f"{vol / 1e6:,.0f} Mn", None), ("Up / Down", f"{up} / {dn}", _col(up - dn))]
        y = c.strip(y, strip)
        g, losers = _pct_movers(view)
        y = c.movers(y, g, losers)
    c.highlights(y, ctx.get("highlights") or [], h=c.H - 172 - 16 - y)
    c.footer()
    return c.png()


def deep_dive_card(cfg: dict, now: datetime, ctx: dict, title2: str) -> bytes:
    """Index movers, sector performance, flows/rates/indices."""
    view = ctx.get("view") or []
    c = Card(cfg, "psx")
    y = c.title_block("Market", title2, f"{now:%a, %d %b %Y}")
    lab, val = _contributors(view, 4)
    y = c.bars(y, "Biggest index movers (points)", lab, val, h=360)
    sec = sorted(ctx.get("sectors") or [], key=lambda s: -s[1])
    if sec:
        y = c.bars(y, "Sector performance (KSE-100, %)", [s[0] for s in sec], [s[1] for s in sec],
                   fmt="{:+.2f}%", h=min(330, 80 + len(sec) * 40))
    items = list(_index_tiles(ctx.get("idx") or []))
    f = (ctx.get("fipi") or {}).get("summary", {}).get("FIPI", {})
    if f.get("net") is not None:
        items.append(("Foreign flow (FIPI)", f"{f['net']:+.2f}m $", None))
    pool = {i[0]: i for i in _rates_items(ctx.get("sbp") or {}, ctx.get("cpi"))}
    items += [pool[n] for n in ("SBP policy rate", "USD/PKR interbank", "3M T-bill cut-off", "Real policy rate")
              if n in pool]
    mk = ctx.get("mk") or {}
    items += [x for x in (_mk_line(mk, "BZ=F", "Brent oil", "${:,.2f}"), _mk_line(mk, "GC=F", "Gold", "${:,.0f}")) if x]
    room = c.H - 172 - 16 - y
    rows = max(1, min(3, int((room - 72) // 104)))
    c.tiles(y, items[: rows * 3], "Indices · flows · rates", cols=3, tile_h=92)
    c.footer()
    return c.png()


US_SYMS = ["^GSPC", "^IXIC", "^DJI"]
MEGA = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "XOM"]
CUES = [("ES=F", "S&P futures", "{:,.0f}"), ("NQ=F", "Nasdaq futures", "{:,.0f}"), ("^N225", "Nikkei", "{:,.0f}"),
        ("^HSI", "Hang Seng", "{:,.0f}"), ("DX-Y.NYB", "Dollar index", "{:,.2f}"), ("^TNX", "US 10Y yield", "{:.2f}%"),
        ("^VIX", "VIX (fear)", "{:,.2f}"), ("BTC-USD", "Bitcoin", "${:,.0f}"), ("^BSESN", "Sensex", "{:,.0f}")]


def us_card(cfg: dict, now: datetime, mk: dict) -> bytes | None:
    if not all(s in mk for s in US_SYMS):
        return None
    c = Card(cfg, "us")
    when = datetime.strptime(mk["^GSPC"]["date"], "%Y-%m-%d")
    y = c.title_block("US market", "Close", f"{when:%a, %d %b %Y}", series=(mk["^GSPC"].get("hist") or [])[-22:])
    pw = (1000 - 32) / 3
    for i, (sym, name) in enumerate(zip(US_SYMS, ("S&P 500", "Nasdaq", "Dow Jones"))):
        d = mk[sym]
        x = c.L + i * (pw + 16)
        c.panel(x, y, pw, 230)
        c.label(x + 22, y + 36, name, 14)
        c.text(x + 22, y + 86, f"{d['last']:,.0f}", 30, CREAM, bold=True)
        ch = d["last"] - d["prev"]
        c.text(x + 22, y + 128, f"{_tri(ch)} {ch:+,.1f} ({d['pct']:+.2f}%)", 13.5, _col(ch), bold=True)
        c.text(x + pw - 20, y + 36, "1 MONTH", 9, c.t["muted"], ha="right")
        c.spark(x + 18, y + 150, pw - 36, 64, (d.get("hist") or [])[-22:], dot=True)
    y += 246
    mega = sorted([(s, mk[s]["pct"]) for s in MEGA if s in mk and mk[s].get("pct") is not None], key=lambda x: -x[1])
    if mega:
        y = c.movers(y, [m for m in mega[:5] if m[1] > 0], [m for m in mega[::-1][:5] if m[1] < 0],
                     "Mega-caps · top", "Mega-caps · bottom")
    cues = [x for x in (_mk_line(mk, s, n, f) for s, n, f in CUES) if x][:6]
    y = c.tiles(y, cues, "Global cues", cols=3, tile_h=88)
    hl = []
    moves = [(n, mk[s]["pct"]) for s, n in zip(US_SYMS, ("S&P 500", "Nasdaq", "Dow")) if mk[s].get("pct") is not None]
    if moves:
        verb = "higher" if sum(p for _, p in moves) > 0 else "lower"
        hl.append(f"Wall Street closed {verb}: " + " · ".join(f"{n} {p:+.2f}%" for n, p in moves))
    if mega:
        hl.append(f"Biggest mega-cap moves: {mega[0][0]} {mega[0][1]:+.1f}% · {mega[-1][0]} {mega[-1][1]:+.1f}%")
    if v := mk.get("^VIX"):
        mood = "calm" if v["last"] < 16 else ("elevated" if v["last"] < 25 else "high fear")
        hl.append(f"VIX at {v['last']:.1f} — volatility {mood}")
    if t := mk.get("^TNX"):
        hl.append(f"US 10-year yield {t['last']:.2f}% · higher US yields tend to pull money from frontier markets")
    c.highlights(y, hl, h=c.H - 172 - 16 - y)
    c.footer()
    return c.png()


COMMOD_MAIN = [("GC=F", "Gold", "USD/oz", "${:,.0f}"), ("BZ=F", "Brent oil", "USD/bbl", "${:,.2f}"),
               ("SI=F", "Silver", "USD/oz", "${:,.2f}"), ("NG=F", "Natural gas", "USD/MMBtu", "${:,.2f}")]
COMMOD_WHY = {
    "BZ=F": ("lifts E&P earnings (OGDC, PPL, MARI) but widens Pakistan's oil import bill",
             "eases Pakistan's import bill and inflation; a headwind for E&P earnings"),
    "GC=F": ("safe-haven demand; local gold prices usually follow", "risk appetite improving; local gold may ease"),
    "CT=F": ("raises input costs for textile mills", "cheaper input for textile mills"),
    "NG=F": ("a proxy for LNG costs — fertilizer and power", "eases LNG-linked costs for fertilizer and power"),
}


def commodity_card(cfg: dict, now: datetime, mk: dict) -> bytes | None:
    main = [(s, n, u, f) for s, n, u, f in COMMOD_MAIN if s in mk]
    if len(main) < 2:
        return None
    c = Card(cfg, "commodity")
    y = c.title_block("Commodity", "Update", f"{now:%a, %d %b %Y}", series=(mk[main[0][0]].get("hist") or [])[-22:])
    pw = (1000 - 16) / 2
    for i, (sym, name, unit, fmt) in enumerate(main[:4]):
        d = mk[sym]
        r, col = divmod(i, 2)
        x, yy = c.L + col * (pw + 16), y + r * 216
        c.panel(x, yy, pw, 200)
        c.label(x + 24, yy + 36, name, 15)
        c.text(x + 24, yy + 62, unit, 11, c.t["muted"])
        c.text(x + 24, yy + 112, fmt.format(d["last"]), 30, CREAM, bold=True)
        ch = d["last"] - d["prev"]
        c.text(x + 24, yy + 158, f"{_tri(ch)} {d['pct']:+.2f}%", 15, _col(ch), bold=True)
        c.text(x + pw - 24, yy + 36, "1 MONTH", 9, c.t["muted"], ha="right")
        c.spark(x + 230, yy + 70, pw - 254, 100, (d.get("hist") or [])[-22:])
    y += ((len(main[:4]) + 1) // 2) * 216
    rows = []
    for sym, (name, sector) in (cfg.get("commodities") or {}).items():
        if (d := mk.get(sym)) and d.get("pct") is not None:
            rows.append((name, d["last"], d["pct"], sector))
    if (d := mk.get("CL=F")) and d.get("pct") is not None:
        rows.insert(0, ("WTI oil", d["last"], d["pct"], "OMCs / refineries"))
    if rows:
        h = 76 + len(rows[:6]) * 42
        c.panel(c.L, y, 1000, h)
        c.label(c.L + 30, y + 38, "PSX-linked commodities", 14)
        c.text(c.R - 30, y + 38, "SECTOR LINK", 10.5, c.t["muted"], bold=True, ha="right")
        for i, (name, last, pct, sector) in enumerate(rows[:6]):
            yy = y + 84 + i * 42
            c.text(c.L + 30, yy, _tri(pct), 13, _col(pct))
            c.text(c.L + 60, yy, clean(name), 15, CREAM, bold=True)
            c.text(c.L + 420, yy, f"{last:,.2f}", 15, CREAM, ha="right")
            c.text(c.L + 540, yy, f"{pct:+.2f}%", 15, _col(pct), bold=True, ha="right")
            c.text(c.R - 30, yy, clean(sector)[:30], 13, c.t["muted"], ha="right")
        y += h + 16
    hl = []
    for sym, name, _, fmt in main:
        p = mk[sym].get("pct")
        if sym in COMMOD_WHY and p is not None and abs(p) >= 0.3:
            hl.append(f"{name} {p:+.1f}% — {COMMOD_WHY[sym][0 if p > 0 else 1]}")
    if (d := mk.get("CT=F")) and d.get("pct") is not None and abs(d["pct"]) >= 0.5:
        hl.append(f"Cotton {d['pct']:+.1f}% — {COMMOD_WHY['CT=F'][0 if d['pct'] > 0 else 1]}")
    if not hl:
        hl.append("Quiet session for commodities — no major moves overnight")
    c.highlights(y, hl, h=c.H - 172 - 16 - y)
    c.footer()
    return c.png()


def news_card(cfg: dict, now: datetime, items: list[dict], title2: str) -> bytes | None:
    """Top stories: impact chip, topic, headline, sectors in focus, source."""
    if not items:
        return None
    from .impact import sectors_line
    c = Card(cfg, "economy")
    y = c.title_block("Top stories", title2, f"{now:%a, %d %b %Y}")
    room = c.H - 172 - 16
    for it in items:
        imp = it.get("impact") or {"level": "Medium", "tone": "Neutral", "summary": "", "pos": [], "neg": [],
                                   "sectors": []}
        lines = _wrap(it["t"], 930, 17, bold=True)[:2]
        summ = _wrap(imp["summary"], 930, 12.5)[:2]
        h = 88 + len(lines) * 30 + len(summ) * 24 + 28
        if y + h > room:
            break
        c.panel(c.L, y, 1000, h)
        x = c.chip(c.L + 26, y + 34, f"{imp['level']} impact", LEVEL_COL[imp["level"]], 10.5)
        if imp["tone"] != "Neutral":
            x = c.chip(x, y + 34, imp["tone"], TONE_COL[imp["tone"]], 10.5)
        c.text(x + 6, y + 34, clean(it.get("topic_label", "")).upper(), 11, c.t["accent2"], bold=True)
        yy = y + 74
        for ln in lines:
            c.text(c.L + 26, yy, ln, 17, CREAM, bold=True)
            yy += 30
        for ln in summ:
            c.text(c.L + 26, yy, ln, 12.5, CREAM, alpha=0.85)
            yy += 24
        meta = " · ".join(x for x in (clean(it.get("s", "")), sectors_line(imp)) if x)
        c.text(c.L + 26, yy + 4, meta[:120], 11.5, c.t["muted"], bold=True)
        y += h + 12
    c.footer()
    return c.png()


def morning_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = [psx_card(cfg, now, ctx, "open")]
    mk = ctx.get("gmk") or {}
    for fn in (us_card, commodity_card):
        try:
            if png := fn(cfg, now, mk):
                out.append(png)
        except Exception:  # noqa: BLE001 — one card failing must not drop the album
            from .common import log
            log.exception("%s failed", fn.__name__)
    if png := news_card(cfg, now, ctx.get("headlines") or [], "Overnight"):
        out.append(png)
    return out


def midday_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = [psx_card(cfg, now, ctx, "midday"), deep_dive_card(cfg, now, ctx, "Movers & sectors")]
    if png := news_card(cfg, now, ctx.get("headlines") or [], "So far today"):
        out.append(png)
    return out


def close_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = [psx_card(cfg, now, ctx, "close"), deep_dive_card(cfg, now, ctx, "Movers & sectors")]
    if png := news_card(cfg, now, ctx.get("headlines") or [], "Today"):
        out.append(png)
    return out


def week_cards(cfg: dict, now: datetime, ctx: dict) -> list[bytes]:
    out = []
    hist = ctx.get("hist") or []
    series = [r["kse_index_close"] for r in hist][-22:]
    c = Card(cfg, "psx")
    y = c.title_block("Week in", "Review", ctx.get("week_label", f"{now:%d %b %Y}"), series=series)
    w = ctx.get("week")
    if w:
        y = c.hero(y, "KSE-100 · this week", w["close"], w["chg"], w["pct"], series, base=series[0] if series else None,
                   times=[hist[-len(series)]["date"].strftime("%d %b"), hist[-1]["date"].strftime("%d %b")]
                   if len(series) >= 2 else None)
    y = c.strip(y, ctx.get("week_strip") or [])
    y = c.movers(y, ctx.get("week_gain") or [], ctx.get("week_lose") or [], "Week's top gainers", "Week's top losers")
    c.highlights(y, ctx.get("week_highlights") or [], h=c.H - 172 - 16 - y)
    c.footer()
    out.append(c.png())

    c = Card(cfg, "gold")
    y = c.title_block("The week", "Ahead", ctx.get("ahead_label", ""))
    for title, rows in ctx.get("ahead") or []:
        if not rows:
            continue
        h = 72 + len(rows) * 40
        if y + h > c.H - 188:
            break
        c.panel(c.L, y, 1000, h)
        c.label(c.L + 30, y + 38, title, 14, c.t["accent2"])
        for i, (left, right) in enumerate(rows):
            yy = y + 82 + i * 40
            c.text(c.L + 30, yy, clean(left), 14.5, CREAM, bold=True)
            c.text(c.L + 250, yy, clean(right)[:70], 14, CREAM, alpha=0.9)
        y += h + 16
    c.footer()
    out.append(c.png())
    return out


# ===================================================================== alert cards
def alert_card(cfg: dict, spec: dict, when: datetime, tiles: list | None = None) -> bytes:
    """One important alert / news item.

    spec keys: theme, kicker, title, stats [(label, value, sign|None, sub)], rows_title,
    rows [(left, middle, right, sign|None)], why, sectors [..], source.
    """
    theme = spec.get("theme", "economy")
    t = THEMES.get(theme, THEMES["economy"])
    title_size = 34 if len(clean(spec.get("title", ""))) > 70 else 40
    tlines = _wrap(spec.get("title", ""), 1000, title_size, serif=True)[:4]
    why = _wrap(spec.get("why", ""), 900, 15)[:5]
    rows = spec.get("rows") or []
    mx = 200 if any(r[0] for r in rows) else 30  # rows without a left label use the full width
    rows_wrapped = [(r, _wrap(r[1], (760 if r[2] else 930) - mx, 14)[:2]) for r in rows[:14]]
    h = 175 + 60 + len(tlines) * title_size * 1.55 + 46
    h += 206 if spec.get("stats") else 0
    h += (80 + sum(18 + 30 * max(1, len(w)) for _, w in rows_wrapped)) if rows else 0
    imp = spec.get("impact")
    h += (76 + len(why) * 32) + 16 if why else 0
    h += 60 if imp else 0
    h += 86 * ((1 if imp["pos"] else 0) + (1 if imp["neg"] else 0)) if imp and (imp["pos"] or imp["neg"]) else \
        (86 if spec.get("sectors") or (imp and imp.get("sectors")) else 0)
    h += 150 if tiles else 0
    h += 50 + 190
    c = Card(cfg, theme, height=int(max(1080, h)))
    y = 190
    c.chip(c.L, y, spec.get("kicker", "Market update"), t["accent"] if theme != "economy" else "#5d7387")
    y += 54
    for ln in tlines:
        y += title_size * 1.55 * 0.5
        c.text(c.L, y, ln, title_size, CREAM, bold=True, serif=True)
        y += title_size * 1.55 * 0.5
    c.text(c.L + 2, y + 24, sp(f"{when:%a, %d %b %Y · %H:%M} PKT"), 12.5, CREAM, alpha=0.8)
    y += 60
    if imp:
        x = c.chip(c.L, y + 18, f"{imp['level']} impact", LEVEL_COL[imp["level"]], 12)
        if imp["tone"] != "Neutral":
            c.chip(x, y + 18, imp["tone"], TONE_COL[imp["tone"]], 12)
        y += 60
    if stats := spec.get("stats"):
        stats = stats[:3]
        pw = (1000 - 16 * (len(stats) - 1)) / len(stats)
        for i, st in enumerate(stats):
            lab, val, sign = st[0], st[1], st[2]
            sub = st[3] if len(st) > 3 else ""
            x = c.L + i * (pw + 16)
            c.panel(x, y, pw, 190, edge=t["accent"] if i == 0 else None, lw=1.6 if i == 0 else 1.3)
            c.text(x + 24, y + 40, sp(clean(lab)), 11.5, t["muted"], bold=True)
            size = 38 if len(val) <= 8 else (30 if len(val) <= 11 else 24)
            c.text(x + 22, y + 104, clean(val), size, CREAM if sign is None else _col(sign), bold=True)
            if sub:
                c.text(x + 24, y + 156, clean(sub), 12.5, _col(sign) if sign is not None else t["muted"], bold=True)
        y += 206
    if rows:
        ph = 80 + sum(18 + 30 * max(1, len(w)) for _, w in rows_wrapped) - 10
        c.panel(c.L, y, 1000, ph)
        c.label(c.L + 30, y + 38, spec.get("rows_title", "Details"), 13.5)
        yy = y + 84
        for (left, mid, right, sign), w in rows_wrapped:
            if left:
                c.text(c.L + 30, yy, clean(left)[:14], 15, t["accent2"], bold=True)
            for j, ln in enumerate(w or [""]):
                c.text(c.L + mx, yy + j * 30, ln, 14, CREAM)
            if right:
                c.text(c.R - 30, yy, clean(right), 15, _col(sign) if sign is not None else CREAM, bold=True, ha="right")
            yy += 18 + 30 * max(1, len(w))
        y += ph + 16
    if why:
        ph = 66 + len(why) * 32
        c.panel(c.L, y, 1000, ph)
        c._icon("search", c.L + 48, y + 38, 30)
        c.label(c.L + 80, y + 38, "What it means" if imp else "Why it matters", 13.5)
        for j, ln in enumerate(why):
            c.text(c.L + 30, y + 80 + j * 32, ln, 15, CREAM)
        y += ph + 16
    groups = []
    if imp and (imp["pos"] or imp["neg"]):
        groups = [(lab, secs, col) for lab, secs, col in (("Positive for", imp["pos"], "#2f7d4f"),
                                                          ("Pressure on", imp["neg"], "#a83a3a")) if secs]
    elif sectors := (spec.get("sectors") or (imp or {}).get("sectors")):
        groups = [("Sectors in focus", sectors, t["edge"])]
    for lab, secs, col in groups:
        c.label(c.L, y + 22, lab, 12, t["muted"])
        x = c.L
        for s in secs[:6]:
            x = c.chip(x, y + 62, s, col, 11.5)
        y += 86
    if tiles:
        c.label(c.L, y + 20, "Market right now", 12, t["muted"])
        cw = (1000 - 16 * (len(tiles[:3]) - 1)) / len(tiles[:3])
        for i, (lab, val, chg) in enumerate(tiles[:3]):
            x = c.L + i * (cw + 16)
            c.panel(x, y + 40, cw, 96)
            c.text(x + 20, y + 68, clean(lab), 11.5, t["muted"])
            c.text(x + 20, y + 106, val, 20, CREAM, bold=True)
            if chg is not None:
                c.text(x + cw - 16, y + 106, f"{_tri(chg)} {chg:+.2f}%", 12.5, _col(chg), bold=True, ha="right")
        y += 150
    if spec.get("source"):
        c.text(c.L, y + 20, "Source: " + clean(spec["source"])[:100], 12, t["muted"], italic=True)
    c.footer()
    return c.png()
