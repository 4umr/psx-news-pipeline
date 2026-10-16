"""Command line:

  python -m pipeline run                 one pass (what the scheduler calls)
  python -m pipeline run --dry-run       print messages instead of sending
  python -m pipeline loop --every 120    run forever, every N seconds (PC / VPS)
  python -m pipeline brief morning       send a brief now (morning | close | week_ahead)
  python -m pipeline test                send a test message to Telegram
  python -m pipeline chats               list chats the bot can see (find channel id)
  python -m pipeline about               post the "About this channel" message (to pin)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from .common import load_config, log


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    p = argparse.ArgumentParser(prog="pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    lp = sub.add_parser("loop")
    lp.add_argument("--every", type=int, default=120)
    lp.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("brief")
    b.add_argument("name", choices=["morning", "opening", "midday", "close", "results", "macro", "brokers", "week_ahead"])
    b.add_argument("--dry-run", action="store_true")
    tp = sub.add_parser("test")
    tp.add_argument("--admin-only", action="store_true", help="only message the private admin chat")
    sub.add_parser("chats")
    ab = sub.add_parser("about")
    ab.add_argument("--dry-run", action="store_true")
    sw = sub.add_parser("swing", help="daily swing scanner after the close (admin chat only)")
    sw.add_argument("--dry-run", action="store_true")
    sw.add_argument("--force", action="store_true", help="scan again even if this session was already scanned")
    sw.add_argument("--weekly", action="store_true", help="Sunday public cards: mid & small caps + results week ahead")
    bt = sub.add_parser("backtest", help="test the swing rules on PSX history since 2018")
    bt.add_argument("--refresh", action="store_true", help="download the full history again")
    bt.add_argument("--send", action="store_true", help="send the report cards to the admin chat")
    bt.add_argument("--notify-changes", action="store_true", help="send the report only if a live rule's verdict changed")
    args = p.parse_args()

    if args.cmd in ("swing", "backtest"):
        cfg = load_config()
        if args.cmd == "swing":
            from .swing import scan, weekly
            if args.weekly:
                weekly(cfg, args.dry_run, args.force)
            else:
                scan(cfg, args.dry_run, args.force)
        else:
            from . import backtest
            from .cards import backtest_cards
            from .common import ROOT
            before = backtest.load_stats()
            summary, _ = backtest.run(cfg, args.refresh)
            changes = backtest.verdict_changes(before, backtest.load_stats())
            pngs = backtest_cards(cfg, summary, backtest.load_stats())
            out = ROOT / "out" / "backtest"
            for i, png in enumerate(pngs, 1):
                (out / f"report_{i}.png").write_bytes(png)
            from .cards import education_card
            (out / "education.png").write_bytes(education_card(cfg, summary, backtest.load_stats()))
            print(f"Report: {len(pngs)} cards + education.png in {out}")
            from .telegram import Sender
            if args.send or (args.notify_changes and changes):
                s = Sender()
                if changes:
                    s.send_admin("<b>Swing rules re-test: verdicts changed</b>\n" + "\n".join(changes) +
                                 "\n\nThe scanner follows the new verdicts automatically.")
                for i, png in enumerate(pngs, 1):
                    s.send_admin_photo(png, "Swing rules backtest" if i == 1 else "", name=f"backtest_{i}")
            print("Verdict changes: " + ("; ".join(changes) if changes else "none"))
        return

    from .runner import run_once
    from .telegram import Sender, find_chats

    cfg = load_config()
    if args.cmd == "run":
        run_once(cfg, args.dry_run)  # never raises: errors are contained and reported privately
    elif args.cmd == "brief":
        run_once(cfg, args.dry_run, force_brief=args.name)
    elif args.cmd == "loop":
        while True:
            try:
                run_once(cfg, args.dry_run)
            except Exception:  # noqa: BLE001
                log.exception("run failed")
            time.sleep(args.every)
    elif args.cmd == "test":
        s = Sender()
        if s.dry:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set (.env or environment).")
            sys.exit(1)
        ok = True
        if not args.admin_only:
            ok = s.send("✅ Test message from your PSX News Pipeline.")
            print("Channel: sent!" if ok else "Channel: FAILED — check the token, chat id, and that the bot is an admin of the channel.")
        if s.admin:
            a = s.send_admin("✅ Private admin alerts are working. Health warnings and WhatsApp-ready copies will arrive here.")
            print("Admin chat: sent!" if a else "Admin chat: FAILED — message your bot once from your own account, then check the id.")
            ok &= a
        else:
            print("Admin chat: not configured (TELEGRAM_ADMIN_CHAT_ID).")
            ok = ok and not args.admin_only
        sys.exit(0 if ok else 1)
    elif args.cmd == "chats":
        find_chats()
    elif args.cmd == "about":
        from .runner import about_text
        ok = Sender(args.dry_run).send(about_text(cfg))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
