"""Client profile for an investment plan: the questions, the risk scores and the Investment Policy Statement.

Industry practice (CFA Institute IPS; SECP suitability rules for AMCs; Wealthfront's method): risk is judged
on two separate scales, the ABILITY to take risk (capacity: horizon, income, age, money needed soon) and the
WILLINGNESS (tolerance: the loss one can live with, what one would do after a fall, experience, goal). When they
disagree the more conservative one sets the profile, because people overstate their tolerance in good times.
"""
from __future__ import annotations

import re

LEVELS = {1: "Conservative", 2: "Moderately conservative", 3: "Balanced", 4: "Growth", 5: "Aggressive"}
SECP_RISK = {1: "Low", 2: "Low to medium", 3: "Medium", 4: "High", 5: "Very high"}

TEMPLATE = ("/plan\n"
            "amount: 1000000\n"
            "monthly: 20000\n"
            "years: 5\n"
            "goal: growth\n"
            "max loss: 20\n"
            "if market falls 20%: hold\n"
            "experience: some\n"
            "income: stable\n"
            "age: 35\n"
            "cash needed in 1 year: 0\n"
            "shariah: no\n"
            "gold: yes")

HELP = ("Copy this, change the answers and send it back (lines you leave out get a cautious default):\n\n"
        + TEMPLATE + "\n\n"
        "goal: preserve / income / balanced / growth / aggressive / retirement\n"
        "max loss: the largest fall in a bad year you could live with, in %\n"
        "if market falls 20%: sell all / sell some / hold / buy more\n"
        "experience: none / some / experienced;  income: stable / variable / retired / none\n"
        "To get a switch plan for what you already own, add 'holdings:' and then one stock per line (FFC 500 @ 450, "
        "cash 50000); 'amount' is then only the NEW money you add (0 if none).")

_KEYS = [  # (field, words that may start the line)
    ("amount", ("amount", "invest", "capital", "lump", "money")),
    ("monthly", ("monthly", "sip", "add", "each month", "per month", "contribution")),
    ("years", ("years", "horizon", "time", "duration", "period")),
    ("goal", ("goal", "objective", "purpose", "aim")),
    ("max_loss", ("max loss", "loss", "drawdown", "maximum loss", "worst")),
    ("reaction", ("if market", "if the market", "market falls", "fall", "drop", "reaction")),
    ("experience", ("experience", "knowledge")),
    ("income", ("income", "job", "salary")),
    ("age", ("age",)),
    ("need", ("cash needed", "need", "liquidity", "withdraw")),
    ("shariah", ("shariah", "sharia", "islamic", "halal")),
    ("gold", ("gold",)),
    ("filer", ("filer", "atl", "tax")),
]


def _money(s: str) -> float | None:
    s = s.lower().replace(",", "").replace("rs", "").replace("pkr", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|thousand|lac|lakh|lakhs|m|mn|million|crore|cr)?", s)
    if not m:
        return None
    v = float(m.group(1))
    mult = {"k": 1e3, "thousand": 1e3, "lac": 1e5, "lakh": 1e5, "lakhs": 1e5, "m": 1e6, "mn": 1e6, "million": 1e6,
            "crore": 1e7, "cr": 1e7}.get(m.group(2) or "", 1)
    return v * mult


def _yes(s: str) -> bool:
    return bool(re.match(r"\s*(y|yes|haan|ha|true|1|only)", s.lower()))


def parse(text: str) -> tuple[dict, str, list[str]]:
    """'/plan' form -> (answers, holdings text, notes)."""
    text = re.sub(r"^/?\s*plan\b", "", text.strip(), flags=re.I)
    parts = re.split(r"\n\s*holdings?\s*:?\s*\n?", text, maxsplit=1, flags=re.I)
    form, hold = parts[0], (parts[1] if len(parts) > 1 else "")
    a: dict = {}
    for line in form.splitlines():
        if ":" not in line and "=" not in line:
            continue
        k, _, v = re.split(r"(:|=)", line, maxsplit=1)
        k, v = k.strip().lower(), v.strip()
        field = next((f for f, words in _KEYS if any(k.startswith(w) for w in words)), None)
        if not field or not v or field in a:
            continue
        if field in ("amount", "monthly", "need"):
            x = _money(v)
            if field == "need" and "%" in v and x is not None:
                a["need_pct"] = x / 100
            elif x is not None:
                a[field] = x
        elif field in ("years", "age"):
            m = re.search(r"\d+(?:\.\d+)?", v)
            if m:
                a[field] = float(m.group(0))
        elif field == "max_loss":
            m = re.search(r"\d+(?:\.\d+)?", v)
            if m:
                a[field] = float(m.group(0)) / 100
        elif field in ("shariah", "gold", "filer"):
            a[field] = _yes(v)
        else:
            a[field] = v.lower()
    return a, hold.strip(), []


def _goal(g: str) -> str:
    g = (g or "").lower()
    for key, words in (("preserve", ("preserv", "safe", "protect", "capital")), ("income", ("income", "dividend")),
                       ("aggressive", ("aggress", "maximum", "max")), ("growth", ("growth", "grow", "wealth")),
                       ("retirement", ("retire", "pension")), ("balanced", ("balanc", "moderate"))):
        if any(w in g for w in words):
            return key
    return "balanced"


def score(a: dict) -> dict:
    """Capacity and tolerance scores (1-5 each) and the resulting profile, with the reason for every part."""
    notes, cap, tol = [], {}, {}
    yrs = a.get("years")
    if yrs is None:
        notes.append("time horizon not given: 3 years assumed")
        yrs = 3.0
    cap["Time horizon"] = 1 if yrs < 1 else 2 if yrs < 3 else 3 if yrs < 5 else 4 if yrs < 10 else 5
    inc = a.get("income", "")
    if inc:
        cap["Income"] = 1 if "none" in inc or "no " in inc else 2 if "retire" in inc else 3 if "var" in inc or "business" in inc else 4
    if a.get("age"):
        age = a["age"]
        cap["Age"] = 5 if age < 30 else 4 if age < 45 else 3 if age < 55 else 2 if age < 65 else 1
    amount = a.get("amount") or 0
    need = a.get("need", 0.0) if "need_pct" not in a else a["need_pct"] * amount
    share = need / amount if amount else 0
    cap["Money needed within a year"] = 5 if share <= 0 else 4 if share < 0.10 else 3 if share < 0.25 else 2 if share < 0.5 else 1
    ml = a.get("max_loss")
    if ml is None:
        notes.append("largest acceptable loss not given: 10% assumed")
        ml = 0.10
    tol["Loss you could live with"] = 1 if ml <= 0.05 else 2 if ml <= 0.10 else 3 if ml <= 0.20 else 4 if ml <= 0.30 else 5
    re_ = a.get("reaction", "")
    if re_:
        tol["Reaction to a 20% fall"] = (1 if "all" in re_ else 2 if "some" in re_ or "part" in re_ else 5 if "buy" in re_
                                         else 3)
    else:
        notes.append("reaction to a fall not given: 'sell some' assumed")
        tol["Reaction to a 20% fall"] = 2
    ex = a.get("experience", "")
    tol["Experience"] = 2 if not ex or "none" in ex or "no" == ex[:2] else 4 if "exper" in ex or "profes" in ex else 3
    goal = _goal(a.get("goal", ""))
    tol["Goal"] = {"preserve": 1, "income": 2, "retirement": 3, "balanced": 3, "growth": 4, "aggressive": 5}[goal]
    c = sum(cap.values()) / len(cap)
    t = sum(tol.values()) / len(tol)
    level = max(1, min(5, int(min(c, t) + 0.5)))
    if yrs < 1:
        level = 1
    return {"capacity": c, "tolerance": t, "cap_parts": cap, "tol_parts": tol, "level": level, "name": LEVELS[level],
            "secp": SECP_RISK[level], "goal": goal, "years": yrs, "max_loss": ml, "need": need, "notes": notes,
            "limited_by": "capacity" if c < t - 0.25 else "tolerance" if t < c - 0.25 else "both"}
