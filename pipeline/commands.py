"""Commands from the owner's private chat with the bot (TELEGRAM_ADMIN_CHAT_ID only), so the system can be
used from the phone without opening GitHub:

  STCL   or   /analyse STCL,LUCK     Research Lab card(s) for any stock now (starts lab.yml, about 3-6 minutes)
  /note FABL                         Stock of the Day note preview for that company, to this chat only
  /status                            is everything running? today's numbers
  /help                              this list

Messages from anyone else are ignored: never answered and never logged (Actions logs are public). Checked at
the start of every pass of the news loop; between passes the loop long-polls Telegram (`wait`), so a command
is picked up within seconds. Workflows are started with the job's own GITHUB_TOKEN (never logged), at most
`commands.max_per_day` a day.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime

from .common import esc, http, log

_OFFSET: dict = {"next": None}   # next update id; also in state 'tg_offset' so a new job does not re-read

HELP = ("<b>Commands</b> (only this chat can use them)\n"
        "<b>STCL</b> or <b>/analyse STCL,LUCK</b>: Research Lab card for any stock, now (about 3-6 minutes)\n"
        "<b>/note FABL</b>: Stock of the Day note preview for that company, to this chat only (about 5 minutes)\n"
        "<b>/status</b>: is everything running, today's numbers\n"
        "<b>/help</b>: this list")


def _admin() -> str:
    return os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()


def _api(method: str, **params):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    try:
        r = http().post(f"https://api.telegram.org/bot{token}/{method}", json=params,
                        timeout=int(params.get("timeout", 0)) + 15)
        j = r.json()
        return j.get("result") if j.get("ok") else None
    except Exception as e:  # noqa: BLE001  (the message never contains the token)
        log.warning("Telegram %s failed: %s", method, type(e).__name__)
        return None


def wait(seconds: float) -> bool:
    """Sleep up to `seconds`, but return True at once if a new Telegram message is waiting (it is NOT consumed
    here: the next pass reads and answers it). Used by the loop between passes."""
    end = time.time() + seconds
    adm = _admin()
    while _OFFSET["next"] is not None and adm and end - time.time() >= 2:
        ups = _api("getUpdates", offset=_OFFSET["next"], timeout=int(end - time.time()), limit=10,
                   allowed_updates=["message"])
        if ups is None:
            break  # Telegram not reachable: plain sleep
        if not ups:
            return False  # nothing came in the whole time
        if any(str(((u.get("message") or {}).get("chat") or {}).get("id")) == adm for u in ups):
            return True
        _OFFSET["next"] = ups[-1]["update_id"] + 1  # someone else: ignore while waiting (the next pass discards it)
    left = end - time.time()
    if left > 0:
        time.sleep(left)
    return False


def handle(state, sender, cfg: dict, now: datetime) -> int:
    """Read new messages; answer the owner's commands. Returns how many were answered."""
    adm = _admin()
    if sender.dry or not adm or not (cfg.get("commands") or {}).get("enabled", True):
        return 0
    params = {"timeout": 0, "allowed_updates": ["message"]}
    if state.data.get("tg_offset"):
        params["offset"] = state.data["tg_offset"]
    ups = _api("getUpdates", **params)
    if ups is None:
        return 0
    n = 0
    for u in ups:
        state.data["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != adm:
            continue
        if time.time() - m.get("date", 0) > 1800:
            continue  # sent while the loop was down: too old to act on
        text = (m.get("text") or "").strip()
        if not text:
            continue
        try:
            reply = respond(text, state, cfg, now)
        except Exception as e:  # noqa: BLE001
            log.exception("command failed")
            reply = f"That did not work ({esc(type(e).__name__)}). Try again in a minute."
        if reply:
            sender.send_admin(reply)
            n += 1
    _OFFSET["next"] = state.data.get("tg_offset") or 0
    return n


def _symbols(arg: str) -> list[str]:
    return list(dict.fromkeys(x for x in re.split(r"[\s,]+", arg.upper()) if x))[:3]


def _known() -> set[str]:
    from .sources import scs
    try:
        return {(r.get("company_code") or "").strip() for r in scs.daily_activity() if r.get("company_code")}
    except Exception:  # noqa: BLE001
        return set()


def respond(text: str, state, cfg: dict, now: datetime) -> str:
    """The reply to one message (also starts the workflow it asks for)."""
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.lstrip("/").split("@")[0].lower()
    if cmd in ("help", "start", "menu", "commands", "?"):
        return HELP
    if cmd in ("status", "health"):
        return status(state, now)
    if cmd in ("analyse", "analyze", "a", "lab", "stock"):
        syms, what = _symbols(arg), "lab"
    elif cmd in ("note", "sotd"):
        syms, what = _symbols(arg)[:1], "note"
    elif re.fullmatch(r"[A-Za-z0-9]{2,10}([\s,]+[A-Za-z0-9]{2,10}){0,2}", text.strip()):
        syms, what = _symbols(text), "lab"   # a bare ticker (or two or three)
    else:
        return "I did not understand that.\n\n" + HELP
    if not syms:
        return "Which stock? For example: <b>/analyse STCL</b>\n\n" + HELP
    known = _known()
    if known and not text.startswith("/") and not any(s in known for s in syms):
        return "I did not understand that.\n\n" + HELP  # ordinary words ('hi there'), not tickers
    unknown = [s for s in syms if known and s not in known]
    if unknown:
        return (f"{esc(', '.join(unknown))}: not a PSX symbol I can find in today's market data. "
                "Check the spelling (PSX symbols, e.g. LUCK, FFC, STCL).")
    day = f"{now:%Y-%m-%d}"
    used = state.data.get("tg_dispatch") or {}
    if used.get("d") != day:
        used = {"d": day, "n": 0}
    cap = int((cfg.get("commands") or {}).get("max_per_day", 12))
    if used["n"] >= cap:
        return f"Today's limit of {cap} on-request runs is used up (it protects the free GitHub minutes). Tomorrow again."
    if what == "lab":
        ok, why = dispatch("lab.yml", {"command": "lab", "symbol": ",".join(syms)})
        msg = (f"Analysing <b>{esc(', '.join(syms))}</b> now: the Research Lab card"
               f"{'s arrive' if len(syms) > 1 else ' arrives'} here in about {3 + 2 * len(syms)} minutes.")
    else:
        ok, why = dispatch("stock.yml", {"command": "sotd --preview", "symbol": syms[0]})
        msg = (f"Preparing a Stock of the Day note on <b>{esc(syms[0])}</b> (preview, to this chat only): "
               "about 5 minutes.")
    if not ok:
        return f"Could not start it: {esc(why)}. Try again in a few minutes."
    used["n"] += 1
    state.data["tg_dispatch"] = used
    log.info("owner command: %s started", what)
    return msg


def dispatch(workflow: str, inputs: dict) -> tuple[bool, str]:
    """Start a workflow of this repository (needs GITHUB_TOKEN with actions: write, set by the workflow)."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False, "this only works on GitHub (no workflow token here)"
    try:
        r = http().post(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                        json={"ref": "main", "inputs": inputs}, timeout=20,
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                 "X-GitHub-Api-Version": "2022-11-28"})
        return (r.status_code == 204), f"GitHub answered {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def status(state, now: datetime) -> str:
    """Short live health line set for /status."""
    from . import editor
    day = f"{now:%Y-%m-%d}"
    st = (state.data.get("stats") or {}).get(day, {})
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    posted = [p for p in state.data.get("posted", []) if p.get("ts", 0) >= start]
    news = sum(1 for p in posted if p.get("kind") == "news")
    briefs = [k for k, v in (state.data.get("briefs") or {}).items() if v == day]
    down = [k for k, v in (state.data.get("health") or {}).items() if v >= 3]
    comp = list(state.data.get("comp_errors") or {})
    lines = [f"<b>Status</b> · {now:%a %d %b %H:%M} PKT",
             f"Loop running: yes ({st.get('runs', 0)} passes today, {st.get('msgs', 0)} messages sent)",
             f"Main channel today: {news} news posts, {len(posted) - news} data cards"
             + (f"; briefs: {', '.join(sorted(briefs))}" if briefs else ""),
             f"KSE-100 last close {state.snap('kse_last_close') or 'n/a'} ({state.snap('kse_session_date') or 'n/a'})",
             f"AI: {esc(state.snap('ai_status') or 'not checked')}, {(state.data.get('ai_usage') or {}).get(day, 0)} "
             "requests today",
             "Sources: " + ("all healthy" if not down else "DOWN: " + esc(", ".join(down))),
             "Components: " + ("OK" if not comp else "failing: " + esc(", ".join(comp)))]
    if ed := editor.day_summary(state, start):
        lines.append(ed)
    return "\n".join(lines)
