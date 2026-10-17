"""Stock of the Day as a brokerage-style research note (3 A4 pages: PNG for Telegram + one PDF).

Layout follows the usual sell-side company note: a firm header band, title block with a factual headline,
a left sidebar of key data and price performance, a main column of short written sections and the financial
summary, a second page of business tables, and a disclosures block. No rating or target price.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.textpath import TextPath  # noqa: E402

from .cards import clean  # strips HTML, emoji and long dashes

W, H, DPI = 1240, 1754, 150          # A4 portrait at 150 dpi
M = 70                                # page margin
SIDE_W = 330                          # sidebar width
GAP = 34
MAIN_X = M + SIDE_W + GAP
MAIN_W = W - M - MAIN_X
BOTTOM = H - 80                       # content must end above the footer rule
FONT = "DejaVu Sans"                  # ships with matplotlib, so pages look the same on every machine
INK = "#1b2a22"
GREEN = "#174f33"
GOLD = "#a8822d"
LIGHT = "#e9efe9"
ZEBRA = "#f6f8f5"
RULE = "#c9d3cb"
MUTED = "#5f6b63"
UP, DOWN = "#1a7a43", "#b33a2f"
PAD = 8
_PROPS: dict = {}


def _fp(bold: bool) -> FontProperties:
    if bold not in _PROPS:
        _PROPS[bold] = FontProperties(family=FONT, weight="bold" if bold else "normal")
    return _PROPS[bold]


def px(size: float) -> float:
    """Points to pixels."""
    return size * DPI / 72


def text_w(s: str, size: float, bold: bool = False) -> float:
    if not s:
        return 0.0
    return TextPath((0, 0), s, size=size, prop=_fp(bold)).get_extents().width * DPI / 72 * 1.04


def wrap(s: str, width: float, size: float, bold: bool = False) -> list[str]:
    words, lines, cur = clean(str(s)).split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if cur and text_w(t, size, bold) > width:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def fit_sentences(s: str, width: float, size: float, max_lines: int) -> str:
    """Trim text to whole sentences that fit in max_lines (never cut mid-sentence)."""
    s = clean(s)
    if len(wrap(s, width, size)) <= max_lines:
        return s
    parts = re.split(r"(?<=[.!?])\s+", s)
    out = ""
    for part in parts:
        t = f"{out} {part}".strip()
        if len(wrap(t, width, size)) > max_lines:
            break
        out = t
    return out or " ".join(wrap(s, width, size)[:max_lines])


class Page:
    def __init__(self, n: int, total: int, meta: dict):
        self.fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
        self.fig.patch.set_facecolor("white")
        self.n, self.total, self.meta = n, total, meta
        self._header()
        self._footer()

    # ---- primitives (pixel coordinates from the top-left; y of text = baseline)
    def text(self, x, y, s, size=9, color=INK, bold=False, ha="left", italic=False):
        self.fig.text(x / W, 1 - y / H, s, fontsize=size, color=color, ha=ha, va="baseline", family=FONT,
                      fontweight="bold" if bold else "normal", fontstyle="italic" if italic else "normal")

    def rect(self, x, y, w, h, color, z=1):
        self.fig.patches.append(Rectangle((x / W, 1 - (y + h) / H), w / W, h / H, transform=self.fig.transFigure,
                                          facecolor=color, edgecolor="none", zorder=z))

    def rule(self, x1, y, x2, color=RULE, lw=0.8):
        self.fig.add_artist(plt.Line2D([x1 / W, x2 / W], [1 - y / H, 1 - y / H], color=color, lw=lw))

    # ---- blocks: each takes the top y and returns the next free y
    def para(self, x, y, s, width, size=9, color=INK, bold=False, lead=1.45, max_lines=99, max_y=None) -> float:
        step = px(size) * lead
        lines = wrap(s, width, size, bold)[:max_lines]
        if max_y:
            lines = lines[:max(0, int((max_y - y) // step))]
        for i, ln in enumerate(lines):
            self.text(x, y + px(size) + i * step, ln, size, color, bold)
        return y + len(lines) * step + (step - px(size)) * 0.5

    def section(self, x, y, w, title) -> float:
        self.rect(x, y, w, 26, LIGHT)
        self.rect(x, y, 5, 26, GREEN, z=2)
        self.text(x + 14, y + 18, title.upper(), 8.5, GREEN, bold=True)
        return y + 26 + 10

    def bullets(self, x, y, items, width, size=9, gap=7, max_y=None) -> tuple[float, int]:
        step = px(size) * 1.42
        shown = 0
        for it in items:
            lines = wrap(it, width - 20, size)
            if max_y and y + len(lines) * step > max_y:
                break
            self.rect(x + 3, y + px(size) * 0.55, 6, 6, GOLD)
            for i, ln in enumerate(lines):
                self.text(x + 20, y + px(size) + i * step, ln, size)
            y += len(lines) * step + gap
            shown += 1
        return y, shown

    def table(self, x, y, widths, rows, header=None, size=8.5, align=None, max_y=None, colors=None,
              bold_first=False) -> float:
        """rows: lists of strings; every cell wraps inside its column."""
        align = align or (["left"] + ["right"] * (len(widths) - 1))
        step = px(size) * 1.38
        if header:
            hh = step + 12
            self.rect(x, y, sum(widths), hh, GREEN)
            cx = x
            for w, h_, al in zip(widths, header, align):
                self.text(cx + PAD if al == "left" else cx + w - PAD, y + hh / 2 + px(size - 0.5) * 0.36, h_,
                          size - 0.5, "white", bold=True, ha=al)
                cx += w
            y += hh
        for r_i, row in enumerate(rows):
            cells = [wrap(v, w - 2 * PAD, size) or [""] for w, v in zip(widths, row)]
            hgt = max(len(c) for c in cells) * step + 10
            if max_y and y + hgt > max_y:
                break
            if r_i % 2 == 1:
                self.rect(x, y, sum(widths), hgt, ZEBRA)
            cx = x
            for c_i, (w, cl, al) in enumerate(zip(widths, cells, align)):
                col = colors[r_i][c_i] if colors and colors[r_i] and colors[r_i][c_i] else INK
                for li, ln in enumerate(cl):
                    self.text(cx + PAD if al == "left" else cx + w - PAD, y + 5 + px(size) + li * step, ln, size,
                              col, bold=bold_first and c_i == 0, ha=al)
                cx += w
            self.rule(x, y + hgt, x + sum(widths), lw=0.5)
            y += hgt
        return y + 8

    def chart(self, x, y, w, h, series, bench=None, label="", blabel="KSE-100"):
        # legend as text above the plot so it never covers the lines
        self.rect(x, y + 4, 18, 3, GREEN)
        self.text(x + 24, y + 11, label, 7.5, INK)
        bx = x + 34 + text_w(label, 7.5)
        if bench is not None and len(bench) > 2:
            self.rect(bx, y + 4, 18, 3, GOLD)
            self.text(bx + 24, y + 11, blabel, 7.5, INK)
        top = y + 22
        ax = self.fig.add_axes([(x + 34) / W, 1 - (top + h) / H, (w - 34) / W, h / H])
        s = np.array(series, dtype=float)
        ax.plot(np.arange(len(s)), s / s[0] * 100, color=GREEN, lw=1.5)
        if bench is not None and len(bench) > 2:
            b = np.array(bench, dtype=float)
            ax.plot(np.linspace(0, len(s) - 1, len(b)), b / b[0] * 100, color=GOLD, lw=1.1)
        ax.axhline(100, color=RULE, lw=0.8)
        ax.tick_params(labelsize=6.5, colors=MUTED, length=2)
        ax.set_xticks([])
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(RULE)
        ax.set_xlim(0, len(s) - 1)
        return top + h + 12

    def bars(self, x, y, w, h, labels, values):
        """Simple column chart with value labels (losses in red)."""
        ax = self.fig.add_axes([(x + 10) / W, 1 - (y + h) / H, (w - 20) / W, h / H])
        cols = [GREEN if v >= 0 else DOWN for v in values]
        ax.bar(range(len(values)), values, color=cols, width=0.55)
        span = max(abs(v) for v in values) or 1
        for i, v in enumerate(values):
            ax.text(i, v + (span * 0.03 if v >= 0 else -span * 0.03), f"{v:,.1f}", ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=8, color=INK, family=FONT)
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(labels, fontsize=8, color=MUTED, family=FONT)
        ax.set_yticks([])
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.set_ylim(min(0, min(values) * 1.2), max(0, max(values)) * 1.15 + (0 if max(values) > 0 else span * 0.1))
        return y + h + 34

    def _header(self):
        m = self.meta
        self.rect(0, 0, W, 64, GREEN)
        self.text(M, 42, "PAKISTAN INVESTORS", 15, "white", bold=True)
        self.text(W - M, 40, "EQUITY RESEARCH  |  COMPANY PROFILE", 9, "#e8dcc0", bold=True, ha="right")
        self.rect(0, 64, W, 4, GOLD)
        self.text(M, 96, m["date_long"], 9, MUTED)
        self.text(W - M, 96, m["tag"], 9, MUTED, ha="right")
        self.rule(M, 108, W - M)

    def _footer(self):
        y = H - 58
        self.rule(M, y, W - M, color=GREEN, lw=1)
        self.text(M, y + 22, "PAKISTAN INVESTORS  |  For information only, not investment advice. "
                  f"Please read the disclosures on page {self.total}.", 7.5, MUTED)
        self.text(W - M, y + 22, f"Page {self.n} of {self.total}", 7.5, MUTED, ha="right")

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=DPI, facecolor="white")
        return buf.getvalue()


# ---------------------------------------------------------------- content helpers
def _bn(x) -> str:
    return "-" if x is None else f"{x * 1000 / 1e9:,.1f}"     # PSX figures are Rs thousands


def _pct(x, dec: int = 1) -> str:
    return "-" if x is None or x != x else f"{x * 100:+.{dec}f}%"


def _fy(y) -> str:
    return f"FY{str(y)[-2:]}" if y else ""


def headline(d: dict) -> str:
    """Factual one-line headline from the filed figures (no opinion)."""
    ann = d["profile"].get("annual") or {}
    ys = sorted(ann)
    if len(ys) >= 2:
        a, b = ann[ys[-1]].get("Profit after Taxation"), ann[ys[-2]].get("Profit after Taxation")
        if a is not None and b is not None:
            fy = _fy(ys[-1])
            if a > 0 and b > 0:
                ch = a / b - 1
                if abs(ch) < 0.005:
                    return f"{fy} profit flat at Rs {a * 1000 / 1e9:,.1f}bn"
                return f"{fy} profit {'up' if ch > 0 else 'down'} {abs(ch) * 100:.0f}% to Rs {a * 1000 / 1e9:,.1f}bn"
            if a > 0 >= b:
                return f"{fy}: back to profit at Rs {a * 1000 / 1e9:,.1f}bn"
            if a < 0:
                return f"{fy}: loss of Rs {abs(a) * 1000 / 1e9:,.1f}bn"
    return "Company profile"


def subhead(d: dict) -> str:
    """Second line: the latest quarter vs a year earlier, or the dividend."""
    lines = d.get("result_lines") or []
    q = [ln for ln in lines if not ln.startswith("Year")]
    if q and d.get("latest_label") and not d["latest_label"].startswith("Year"):
        return q[0].replace("profit after tax", "quarterly profit")
    divs = dict(d.get("divs") or [])
    ann = d["profile"].get("annual") or {}
    ys = sorted(ann)
    if ys and divs.get(int(ys[-1])):
        return f"Dividends for {_fy(ys[-1])}: {divs[int(ys[-1])]:.0f}% of face value"
    return ""


def fin_rows(d: dict) -> tuple[list[str], list[list[str]], list]:
    prof = d["profile"]
    ann = prof.get("annual") or {}
    ys = sorted(ann)[-4:]
    rat = prof.get("ratios") or {}
    top = next((k for k in ("Sales", "Total Income") if any(k in ann[y] for y in ys)), None)
    divs = dict(d.get("divs") or [])
    rows = []

    def growth(key):
        out = []
        for i, y in enumerate(ys):
            prev = ann[ys[i - 1]].get(key) if i else None
            cur = ann[y].get(key)
            out.append(_pct(cur / prev - 1, 0) if cur is not None and prev and prev > 0 else "-")
        return out

    def ratio(key):
        return [f"{rat[y][key]:.1f}%" if key in rat.get(y, {}) else "-" for y in ys]
    if top:
        rows.append(["Sales" if top == "Sales" else "Total income"] + [_bn(ann[y].get(top)) for y in ys])
        rows.append(["Sales growth" if top == "Sales" else "Income growth"] + growth(top))
    if any("Gross Profit Margin (%)" in rat.get(y, {}) for y in ys):
        rows.append(["Gross margin"] + ratio("Gross Profit Margin (%)"))
    rows.append(["Profit after tax"] + [_bn(ann[y].get("Profit after Taxation")) for y in ys])
    rows.append(["Profit growth"] + growth("Profit after Taxation"))
    if any("Net Profit Margin (%)" in rat.get(y, {}) for y in ys):
        rows.append(["Net margin"] + ratio("Net Profit Margin (%)"))
    rows.append(["EPS (Rs)"] + [f"{ann[y]['EPS']:,.2f}" if ann[y].get("EPS") is not None else "-" for y in ys])
    rows.append(["Dividend (% of FV)"] + [f"{divs[int(y)]:.0f}%" if divs.get(int(y)) else "-" for y in ys])
    colors = [[None] + [(UP if v.startswith("+") else DOWN) if r[0].endswith("growth") and v != "-" else None
                        for v in r[1:]] for r in rows]
    return ["Rs bn"] + [_fy(y) for y in ys], rows, colors


def _figure(v: str) -> str:
    """A balance sheet value must be a short figure, not a sentence."""
    v = clean(v or "").strip(" .")
    ok = re.fullmatch(r"(Rs\.?|PKR|USD|US\$)?\s*\(?-?[\d,]+(\.\d+)?\)?\s*(million|billion|thousand|mn|bn|m)?", v, re.I)
    return v if ok else ""


def _locations(rp: dict) -> list[list[str]]:
    locs = [[clean(x.get("place", "")), clean(x.get("what", ""))] for x in rp.get("locations") or []]
    specific = [r for r in locs if r[0].lower().strip(". ") != "pakistan"]
    return specific or locs


def _kse_ret(d: dict, n: int):
    k = [v for _, v in d.get("kse_series") or []]
    return k[-1] / k[-1 - n] - 1 if len(k) > n and k[-1 - n] else None


DISCLOSURE = ("This note is prepared by PAKISTAN INVESTORS from the company's filings with the Pakistan Stock Exchange, "
              "its latest annual report and exchange price data. It is for information and education only. It is not "
              "a recommendation or an offer to buy or sell any security, and it carries no rating or target price. "
              "Financial figures are as filed by the company with PSX (standalone); statements about the business, "
              "plans and risks are the company's own, taken from its annual report. Past performance does not "
              "indicate future results. Readers should check the official filings and seek independent advice "
              "before investing.")


# ---------------------------------------------------------------- the note
def _title(p: Page, d: dict, price_day: datetime) -> float:
    """Company name, price line, factual headline and logo. Returns the y where the body starts."""
    logo_w = 0
    if d.get("logo"):
        try:
            from PIL import Image
            im = Image.open(d["logo"]).convert("RGBA")
            iw, ih = im.size
            sc = min(250 / iw, 92 / ih)
            ax = p.fig.add_axes([(W - M - iw * sc) / W, 1 - (130 + ih * sc) / H, iw * sc / W, ih * sc / H])
            ax.imshow(np.asarray(im))
            ax.axis("off")
            logo_w = int(iw * sc) + 30
        except Exception:  # noqa: BLE001
            logo_w = 0
    title_w = W - 2 * M - logo_w
    tsize = 22
    while tsize > 15 and text_w(d["name"], tsize, True) > title_w:
        tsize -= 1
    p.text(M, 162, d["name"], tsize, GREEN, bold=True)
    p.text(M, 196, f"PSX: {d['sym']}   |   {d['sector']}   |   Rs {d['price']:,.2f} "
                   f"({price_day.day} {price_day:%b %Y} close)", 9.5, MUTED)
    y = p.para(M, 214, headline(d), title_w, size=14, color=INK, bold=True, lead=1.3, max_lines=1)
    sh = subhead(d)
    if sh:
        y = p.para(M, y, sh, title_w, size=10, color=MUTED, max_lines=1)
    y = max(y, 250)
    p.rule(M, y + 6, W - M, color=GREEN, lw=1.2)
    return y + 28


def _page_title(p: Page, d: dict, title: str) -> float:
    p.text(M, 152, f"{d['name']} ({d['sym']})", 15, GREEN, bold=True)
    p.text(W - M, 152, title, 10, MUTED, ha="right")
    p.rule(M, 166, W - M, color=GREEN, lw=1.2)
    return 190


def build(d: dict) -> tuple[list[bytes], bytes]:
    """Returns ([page PNGs], pdf bytes). Three pages: summary, financials, business and outlook
    (two pages when the annual report could not be read)."""
    rp = d.get("report") or {}
    prof = d["profile"]
    pub = datetime.strptime(str(d.get("published") or d["date"])[:10], "%Y-%m-%d")
    price_day = datetime.strptime(str(d["date"])[:10], "%Y-%m-%d")
    meta = {"date_long": f"{pub.day} {pub:%B %Y}",
            "tag": f"{d['sector']}  |  {'KMI-30  |  ' if d.get('kmi') else ''}PSX: {d['sym']}"}
    total = 3 if rp else 2
    pages = [Page(i, total, meta) for i in range(1, total + 1)]
    SP = 18  # space between blocks

    # ================= page 1: summary (sidebar of key data, main column of the story)
    p = pages[0]
    top = _title(p, d, price_day)
    x, sy = M, top
    ey = d.get("eps_year", "")
    sy = p.section(x, sy, SIDE_W, "Key data")
    shares = prof.get("shares")
    kd = [["Price (Rs)", f"{d['price']:,.2f}"],
          ["Market value (Rs bn)", f"{d['mcap'] / 1e9:,.1f}" if d.get("mcap") else "-"],
          ["Shares (m)", f"{shares / 1e6:,.1f}" if shares else "-"],
          ["Free float", f"{prof['free_float_pct']:.0f}%" if prof.get("free_float_pct") else "-"],
          ["12M high / low (Rs)", f"{d['hi']:,.0f} / {d['lo']:,.0f}"],
          ["Daily value (Rs m)", f"{d['adtv'] / 1e6:,.0f}" if d.get("adtv") else "-"],
          [f"EPS {_fy(ey)} (Rs)", f"{d['eps']:,.2f}" if d.get("eps") is not None else "-"],
          [f"P/E {_fy(ey)} (x)", f"{d['pe']:.1f}" if d.get("pe") else "n/m"],
          ["Size", f"{d['tier']} cap"],
          ["Year end", (prof.get("year_end") or "-").title()]]
    sy = p.table(x, sy, [215, SIDE_W - 215], kd, size=8.3)
    sy = p.section(x, sy + SP, SIDE_W, "Price performance")
    rets = d.get("returns") or {}
    perf = [["1M", _pct(rets.get("1 month")), _pct(_kse_ret(d, 21))],
            ["3M", _pct(rets.get("3 months")), _pct(_kse_ret(d, 63))],
            ["12M", _pct(rets.get("1 year")), _pct(d.get("kse_1y"))]]
    cols = [[None] + [UP if v.startswith("+") else DOWN if v.startswith("-") and v != "-" else None for v in r[1:]]
            for r in perf]
    sy = p.table(x, sy, [110, 110, SIDE_W - 220], perf, header=["Period", d["sym"], "KSE-100"], size=8.3, colors=cols)
    ser = [v for _, v in d.get("series") or []]
    if len(ser) > 20:
        sy = p.section(x, sy + SP, SIDE_W, "12 months, rebased to 100")
        sy = p.chart(x, sy, SIDE_W, 170, ser, [v for _, v in d.get("kse_series") or []], d["sym"])
    mg = [[r, n] for n, r in prof.get("people", [])[:3]]
    if prof.get("auditor"):
        mg.append(["Auditor", re.sub(r"M/s\.?|Chartered Accountants.*", "", prof["auditor"]).strip(" ,")])
    if mg and BOTTOM - sy > 120:
        sy = p.section(x, sy + SP, SIDE_W, "Management")
        sy = p.table(x, sy, [125, SIDE_W - 125], mg, size=8, align=["left", "left"], max_y=BOTTOM)

    x, my, mw = MAIN_X, top, MAIN_W
    my = p.section(x, my, mw, "Company overview")
    over = (rp.get("what_they_do") or {}).get("text") or prof.get("description", "")
    my = p.para(x, my, fit_sentences(over, mw, 9.5, 7 if rp else 12), mw, size=9.5)
    segs = rp.get("segments") or []
    if segs:
        my = p.section(x, my + SP, mw, "Business mix")
        my, _ = p.bullets(x, my, [f"{clean(s['name'])}: {clean(s['detail'])}" for s in segs[:6]], mw, size=9,
                          max_y=BOTTOM - 330)
    lines = list(d.get("result_lines") or [])
    if lines:
        lab = d.get("latest_label", "")
        lab = _fy(lab.split()[-1]) if lab.startswith("Year") else lab
        my = p.section(x, my + SP, mw, f"Latest results ({lab}, vs a year earlier)")
        lines = [re.sub(r"\bin 20(\d\d)\b", lambda m_: f"in FY{m_.group(1)}", ln.replace("Year 20", "FY"))
                 if ln.startswith("Year") else ln for ln in lines[:3]]
        my, _ = p.bullets(x, my, lines, mw, size=9, max_y=BOTTOM - 150)
    fut = [clean(f["text"]) for f in (rp.get("future") or [])]
    fut_left = fut
    if fut and BOTTOM - my > 160:
        my = p.section(x, my + SP, mw, "Outlook and projects (as stated by the company)")
        my, shown = p.bullets(x, my, fut, mw, size=9, max_y=BOTTOM)
        fut_left = fut[shown:]

    # ================= page 2: financials
    p = pages[1]
    full = W - 2 * M
    half = (full - GAP) / 2
    lx, rx = M, M + half + GAP
    y = _page_title(p, d, "Financials")
    head, rows, colors = fin_rows(d)
    n = len(head) - 1
    y = p.section(M, y, full, "Financial summary (standalone, as filed with PSX)")
    y = p.table(M, y, [full - n * 170] + [170] * n, rows, header=head, size=9, colors=colors)
    p.text(M, y + 8, "Rs bn unless stated. FV = face value. Source: company filings with PSX.", 7.2, MUTED)
    y += 26
    q = prof.get("quarterly") or {}
    if q:
        qs = list(q)[::-1]
        tk = next((k for k in ("Sales", "Total Income") if any(k in q[c] for c in qs)), None)
        qrows = []
        if tk:
            qrows.append(["Sales" if tk == "Sales" else "Total income"] + [_bn(q[c].get(tk)) for c in qs])
        qrows.append(["Profit after tax"] + [_bn(q[c].get("Profit after Taxation")) for c in qs])
        qrows.append(["EPS (Rs)"] + [f"{q[c]['EPS']:,.2f}" if q[c].get("EPS") is not None else "-" for c in qs])
        y = p.section(M, y + SP, full, "Recent quarters (Rs bn)")
        y = p.table(M, y, [full - len(qs) * 170] + [170] * len(qs), qrows, header=["Quarter"] + qs, size=9)
    y += SP
    # charts side by side: sales (or total income) and profit after tax
    ann = prof.get("annual") or {}
    ys = sorted(ann)[-4:]
    top_k = next((k for k in ("Sales", "Total Income") if all(ann[k_].get(k) is not None for k_ in ys)), None)
    charts = []
    if top_k and len(ys) >= 2:
        charts.append(("Sales, Rs bn" if top_k == "Sales" else "Total income, Rs bn", [ann[k][top_k] for k in ys]))
    pats = [ann[k].get("Profit after Taxation") for k in ys]
    if len(ys) >= 2 and all(v is not None for v in pats):
        charts.append(("Profit after tax, Rs bn", pats))
    cy = y
    for i, (lab, vals) in enumerate(charts):
        cx = lx if i == 0 else rx
        yy = p.section(cx, y, half, lab)
        cy = max(cy, p.bars(cx, yy, half, 200, [_fy(k) for k in ys], [v * 1000 / 1e9 for v in vals]))
    ly = ry = cy + (SP if charts else 0)
    facts = [clean(t) for _, t in d.get("facts") or []]
    if facts:
        ly = p.section(lx, ly, half, "Track record")
        ly, _ = p.bullets(lx, ly, facts, half, size=8.6, gap=6, max_y=BOTTOM)
    bs = rp.get("balance_sheet") or {}
    brow = [[lab, _figure(bs.get(k))] for k, lab in (("borrowings", "Borrowings"), ("cash", "Cash and bank"),
                                                     ("equity", "Total equity")) if _figure(bs.get(k))]
    if brow:
        ry = p.section(rx, ry, half, "Balance sheet (latest annual report)")
        ry = p.table(rx, ry, [half * 0.5, half * 0.5], brow, size=8.6) + SP
    ev = d.get("events") or []
    if ev:
        ry = p.section(rx, ry, half, "Coming up")
        ry, _ = p.bullets(rx, ry, ev[:4], half, size=8.6, gap=6, max_y=BOTTOM)

    if not rp:
        _disclosures(p, rp, max(ly, ry) + SP)
        return _finish(pages, f"{d['name']} ({d['sym']}) research note")

    # ================= page 3: business and outlook
    p = pages[2]
    y = _page_title(p, d, "Business, outlook and risks")
    disc_top = BOTTOM - 150
    ly = ry = y
    limit = disc_top - 330
    locs = _locations(rp)
    if locs:
        ly = p.section(lx, ly, half, "Operations")
        ly = p.table(lx, ly, [150, half - 150], locs[:7], size=8, align=["left", "left"], max_y=limit,
                     bold_first=True)
    cap = [clean(c["text"]) for c in (rp.get("capacity") or [])]
    if cap and limit - ly > 80:
        ly = p.section(lx, ly + SP, half, "Capacity")
        ly, _ = p.bullets(lx, ly, cap[:4], half, size=8.3, gap=5, max_y=limit)
    subs = rp.get("subsidiaries") or []
    if subs:
        has_stake = sum(1 for s_ in subs if s_.get("stake")) >= len(subs) / 2
        ry = p.section(rx, ry, half, "Group companies")
        if has_stake:
            srows = [[clean(s_["name"]), clean(s_.get("what") or "-"), clean(s_.get("stake") or "-")] for s_ in subs[:7]]
            ry = p.table(rx, ry, [190, half - 265, 75], srows, header=["Company", "Business", "Stake"], size=7.8,
                         align=["left", "left", "right"], max_y=limit)
        else:
            srows = [[clean(s_["name"]), clean(s_.get("what") or "-")] for s_ in subs[:7]]
            ry = p.table(rx, ry, [200, half - 200], srows, size=7.8, align=["left", "left"], max_y=limit,
                         bold_first=True)
    mil = rp.get("milestones") or []
    if mil and limit - ry > 80:
        ry = p.section(rx, ry + SP, half, "Milestones")
        ry = p.table(rx, ry, [70, half - 70], [[str(m_["year"]), clean(m_["event"])] for m_ in mil[:7]], size=7.8,
                     align=["left", "left"], max_y=limit, bold_first=True)
    y = max(ly, ry)
    if fut_left:
        y = p.section(M, y + SP, full, "Outlook and projects (continued)")
        y, _ = p.bullets(M, y, fut_left, full, size=8.8, gap=6, max_y=disc_top - 140)
    risks = [clean(r_["text"]) for r_ in (rp.get("risks") or [])]
    if risks:
        y = p.section(M, y + SP, full, "Key risks (as stated by the company)")
        y, _ = p.bullets(M, y, risks[:5], full, size=8.8, gap=6, max_y=disc_top - 10)
    _disclosures(p, rp, disc_top)
    return _finish(pages, f"{d['name']} ({d['sym']}) research note")


def _disclosures(p: Page, rp: dict, y: float) -> None:
    y = max(y, BOTTOM - 150)
    p.rule(M, y, W - M, color=GREEN, lw=1)
    p.text(M, y + 24, "DISCLOSURES", 8, GREEN, bold=True)
    src = "Sources: company filings with PSX" + (f"; annual report {rp['report']['period']}" if rp.get("report") else "") + \
          "; prices from the Pakistan Stock Exchange."
    text = DISCLOSURE if rp else (DISCLOSURE.replace(", its latest annual report and exchange price data", " and exchange price data")
                                  .replace("; statements about the business, plans and risks are the company's own, taken "
                                           "from its annual report", ""))
    p.para(M, y + 34, text + " " + src, W - 2 * M, size=7.2, color=MUTED, lead=1.4)


def _finish(pages: list[Page], title: str) -> tuple[list[bytes], bytes]:
    pngs = [pg.png() for pg in pages]
    buf = io.BytesIO()
    meta = {"Title": title, "Author": "PAKISTAN INVESTORS", "Creator": "PAKISTAN INVESTORS", "Producer": "PAKISTAN INVESTORS"}
    with PdfPages(buf, metadata=meta) as pdf:
        for pg in pages:
            pdf.savefig(pg.fig, facecolor="white")
    for pg in pages:
        plt.close(pg.fig)
    return pngs, buf.getvalue()
