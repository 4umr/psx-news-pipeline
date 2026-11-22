"""Portfolio review on request (private): the owner sends his holdings to the bot (text, or a CSV / Excel export)
-> commands.py encrypts them and starts lab.yml -> `python -m pipeline portfolio` builds an 8-page report
(engine.py: performance, risk, VaR, stress tests, Monte Carlo, fundamentals, rule book; report.py: the pages)
and sends it to the owner's private chat only.

Privacy: the repository and its Actions logs are public. The holdings travel encrypted (openssl AES-256, key =
the bot token, the same scheme as the Lab's backup branch), are kept only inside the encrypted Lab data, and the
log lines written during a review have every holding's symbol masked.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess

from ..common import ROOT, log, now_pkt

SAVED = ROOT / "state" / "lab" / "portfolio.enc"
PLAN_SAVED = ROOT / "state" / "lab" / "plan.enc"
# made-up example (lab.yml input 'sample'): shows what the review looks like without sending real holdings
SAMPLE = ("FFC 500 @ 450\nMEBL 800 @ 300\nOGDC 1000 @ 220\nLUCK 300\nSYS 1500 @ 140\nHUBC 1000\nSTCL 20000 @ 7\n"
          "cash 100000")


def _key_env() -> dict:
    return {**os.environ, "PF_KEY": os.getenv("TELEGRAM_BOT_TOKEN", "").strip()}


def encrypt(text: str) -> str:
    r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-a", "-A",
                        "-pass", "env:PF_KEY"], input=text.encode("utf-8"), capture_output=True, env=_key_env(),
                       timeout=30, check=True)
    return r.stdout.decode().strip()


def decrypt(b64: str) -> str:
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A",
                        "-pass", "env:PF_KEY"], input=(b64.strip() + "\n").encode(), capture_output=True,
                       env=_key_env(), timeout=30, check=True)
    return r.stdout.decode("utf-8")


class _Mask(logging.Filter):
    """Replaces the portfolio's symbols in every log line (the Actions log is public)."""

    def __init__(self, syms: list[str]):
        super().__init__()
        self.rx = re.compile(r"\b(" + "|".join(map(re.escape, sorted(syms, key=len, reverse=True))) + r")\b") if syms else None

    def filter(self, record: logging.LogRecord) -> bool:
        if self.rx:
            try:
                msg = record.getMessage()
            except Exception:  # noqa: BLE001
                return True
            record.msg, record.args = self.rx.sub("***", msg), ()
        return True


def _source(text: str | None) -> tuple[str | None, str]:
    """The holdings text from the argument, the PORTFOLIO environment value ('enc:...' or 'saved'), or the file."""
    if text:
        return text, "given"
    env = (os.getenv("PORTFOLIO") or "").strip()
    if env == "sample-plan":
        from .profile import TEMPLATE
        return TEMPLATE.replace("amount: 1000000", "amount: 200000") + "\nholdings:\n" + SAMPLE, "sample"
    if env == "sample":
        return SAMPLE, "sample"
    if env.startswith("enc:"):
        return decrypt(env[4:]), "bot"
    if env == "saved" or not env:
        if SAVED.exists():
            return decrypt(SAVED.read_text(encoding="utf-8")), "saved"
        return None, "none saved"
    return None, "plain"


def run(cfg: dict, text: str | None = None, dry: bool = False) -> bool:
    from ..telegram import Sender
    from . import holdings
    s = Sender(dry)
    admin = s.admin

    def say(msg: str) -> None:
        if dry or not admin:
            print(msg)
        else:
            s._post(admin, msg, False)

    try:
        text, how = _source(text)
    except Exception as e:  # noqa: BLE001
        log.error("portfolio: could not read the holdings (%s)", type(e).__name__)
        say("Portfolio review: the holdings could not be read. Please send them again.")
        return False
    if text is None:
        say("Portfolio review: " + ("no saved portfolio yet. Send it first, e.g.\n/portfolio\nFFC 500 @ 450\nMEBL 800\ncash 100000"
                                    if how == "none saved" else "please send the holdings through the bot (they travel encrypted)."))
        return False
    if re.match(r"^\s*/?plan\b", text, re.I):
        return _plan(cfg, s, say, text, how, dry)
    hold, cash, mode, probs = holdings.parse(text)
    if not hold or mode == "mixed":
        say("Portfolio review: I could not read the holdings" + (f" ({'; '.join(probs[:3])})" if probs else "") +
            ". One per line, e.g.\nFFC 500 @ 450   (shares @ cost)\nMEBL 800\nOGDC 20%\ncash 100000")
        return False
    syms = [h["sym"] for h in hold]
    mask = _Mask(syms)
    for h in logging.getLogger().handlers:
        h.addFilter(mask)
    log.info("portfolio review: %d holdings (%s)", len(hold), how)
    try:
        return _review(cfg, s, say, hold, cash, mode, probs, text, how, dry)
    finally:
        for h in logging.getLogger().handlers:
            h.removeFilter(mask)


def _plan(cfg, s, say, text, how, dry) -> bool:
    """Investment plan (pf/plan.py). The client's own holdings are masked in the log like a review's."""
    from . import holdings, plan, profile
    _, hold_text, _ = profile.parse(text)
    mask = _Mask([h["sym"] for h in holdings.parse(hold_text)[0]] if hold_text else [])
    for h in logging.getLogger().handlers:
        h.addFilter(mask)
    log.info("investment plan requested (%s)", how)
    try:
        ok = plan.run(cfg, text, s, say, dry)
    finally:
        for h in logging.getLogger().handlers:
            h.removeFilter(mask)
    if ok and how == "bot":   # the profile is reused by later /portfolio reviews (loss limit, style)
        try:
            PLAN_SAVED.parent.mkdir(parents=True, exist_ok=True)
            PLAN_SAVED.write_text(encrypt(text), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("plan: not saved (%s)", type(e).__name__)
    return ok


def saved_profile() -> dict | None:
    """The client profile of the last plan (for /portfolio suggestions), if one was made."""
    if not PLAN_SAVED.exists():
        return None
    try:
        from . import profile
        a, _, _ = profile.parse(decrypt(PLAN_SAVED.read_text(encoding="utf-8")))
        return profile.score(a)
    except Exception:  # noqa: BLE001
        return None


def _review(cfg, s, say, hold, cash, mode, probs, text, how, dry) -> bool:
    import json

    from ..lab import chunks
    from . import data, engine, report
    state = None
    sp = ROOT / "state" / "state.json"
    if sp.exists():
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None
    D = data.load([h["sym"] for h in hold], cfg, state)
    R = engine.analyse(hold, cash, mode, D, cfg, saved_profile())
    if R.get("error"):
        say(f"Portfolio review: {R['error']}.")
        return False
    if probs:
        R["pos"]["notes"].append("skipped lines: " + "; ".join(probs[:3]))
    now = now_pkt()
    last = D["idx"].index[-1]
    live = now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) <= (15, 30) and not D["live_full"] and D["live_px"]
    meta = {"price_day": (f"{now:%a %d %b %Y %H:%M} (live)" if live else f"{last:%a %d %b %Y}"),
            "date_long": f"{now:%A, %d %B %Y}", "tier": D["tier"], "sector": D["sector"]}
    pngs, pdf = report.build(R, meta)
    msg = report.summary_text(R, meta)
    if dry or not s.admin:
        out = ROOT / "out" / "portfolio"
        out.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(pngs, 1):
            (out / f"page{i}.png").write_bytes(b)
        (out / "portfolio-review.pdf").write_bytes(pdf)
        (out / "summary.txt").write_text(msg, encoding="utf-8")
        log.info("portfolio review (dry run): %d pages written to out/portfolio", len(pngs))
    else:
        for part in chunks(msg):
            s._post(s.admin, part, False)
        chats, s.chats = s.chats, [s.admin]
        try:
            s.send_album(pngs, "Portfolio review", name="portfolio")
        finally:
            s.chats = chats
        s.send_document(pdf, f"Portfolio-review-{now:%Y-%m-%d}.pdf", "Portfolio review (8 pages)", admin_only=True)
        log.info("portfolio review sent (%d pages)", len(pngs))
    if how == "bot":   # remembered (encrypted, inside the Lab's private data) for '/portfolio' without holdings
        try:
            SAVED.parent.mkdir(parents=True, exist_ok=True)
            SAVED.write_text(encrypt(text), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("portfolio: not saved (%s)", type(e).__name__)
    return True
