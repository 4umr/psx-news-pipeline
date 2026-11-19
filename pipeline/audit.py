"""Daily self-audit, part of the 23:45 private report: did today's expected outputs happen, and do they agree
with the market? Built 20 Nov 2026 after the closing card was replaced by a "PSX closed today" notice on two
normal trading days (13 and 19 Nov) and the next day's opening and midday cards were skipped, unnoticed for a
week. Each line names one problem in plain words; no lines = nothing wrong found."""
from __future__ import annotations

from datetime import datetime

DAILY_BRIEFS = ("morning", "opening", "midday", "close")  # brokers / results depend on what was published


def problems(state, cfg: dict, now: datetime) -> list[str]:
    from . import watchers
    day = f"{now:%Y-%m-%d}"
    out: list[str] = []
    runs = ((state.data.get("stats") or {}).get(day) or {}).get("runs", 0)
    weekday = now.weekday() < 5
    expect = 550 if weekday else 120  # about 690 passes on a weekday (fast loop), 143 at the weekend
    if runs < expect:
        out.append(f"The loop ran {runs} passes today (normal: {'about 690' if weekday else 'about 143'}): the job "
                   "chain had gaps, so some news or alerts came late.")
    for k in sorted(state.data.get("flags") or {}):
        if k.startswith("wfgaveup:") and k.endswith(day):
            wf = k.split(":")[1:-1]
            out.append(f"Could not start {' '.join(wf)} on time (GitHub refused 3 times); its own late schedule ran it.")
    if not weekday:
        return out
    official = dict(watchers.official_closes())
    traded = day in official or state.snap("kse_session_date") == day
    if state.flag(f"holiday_notice:{day}") and traded:
        out.append("The closing brief posted 'PSX closed today' although the market traded today. Check the channel.")
    if not traded:
        if not state.flag(f"holiday_notice:{day}"):
            out.append("No KSE-100 session recorded today and no holiday notice was posted: was PSX open?")
        return out
    briefs = state.data.get("briefs") or {}
    sched = cfg.get("briefs") or {}
    missing = [b for b in DAILY_BRIEFS if now.weekday() in (sched.get(b) or {}).get("days", []) and briefs.get(b) != day]
    if missing:
        out.append(f"Brief(s) not sent on a trading day: {', '.join(missing)}.")
    closes = state.data.get("kse_closes") or {}
    if day not in closes:
        out.append("Today's KSE-100 close was not recorded (holiday check, weekly change and AI context use it).")
    elif day in official and abs(official[day] - closes[day]) > 1:
        out.append(f"Recorded KSE-100 close {closes[day]:,.2f} differs from the official {official[day]:,.2f}.")
    pts = len((((state.data.get("intraday") or {}).get(day)) or {}).get("pts") or [])
    if pts < 120:
        out.append(f"The KSE-100 intraday line has only {pts} points (normal: 200 or more); the opening, midday and "
                   "closing charts were thin or missing.")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    no_ai = sum(1 for r in state.data.get("news_log", []) if r.get("ts", 0) >= start
                and str(r.get("why", "")).startswith("AI unavailable"))
    if no_ai >= 3:
        out.append(f"{no_ai} important news items could not be read by the AI today (Gemini busy or failing).")
    return out


def lines(state, cfg: dict, now: datetime) -> list[str]:
    try:
        bad = problems(state, cfg, now)
    except Exception as e:  # noqa: BLE001  the report must still go out
        return [f"Self-audit could not run ({type(e).__name__})."]
    if not bad:
        return ["Self-audit: every expected output happened today and agrees with the market."]
    return ["<b>Self-audit found:</b>"] + [f"- {b}" for b in bad]
