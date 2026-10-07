"""Command line:

  python -m pipeline run                 one pass (what the scheduler calls)
  python -m pipeline run --dry-run       print messages instead of sending
  python -m pipeline loop --every 120    run forever, every N seconds (PC / VPS)
  python -m pipeline brief morning       send a brief now (morning | close | week_ahead)
  python -m pipeline test                send a test message to Telegram
  python -m pipeline chats               list chats the bot can see (find channel id)
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
    b.add_argument("name", choices=["morning", "close", "week_ahead"])
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("test")
    sub.add_parser("chats")
    args = p.parse_args()

    from .runner import run_once
    from .telegram import Sender, find_chats

    cfg = load_config()
    if args.cmd == "run":
        run_once(cfg, args.dry_run)
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
        ok = s.send("✅ Test message from your PSX News Pipeline.")
        print("Sent!" if ok else "Failed — check the token, chat id, and that the bot is an admin of the channel.")
        sys.exit(0 if ok else 1)
    elif args.cmd == "chats":
        find_chats()


if __name__ == "__main__":
    main()
