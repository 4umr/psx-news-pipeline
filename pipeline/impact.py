"""Impact engine: what a headline means for the market, in one clean line.

Rule-based (free, no AI service): the news topic + the direction words in the
headline (cut vs hike, rupee up vs down, oil up vs down, ...) select a short,
factual summary and the sectors that typically benefit or come under pressure.
These are general, well-known market relationships, not forecasts or advice.

assess() returns:
  level    High / Medium / Low   (how much it can move PSX)
  tone     Positive / Negative / Mixed / Neutral   (for the broad market)
  summary  one plain sentence
  pos/neg  sectors that typically benefit / come under pressure
"""
from __future__ import annotations

import re

_UP = r"\b(rise[sn]?|rose|up|gains?|gained|jumps?|jumped|surges?|surged|soars?|climbs?|climbed|higher|increases?|increased|" \
      r"hikes?|hiked|raises?|raised|record high|widens?|accelerat\w*|spikes?|rall(y|ies))\b"
_DOWN = r"\b(falls?|fell|drops?|dropped|declines?|declined|down|plunges?|plunged|slumps?|lower|lowers|lowered|decreases?|" \
        r"decreased|cuts?|slash\w*|reduces?|reduced|eases?|eased|narrows?|slows?|slowed|tumbles?|sheds?|slides?|dips?)\b"


def _has(rx: str, text: str) -> bool:
    return re.search(rx, text, re.I) is not None


def _dir(text: str) -> int:
    """+1 up, -1 down, 0 unclear (both or neither)."""
    u, d = _has(_UP, text), _has(_DOWN, text)
    return 1 if u and not d else (-1 if d and not u else 0)


def _rule(topic: str, t: str):
    """(tone, summary, positive sectors, negative sectors) or None for the topic default."""
    if topic == "monetary_policy":
        if _has(r"\b(hold|holds|held|unchanged|keeps?|kept|maintain\w*|status quo|pause\w*)\b", t):
            if _has(r"expect|likely|poll|forecast|may|could|seen|analysts?", t):
                return ("Neutral", "Market expects the policy rate to stay unchanged; the actual decision and "
                        "guidance will set the tone for banks and rate-sensitive stocks.", [], [])
            return ("Neutral", "Policy rate unchanged: borrowing costs stay the same; focus shifts to SBP's "
                    "guidance for the next meeting.", [], [], True)
        if _has(r"\b(cuts?|reduc\w*|lowers?|lowered|slash\w*|eases?|easing)\b", t):
            return ("Positive", "A lower policy rate reduces borrowing costs and usually supports equity "
                    "valuations; banks' interest margins can narrow.",
                    ["Cement", "Autos", "Steel", "Textile"], ["Banks"])
        if _has(r"\b(hikes?|hiked|raises?|raised|increase\w*|tighten\w*)\b", t):
            return ("Negative", "A higher policy rate raises borrowing costs and weighs on equity valuations; "
                    "banks' interest margins benefit.", ["Banks"], ["Cement", "Autos", "Steel"])
        return None
    if topic == "inflation":
        if _has(r"\b(eases?|eased|slows?|slowed|cools?|falls?|fell|declines?|declined|lower|drops?|dropped|down)\b", t):
            return ("Positive", "Slower inflation gives SBP more room to cut rates, which is usually supportive "
                    "for equities.", ["Cement", "Autos", "Consumer"], [])
        if _has(r"\b(rises?|rose|accelerat\w*|jumps?|surges?|higher|up|increases?|climbs?|spikes?)\b", t):
            return ("Negative", "Faster inflation reduces room for rate cuts and squeezes household spending.",
                    [], ["Consumer", "Autos", "Cement"])
        return None
    if topic == "fuel_prices":
        power = _has(r"electricity|power tariff|tariff|gas price|\blng\b", t)
        d = _dir(t)
        if _has(r"costlier|expensive|dearer", t) and not _has(r"cheaper|relief|lower", t):
            d = 1
        elif _has(r"cheaper|relief", t) and not _has(r"costlier|expensive", t):
            d = -1
        if power and d > 0:
            return ("Negative", "Higher power and gas costs feed into inflation and squeeze energy-intensive "
                    "industries.", [], ["Textile", "Steel", "Cement", "Fertilizer"])
        if power and d < 0:
            return ("Positive", "Cheaper power and gas ease inflation and costs for energy-intensive industries.",
                    ["Textile", "Steel", "Cement"], [])
        if d < 0:
            return ("Positive", "Cheaper fuel eases transport costs and CPI inflation; OMCs and refineries may "
                    "book inventory losses.", ["Autos", "Consumer"], ["OMCs", "Refineries"])
        if d > 0:
            return ("Negative", "Costlier fuel lifts transport costs and CPI inflation; OMCs and refineries can "
                    "book inventory gains.", ["OMCs", "Refineries"], ["Autos", "Consumer"])
        return None
    if topic in ("rupee", "rupee_shock"):
        strong = _has(r"rupee\W+(\w+\W+){0,2}?(gains?|gained|appreciat\w*|strengthens?|firms?|rises?|rose|recovers?|up)\b"
                      r"|dollar\W+(\w+\W+){0,2}?(falls?|fell|drops?|dropped|declines?|eases?|down|slips?)", t)
        weak = _has(r"rupee\W+(\w+\W+){0,2}?(falls?|fell|drops?|dropped|depreciat\w*|weakens?|slides?|slips?|"
                    r"declines?|plunges?|down|record low)|dollar\W+(\w+\W+){0,2}?(rises?|rose|gains?|climbs?|jumps?|up|"
                    r"surges?)|devalu", t)
        if strong and not weak:
            return ("Positive", "A stronger rupee lowers import costs and inflation pressure; exporters earn "
                    "fewer rupees per dollar.", ["Autos", "OMCs", "Pharma"], ["Textile", "IT"])
        if weak and not strong:
            return ("Negative", "A weaker rupee raises import costs and inflation; exporters and dollar-linked "
                    "earners benefit.", ["Textile", "IT", "E&P"], ["Autos", "OMCs", "Pharma"])
        if _has(r"\b(holds?|steady|stable|unchanged|flat)\b", t):
            return ("Neutral", "Stable rupee: no change in import costs or exporters' earnings.", [], [])
        return None
    if topic == "reserves_external":
        if _has(r"\breserves?\b", t) and not _has(r"current account|deficit|surplus", t) and (d := _dir(t)):
            return (("Positive", "Higher reserves support the rupee and foreign investor confidence.",
                     ["Banks", "Broad market"], []) if d > 0 else
                    ("Negative", "Falling reserves put pressure on the rupee and external financing.",
                     [], ["Broad market", "Autos", "OMCs"]))
        if _has(r"current account\W+(\w+\W+){0,3}?surplus|reserves\W+(\w+\W+){0,4}?(rise|rises|rose|up|gain|gains|"
                r"increase|increases|increased|climb|jump|swell|improve)", t):
            return ("Positive", "Higher reserves support the rupee and foreign investor confidence.",
                    ["Banks", "Broad market"], [])
        if _has(r"current account\W+(\w+\W+){0,3}?deficit|reserves\W+(\w+\W+){0,4}?(fall|falls|fell|drop|drops|"
                r"dropped|decline|declines|down|decrease|slide|shrink|dip)", t):
            return ("Negative", "Falling reserves or a wider deficit put pressure on the rupee and external "
                    "financing.", [], ["Broad market", "Autos", "OMCs"])
        return None
    if topic == "imf":
        if _has(r"delay|stall|suspend|miss|halt|uncertain|rejects?|fail|tough|concern|warn", t):
            return ("Negative", "IMF delays or tougher conditions raise financing and tax risks for Pakistan.",
                    [], ["Banks", "Broad market"])
        if _has(r"staff.level|approv\w*|disburs\w*|releases?|completes?|agreement|receives?|tranche|\bdeal\b|"
                r"clears?|\bnod\b|secures?|unlocks?|bailout", t):
            return ("Positive", "IMF progress unlocks financing and supports reserves, the rupee and market "
                    "sentiment.", ["Banks", "Broad market"], [])
        return None
    if topic == "global_oil":
        d = _dir(t)
        if d > 0:
            return ("Mixed", "Higher oil lifts E&P earnings but widens Pakistan's import bill and inflation.",
                    ["E&P", "Refineries"], ["Power", "Autos"])
        if d < 0:
            return ("Mixed", "Cheaper oil eases Pakistan's import bill and inflation; E&P earnings soften.",
                    ["Autos", "Power"], ["E&P"])
        return None
    if topic == "govt_securities":
        if _has(r"yields?\W+(\w+\W+){0,3}?(fall|falls|fell|drop|drops|dropped|declin\w*|down|lower|eas\w*)|"
                r"(lower|falling|declining) (yields?|cut.?offs?)|cut.?offs?\W+(\w+\W+){0,3}?(fall|drop|declin\w*|lower)", t):
            return ("Positive", "Falling auction yields signal expected rate cuts, typically supportive for "
                    "equities; banks' investment income softens.", ["Cement", "Autos"], ["Banks"])
        if _has(r"yields?\W+(\w+\W+){0,3}?(rise|rises|rose|up|jump|climb|higher|increas\w*)|"
                r"(higher|rising) (yields?|cut.?offs?)", t):
            return ("Negative", "Rising auction yields signal tighter money ahead; banks benefit, rate-sensitive "
                    "sectors face pressure.", ["Banks"], ["Cement", "Autos"])
        return ("Neutral", "Auction results show where the market sees interest rates heading.", [], [])
    if topic == "sovereign_rating":
        if _has(r"upgrad\w*|positive outlook|outlook to positive|raises?", t):
            return ("Positive", "A better credit rating lowers Pakistan's borrowing costs and attracts foreign "
                    "flows.", ["Banks", "Broad market"], [])
        if _has(r"downgrad\w*|negative outlook|outlook to negative|cuts?|lowers?", t):
            return ("Negative", "A weaker credit rating raises borrowing costs and can deter foreign flows.",
                    [], ["Banks", "Broad market"])
        if _has(r"affirm\w*", t):
            return ("Neutral", "Rating affirmed: no change in Pakistan's credit standing.", [], [], True)
        return None
    if topic in ("market_shock", "psx_market"):
        d = _dir(t)
        if d > 0:
            return ("Positive", "Broad buying at PSX; check what is driving it before reacting.", ["Broad market"], [])
        if d < 0:
            return ("Negative", "Broad selling at PSX; check the trigger before reacting.", [], ["Broad market"])
        return None
    if topic in ("geo_escalation", "geopolitics", "global_shock", "middle_east"):
        if _has(r"ceasefire|truce|peace|de-?escalat\w*|talks|deal|agreement|calm", t):
            return ("Positive", "Signs of de-escalation usually improve risk appetite and can ease oil prices.",
                    ["Broad market"], [])
        return ("Negative", "Escalation can trigger risk-off selling at PSX and push oil prices higher.",
                ["E&P"] if topic == "middle_east" else [], ["Broad market"])
    if topic in ("fed_decision", "global_rates"):
        if _has(r"\b(cuts?|lowers?|easing|dovish)\b", t):
            return ("Positive", "A lower US rate path weakens the dollar and usually helps flows into frontier "
                    "markets like Pakistan.", ["Broad market"], [])
        if _has(r"\b(hikes?|raises?|hawkish|tighten\w*)\b", t):
            return ("Negative", "A higher US rate path strengthens the dollar and can pull money out of "
                    "frontier markets.", [], ["Broad market"])
        return None
    if topic == "external_trade":
        if _has(r"deficit\W+(\w+\W+){0,3}?(widen\w*|rise|rises|rose|up|jump\w*|increas\w*)|imports?\W+(\w+\W+){0,2}?"
                r"(rise|rises|rose|up|jump\w*|surge\w*|increas\w*)", t):
            return ("Negative", "A wider trade gap adds pressure on reserves and the rupee.", [], ["Broad market"])
        if _has(r"(exports?|remittances?|fdi|inflows?)\W+(\w+\W+){0,3}?(rise|rises|rose|up|jump\w*|surge\w*|"
                r"increas\w*|grow\w*|record)|deficit\W+(\w+\W+){0,3}?(narrow\w*|shrink\w*|fall\w*|declin\w*)", t):
            return ("Positive", "Stronger dollar inflows support reserves and the rupee.", ["Banks", "Textile"], [])
        return None
    if topic == "bilateral_finance":
        if _has(r"deposit|rollover|roll over|loan|financing|facility|disburs\w*|deferred|lifeline|bailout", t):
            return ("Positive", "Fresh financing or deposits support reserves and reduce external funding risk.",
                    ["Banks", "Broad market"], [])
        if _has(r"invest\w*", t):
            return ("Positive", "Investment pledges can support growth and reserves if they materialise.",
                    ["Broad market"], [])
        return None
    if topic in ("budget_tax", "tax_admin"):
        if _has(r"collection|revenue|shortfall|target", t):
            return ("Neutral", "Tax revenue performance matters for IMF targets and the risk of new tax measures.",
                    [], [])
        if _has(r"relief|exempt\w*|abolish\w*|withdraw\w*|cuts?|reduc\w*|lower\w*", t):
            return ("Positive", "Tax relief can lift after-tax earnings for the sectors involved.", [], [])
        if _has(r"new tax|super tax|hikes?|raises?|increase\w*|impose\w*|levy|additional tax|withholding", t):
            return ("Negative", "New or higher taxes can cut after-tax earnings and returns.", [], [])
        return None
    if topic == "energy_sector":
        if _has(r"(circular debt)\W+(\w+\W+){0,3}?(rise|rises|rose|up|swell\w*|increas\w*|jump\w*)", t):
            return ("Negative", "Rising circular debt squeezes cash flows across the energy chain.",
                    [], ["E&P", "Power", "OMCs"])
        if _has(r"settle\w*|clear\w*|payment|release\w*|resolv\w*|retir\w*", t):
            return ("Positive", "Payments against circular debt improve cash flows for energy companies.",
                    ["E&P", "Power", "OMCs"], [])
        return None
    if topic == "psx_corporate":
        if _has(r"loss|profit\W+(\w+\W+){0,2}?(falls?|fell|drops?|declin\w*|down|plunge\w*)", t):
            return ("Negative", "Weaker company results can weigh on the stock and its sector peers.", [], [])
        if _has(r"dividend|bonus|payout|profit\W+(\w+\W+){0,2}?(rises?|rose|jumps?|up|surge\w*|soar\w*|grows?|record)|"
                r"buy.?back|acqui\w*|merger", t):
            return ("Positive", "Strong results, payouts or deals can lift the stock and its sector peers.", [], [])
        return None
    return None


# Topics whose events move PSX directly (can be High impact)
_CORE = {"monetary_policy", "imf", "govt_securities", "inflation", "reserves_external", "fuel_prices",
         "budget_tax", "market_shock", "geo_escalation", "sovereign_rating", "rupee_shock", "bilateral_finance"}


def assess(title: str, topic: str, score: int, cfg: dict | None = None, summary: str = "") -> dict:
    meta = ((cfg or {}).get("topic_meta") or {}).get(topic, {}) or {}
    topics = {t["name"]: t for t in (cfg or {}).get("topics", [])}
    rule = _rule(topic, title) or (_rule(topic, f"{title}. {summary}") if summary else None)
    event = False
    if rule:
        tone, text, pos, neg = rule[:4]
        event = len(rule) > 4 and rule[4]
    else:
        tone, text = "Neutral", (topics.get(topic, {}).get("why") or "Worth knowing for PSX investors.")
        pos, neg = [], []
    level = "High" if score >= 9 else ("Medium" if score >= 7 else "Low")
    if level == "High" and (topic not in _CORE or (tone == "Neutral" and not event
                                                   and topic not in ("market_shock", "geo_escalation"))):
        level = "Medium"  # commentary / expectations, or a global story: not a direct PSX mover
    if topic in topics and not topics[topic].get("pk") and score < 10:
        level = "Medium" if level == "High" else level
    sectors = meta.get("sectors") or []
    return {"level": level, "tone": tone, "summary": text.replace("—", ":"),
            "pos": pos, "neg": neg, "sectors": [s for s in sectors if s not in pos + neg][:4] if not (pos or neg) else []}


def sectors_line(imp: dict) -> str:
    parts = []
    if imp["pos"]:
        parts.append("Positive for " + ", ".join(imp["pos"]))
    if imp["neg"]:
        parts.append("Pressure on " + ", ".join(imp["neg"]))
    if not parts and imp["sectors"]:
        parts.append("Sectors in focus: " + ", ".join(imp["sectors"]))
    return " · ".join(parts)
