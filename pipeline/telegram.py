"""Telegram delivery (Bot API). Falls back to printing when no token is set."""
from __future__ import annotations

import html
import os
import re
import sys
import time
from pathlib import Path

from .common import ROOT, http, log

MAX_LEN = 4000  # Telegram limit is 4096


def _vis(text: str) -> int:
    """Length Telegram counts: tags and link URLs are not part of the limit."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def _split(text: str) -> list[str]:
    if _vis(text) <= MAX_LEN:
        return [text]
    # Split on blank lines first so a news item / section is never cut in half
    parts, cur = [], ""
    for para in text.split("\n\n"):
        chunks = [para] if _vis(para) < MAX_LEN else para.split("\n")
        for chunk in chunks:
            if cur and _vis(cur) + _vis(chunk) + 2 > MAX_LEN:
                parts.append(cur.rstrip())
                cur = ""
            cur += chunk + ("\n\n" if chunk is para else "\n")
    if cur.strip():
        parts.append(cur.rstrip())
    return parts


class Sender:
    def __init__(self, dry_run: bool = False):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chats = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
        self.dry = dry_run or not (self.token and self.chats)
        # Optional private chat for operational warnings (never sent to the channel)
        self.admin = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
        self.sent = 0
        if self.dry:
            self.preview = ROOT / "out" / "preview.txt"
            self.preview.parent.mkdir(exist_ok=True)
            log.info("DRY RUN — messages go to %s", self.preview)

    def send(self, text: str, preview: bool = False) -> bool:
        ok = True
        for part in _split(text):
            if self.dry:
                block = f"\n{'=' * 60}\n{part}\n"
                with open(self.preview, "a", encoding="utf-8") as f:
                    f.write(block)
                sys.stdout.buffer.write(block.encode("utf-8"))
                sys.stdout.flush()
                self.sent += 1
                continue
            for chat in self.chats:
                ok &= self._post(chat, part, preview)
        return ok

    def send_admin(self, text: str) -> bool:
        """Operational message to the owner's private chat only."""
        if self.dry:
            return self.send(f"[ADMIN ONLY] {text}")
        if not self.admin:
            log.warning("ADMIN: %s", text)
            return False
        return self._post(self.admin, text, False)

    def send_photo(self, png: bytes, caption: str = "", name: str = "card") -> bool:
        caption = caption[:1000]
        if self.dry:
            path = self.preview.parent / f"{name}.png"
            path.write_bytes(png)
            with open(self.preview, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n[IMAGE {path.name}] {caption}\n")
            self.sent += 1
            return True
        ok = True
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        for chat in self.chats:
            for attempt in range(3):
                try:
                    r = http().post(url, data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                                    files={"photo": (f"{name}.png", png, "image/png")}, timeout=40)
                    if r.status_code == 429:
                        time.sleep(min(r.json().get("parameters", {}).get("retry_after", 5), 60))
                        continue
                    if not r.ok:
                        log.error("Telegram photo error %s: %s", r.status_code, r.text[:200])
                        ok = False
                    else:
                        self.sent += 1
                        time.sleep(1.1)
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("Telegram photo failed (%s), retrying", e)
                    time.sleep(2 * (attempt + 1))
            else:
                ok = False
        return ok

    def _post(self, chat: str, text: str, preview: bool) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": not preview}
        for attempt in range(4):
            try:
                r = http().post(url, json=payload, timeout=20)
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 5)
                    time.sleep(min(wait, 60))
                    continue
                if r.status_code == 400 and "parse" in r.text.lower():
                    # Malformed HTML — resend as plain text rather than lose it
                    payload.pop("parse_mode", None)
                    continue
                if not r.ok:
                    log.error("Telegram error %s: %s", r.status_code, r.text[:200])
                    return False
                self.sent += 1
                time.sleep(1.1)  # stay well under channel rate limits
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram send failed (%s), retrying", e)
                time.sleep(2 * (attempt + 1))
        return False


def find_chats() -> None:
    """Print chats the bot has seen — helps find a private channel's numeric id."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.")
        return
    r = http().get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    if not r.get("ok"):
        print("Telegram error:", r)
        return
    found = {}
    for u in r.get("result", []):
        for k in ("message", "channel_post", "my_chat_member", "edited_channel_post"):
            if k in u:
                c = u[k]["chat"]
                found[c["id"]] = f'{c.get("type")}: {c.get("title") or c.get("username") or c.get("first_name")}'
    if not found:
        print("No chats yet. Post something in your channel (with the bot as admin), then run again.")
    for cid, name in found.items():
        print(f"{cid}  ->  {name}")
