"""Reading a portfolio the owner sends: free text from the bot chat, or a CSV / Excel export from a broker.

Accepted lines (one holding per line, or separated by ';'):
  FFC 500            500 shares
  FFC 500 @ 520      500 shares bought at Rs 520 (also 'FFC 500 520' or 'FFC 500 at 520')
  FFC 20%            a weight (then a notional Rs 1,000,000 portfolio is used for rupee figures)
  FFC Rs 250000      a rupee amount
  cash 150000        cash (or 'cash 10%')
Mixing shares and weights in one portfolio is refused (the result would be meaningless).
"""
from __future__ import annotations

import re

NOTIONAL = 1_000_000.0

_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_LINE = re.compile(rf"^(?P<sym>[A-Za-z][A-Za-z0-9]{{1,9}})[\s:=-]+(?:(?P<rs>rs\.?|pkr)\s*)?{_NUM}\s*(?P<unit>%|k|m|mn)?"
                   rf"(?:\s*(?:@|at|cost|avg|rate)?\s*(?:rs\.?\s*)?{_NUM})?\s*$", re.I)


def _f(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


def parse(text: str) -> tuple[list[dict], float, str, list[str]]:
    """-> (holdings [{sym, qty|weight|value, cost}], cash, mode 'shares'|'weights'|'values', problems)."""
    text = re.sub(r"^/?\s*(portfolio|pf|folio)\b", "", text.strip(), flags=re.I)
    parts = [p.strip() for p in re.split(r"[\n;]+|,(?=\s*[A-Za-z])", text) if p.strip()]
    out: dict[str, dict] = {}
    cash, probs, modes = 0.0, [], set()
    for p in parts:
        m = _LINE.match(p)
        if not m:
            probs.append(f"could not read '{p[:40]}'")
            continue
        sym, rs, a, unit, cost = m.group("sym").upper(), m.group("rs"), _f(m.group(3)), (m.group("unit") or "").lower(), _f(m.group(5))
        if unit in ("k",):
            a *= 1e3
        elif unit in ("m", "mn"):
            a *= 1e6
        if sym == "CASH":
            if unit == "%":
                modes.add("weights")
                out["CASH%"] = {"sym": "CASH", "weight": a / 100}
            else:
                cash += a
            continue
        h = out.setdefault(sym, {"sym": sym})
        if unit == "%":
            modes.add("weights")
            h["weight"] = h.get("weight", 0) + a / 100
        elif rs:
            modes.add("values")
            h["value"] = h.get("value", 0) + a
        else:
            modes.add("shares")
            if cost and h.get("qty") and h.get("cost"):   # two lots of the same stock: average cost
                h["cost"] = (h["cost"] * h["qty"] + cost * a) / (h["qty"] + a)
            elif cost:
                h["cost"] = cost
            h["qty"] = h.get("qty", 0) + a
    hold = [h for k, h in out.items() if k != "CASH%"]
    if len(modes) > 1:
        probs.append("mix of share counts, rupee amounts and percentages: please use one kind for all holdings")
    mode = next(iter(modes)) if len(modes) == 1 else ("shares" if not modes else "mixed")
    if "CASH%" in out:
        cash = out["CASH%"]["weight"]  # a weight: turned into rupees by the caller
    return hold, cash, mode, probs


_SYM_H = ("symbol", "scrip", "ticker", "security", "stock", "code", "company")
_QTY_H = ("quantity", "qty", "shares", "holding", "volume", "balance", "units", "no. of shares")
_COST_H = ("avg", "average", "cost", "buy rate", "purchase", "rate")


def from_table(rows: list[list]) -> str:
    """Broker export (CSV / Excel rows, header row first somewhere in the top 10) -> the text format above."""
    def low(x):
        return str(x or "").strip().lower()

    head_i, cols = None, {}
    for i, row in enumerate(rows[:10]):
        cells = [low(c) for c in row]
        s = next((j for j, c in enumerate(cells) if any(c.startswith(h) or c == h for h in _SYM_H)), None)
        q = next((j for j, c in enumerate(cells) if any(h in c for h in _QTY_H)), None)
        if s is not None and q is not None:
            head_i = i
            cols = {"sym": s, "qty": q,
                    "cost": next((j for j, c in enumerate(cells) if any(h in c for h in _COST_H) and j not in (s, q)), None)}
            break
    if head_i is None:  # no header: assume symbol, quantity[, cost]
        head_i, cols = -1, {"sym": 0, "qty": 1, "cost": 2}
    lines = []
    for row in rows[head_i + 1:]:
        try:
            sym = str(row[cols["sym"]] or "").strip().upper().split()[0]
            qty = float(str(row[cols["qty"]]).replace(",", ""))
        except (IndexError, ValueError, TypeError):
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", sym) or qty <= 0:
            continue
        cost = None
        if cols.get("cost") is not None:
            try:
                cost = float(str(row[cols["cost"]]).replace(",", ""))
            except (IndexError, ValueError, TypeError):
                cost = None
        lines.append(f"{sym} {qty:g}" + (f" @ {cost:g}" if cost and cost > 0 else ""))
    return "\n".join(lines)


def from_file(name: str, data: bytes) -> str:
    """CSV, TXT or XLSX bytes -> holdings text."""
    n = name.lower()
    if n.endswith((".xlsx", ".xlsm")):
        import io

        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return from_table([list(r) for r in ws.iter_rows(values_only=True)])
    txt = data.decode("utf-8-sig", errors="ignore")
    if n.endswith(".csv") or txt.count(",") > txt.count("\n"):
        import csv
        import io
        return from_table(list(csv.reader(io.StringIO(txt))))
    return txt
