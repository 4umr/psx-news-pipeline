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
        self.failed: list[dict] = []   # messages to retry next run (outbox)
        self.channel_attempts = 0      # posts attempted to the channel this run
        self.channel_ok = True         # False if Telegram refused (bot removed / token revoked)
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
        """One image with an (HTML) caption — the caption can carry the whole message (max ~1024 chars)."""
        if self.dry:
            path = self.preview.parent / f"{name}.png"
            path.write_bytes(png)
            with open(self.preview, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n[IMAGE {path.name}]\n{caption}\n")
            self.sent += 1
            return True
        ok = True
        for chat in self.chats:
            sent = self._photo(chat, png, caption, name)
            log.info("image card '%s' %s", name, "sent" if sent else "FAILED")
            ok &= sent
        return ok

    @staticmethod
    def fits_caption(text: str) -> bool:
        return _vis(text) <= 1000

    def send_admin_plain(self, text: str) -> bool:
        """Plain-text message to the owner (keeps WhatsApp *bold* markers intact for copy-paste)."""
        if self.dry:
            return self.send(f"[ADMIN ONLY · plain text]\n{text}")
        if not self.admin:
            return False
        ok = True
        for i in range(0, len(text), MAX_LEN):
            ok &= self._post(self.admin, text[i:i + MAX_LEN], False, html_mode=False)
        return ok

    def send_album(self, pngs: list[bytes], caption: str = "", name: str = "card") -> bool:
        """Several cards as one album (caption on the first). Falls back to single photos."""
        pngs = [p for p in pngs if p]
        if not pngs:
            return False
        if len(pngs) == 1:
            return self.send_photo(pngs[0], caption, name=name)
        if self.dry:
            for i, p in enumerate(pngs, 1):
                self.send_photo(p, caption if i == 1 else "", name=f"{name}_{i}")
            return True
        import json as _json
        url = f"https://api.telegram.org/bot{self.token}/sendMediaGroup"
        media = [{"type": "photo", "media": f"attach://p{i}", **({"caption": caption[:1000], "parse_mode": "HTML"}
                                                                  if i == 0 and caption else {})}
                 for i in range(len(pngs))]
        files = {f"p{i}": (f"{name}_{i}.png", p, "image/png") for i, p in enumerate(pngs)}
        ok = True
        for chat in self.chats:
            sent = False
            for attempt in range(3):
                try:
                    r = http().post(url, data={"chat_id": chat, "media": _json.dumps(media)}, files=files, timeout=60)
                    if r.status_code == 429:
                        time.sleep(min(r.json().get("parameters", {}).get("retry_after", 5), 60))
                        continue
                    sent = r.ok
                    if not r.ok:
                        log.error("Telegram album error %s: %s", r.status_code, r.text[:200])
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("Telegram album failed (%s), retrying", e)
                    time.sleep(2 * (attempt + 1))
            if sent:
                self.sent += 1
                log.info("album of %d cards sent to %s", len(pngs), "channel")
                time.sleep(1.5)
            else:  # fall back to individual photos so the cards still arrive
                for i, p in enumerate(pngs):
                    ok &= self._photo(chat, p, caption if i == 0 else "", f"{name}_{i}")
        return ok

    def _photo(self, chat: str, png: bytes, caption: str, name: str) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        for attempt in range(3):
            try:
                r = http().post(url, data={"chat_id": chat, "caption": caption[:1024], "parse_mode": "HTML"},
                                files={"photo": (f"{name}.png", png, "image/png")}, timeout=40)
                if r.status_code == 429:
                    time.sleep(min(r.json().get("parameters", {}).get("retry_after", 5), 60))
                    continue
                if not r.ok:
                    log.error("Telegram photo error %s: %s", r.status_code, r.text[:200])
                    return False
                self.sent += 1
                time.sleep(1.1)
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram photo failed (%s), retrying", e)
                time.sleep(2 * (attempt + 1))
        return False

    def _post(self, chat: str, text: str, preview: bool, html_mode: bool = True) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": chat, "text": text, "disable_web_page_preview": not preview}
        if html_mode:
            payload["parse_mode"] = "HTML"
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
                if chat != self.admin:
                    self.channel_attempts += 1
                if not r.ok:
                    log.error("Telegram error %s: %s", r.status_code, r.text[:200])
                    if chat != self.admin and r.status_code in (400, 401, 403) and "parse" not in r.text.lower():
                        self.channel_ok = False
                    if r.status_code >= 500 and chat != self.admin:
                        self.failed.append({"chat": chat, "text": text, "ts": int(time.time())})
                    return False
                self.sent += 1
                time.sleep(1.1)  # stay well under channel rate limits
                return True
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram send failed (%s), retrying", e)
                time.sleep(2 * (attempt + 1))
        if chat != self.admin:
            # network trouble: keep it for the next run instead of losing the message
            self.failed.append({"chat": chat, "text": text, "ts": int(time.time())})
        return False

    def retry_outbox(self, outbox: list[dict]) -> list[dict]:
        """Resend messages that failed in earlier runs (max 6h old). Returns what still failed."""
        if self.dry or not outbox:
            return []
        for m in outbox:
            if time.time() - m["ts"] < 6 * 3600:
                n = len(self.failed)
                if not self._post(m["chat"], m["text"], False) and len(self.failed) > n:
                    self.failed[-1]["ts"] = m["ts"]  # keep original age so it expires after 6h
        return []


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
                found[c["id"]] = (c.get("type"), c.get("title") or c.get("username") or c.get("first_name") or "")
    if not found:
        print("No chats yet. Open your bot in Telegram, press START (or send 'hi'), then run this again "
              "within 24 hours.")
    for cid, (kind, name) in found.items():
        if kind == "private":
            # Don't print personal ids in (public) logs — send the id to that person instead.
            http().post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
                "chat_id": cid,
                "text": f"👋 Your personal chat ID is: {cid}\n\nCopy this number into the GitHub secret "
                        f"TELEGRAM_ADMIN_CHAT_ID to receive private health alerts and WhatsApp-ready copies."})
            masked = str(cid)[:2] + "*" * max(len(str(cid)) - 4, 0) + str(cid)[-2:]
            print(f"{masked}  ->  private chat (full id sent to that person in Telegram)")
        else:
            print(f"{cid}  ->  {kind}: {name}")
