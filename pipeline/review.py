"""Replay test for the main-channel editor: what WOULD the channel have posted from real past candidates?

    python -m pipeline review --hours 36

Takes the important headlines (score >= min_score_instant) recorded in state/state.json, in time order,
and runs each one through the same gates as the live pipeline (editor.before_ai, Gemini analyse with the
posts so far, editor.after_ai). Nothing is sent and the state is not saved. Runs on GitHub (workflow
review.yml) because the Gemini key only exists there; the result table is printed to the run log.
"""
from __future__ import annotations

import time
from datetime import datetime

from . import analysis, context, editor
from .common import NewsItem, log, now_pkt
from .state import State


def run(cfg: dict, hours: float = 36, limit: int = 40, pause: float = 6.0) -> None:
    state = State()
    analysis.init(state, cfg)
    from .sources import scs
    view = scs.kse100_view()
    ctx = context.build(state, view, state.snap("mk_last") or {}) if analysis.available(cfg) else ""
    tickers = context.tickers(view)
    tz = now_pkt().tzinfo
    cut = time.time() - hours * 3600
    cands = sorted((h for h in state.data.get("headlines", []) if h["ts"] >= cut and h["sc"] >= cfg["min_score_instant"]),
                   key=lambda h: h["ts"])[:limit]
    state.data["posted"] = []
    print(f"REPLAY of {len(cands)} candidates from the last {hours:.0f} hours "
          f"(AI: {'on' if analysis.available(cfg) else 'OFF, backup rules only'})")
    rows = []
    for h in cands:
        it = NewsItem(title=h["t"], url=h["u"], source=h["s"], published=None, summary="", pk=True, official=False)
        it.score, it.topic, it.label = h["sc"], h.get("topic", ""), h.get("l", "")
        editor._clock = lambda ts=h["ts"]: ts
        ai, status = None, "skipped"
        reason = editor.before_ai(it, state, cfg, view)
        if not reason:
            ai = analysis.analyse(it.title, it.url, it.source, cfg, "", ctx, tickers, editor.posted_list(state))
            status = analysis.last_status
            reason = editor.after_ai(it, ai, status, state, cfg)
            time.sleep(pause)  # Gemini free tier: about 10 requests a minute
        if not reason:
            editor.record_post(state, it.title, it.topic)
        rows.append((h, reason, ai, status))
        when = datetime.fromtimestamp(h["ts"], tz)
        print(f"\n{when:%d %b %H:%M} | score {h['sc']} | {h.get('topic', '')} | {h['s']}\n  {h['t']}")
        if ai:
            print(f"  AI: materiality {ai.get('materiality')}, confirmed {ai.get('confirmed')}, kind {ai['kind']}, "
                  f"level {ai['level']}, tone {ai['tone']}, same event: {ai.get('same_event') or '-'}")
            print(f"  AI summary: {ai['summary']}")
        elif status not in ("skipped",):
            print(f"  AI: {status}")
        print(f"  => {'POST' if not reason else 'admin: ' + reason}")
    editor._clock = time.time
    posted = [r for r in rows if not r[1]]
    print(f"\nRESULT: {len(posted)} of {len(rows)} would reach the main channel:")
    for h, _, ai, _ in posted:
        print(f"  {datetime.fromtimestamp(h['ts'], tz):%d %b %H:%M}  {h['s'][:20]:20}  {h['t'][:100]}")
    log.info("review done: %d candidates, %d would be posted", len(rows), len(posted))
