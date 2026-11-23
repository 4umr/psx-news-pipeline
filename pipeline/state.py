"""Small JSON state store: what was already sent, latest indicator snapshots."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .common import ROOT

STATE_PATH = ROOT / "state" / "state.json"
SEEN_TTL = 7 * 86400        # remember sent items for 7 days
TITLE_TTL = 48 * 3600       # fuzzy-dedupe titles over 48h
PRIVATE = ("level_log", "level_alerts", "price_checks", "tg_dispatch", "tg_offset")


class State:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self.data: dict = {}
        bak = path.with_suffix(".bak")
        for candidate in (path, bak):  # fall back to the previous good copy if the file is damaged
            if candidate.exists():
                try:
                    self.data = json.loads(candidate.read_text(encoding="utf-8"))
                    break
                except (json.JSONDecodeError, OSError):
                    continue
        self._blob = self.data.pop("_private", None)
        if self._blob:
            self._unseal()
        self.is_new = not self.data
        self.data.setdefault("seen", {})
        self.data.setdefault("titles", [])
        self.data.setdefault("snap", {})
        self.data.setdefault("briefs", {})
        self.data.setdefault("flags", {})
        self.data.setdefault("headlines", [])   # recent scored news for briefs
        self.data.setdefault("kse_closes", {})  # date -> KSE-100 close

    # --- seen items -------------------------------------------------
    def seen(self, key: str) -> bool:
        return key in self.data["seen"]

    def mark(self, key: str) -> None:
        self.data["seen"][key] = int(time.time())

    # --- fuzzy titles -----------------------------------------------
    @property
    def titles(self) -> list:
        return self.data["titles"]

    def add_title(self, tokens: list[str], topic: str = "", weak: bool = False) -> None:
        """weak = seen only from a source that is not on the trusted list: it must not stop the trusted version of
        the same story later (23 Nov 2026: 'IMF approves $497 mln loan' came first from Yeni Safak, and every later
        trusted report was dropped as a duplicate of it, so the channel never carried it)."""
        self.data["titles"].append([tokens, int(time.time()), topic] + ([1] if weak else []))

    def strong_titles(self) -> list:
        """Titles from trusted / official sources only (what a trusted item is de-duplicated against)."""
        return [t for t in self.data["titles"] if not (len(t) > 3 and t[3])]

    # --- snapshots / flags ------------------------------------------
    def snap(self, name: str, default=None):
        return self.data["snap"].get(name, default)

    def set_snap(self, name: str, value) -> None:
        self.data["snap"][name] = value

    def flag(self, key: str) -> bool:
        return key in self.data["flags"]

    def set_flag(self, key: str) -> None:
        self.data["flags"][key] = int(time.time())

    def add_headline(self, item: dict) -> None:
        self.data["headlines"].append(item)

    # --- private part ------------------------------------------------
    # The state file is backed up to the PUBLIC 'state' branch. The Research Lab's level alerts (which stock hit its
    # buy zone, target or fails-below line), the prices logged for them and the owner's command counter are private
    # research, so they are stored encrypted inside the file (openssl AES-256, key = the bot token, as the Lab and
    # swing backups). Without the key (a local test) they stay as they are.
    def _unseal(self) -> None:
        try:
            from .pf import decrypt
            self.data.update(json.loads(decrypt(self._blob)))
            self._blob = None
        except Exception:  # noqa: BLE001  no key here: keep the sealed copy so it is not lost on the next save
            pass

    def _sealed(self, d: dict) -> dict:
        import os
        priv = {k: d[k] for k in PRIVATE if k in d}
        if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
            return d if not self._blob else {**d, "_private": self._blob}
        out = {k: v for k, v in d.items() if k not in PRIVATE}
        try:
            from .pf import encrypt
            out["_private"] = encrypt(json.dumps(priv, ensure_ascii=False, separators=(",", ":")))
        except Exception:  # noqa: BLE001  never write them in the clear: keep the last sealed copy instead
            if self._blob:
                out["_private"] = self._blob
        return out

    def save(self) -> None:
        now = time.time()
        d = self.data
        d["seen"] = {k: v for k, v in d["seen"].items() if now - v < SEEN_TTL}
        d["titles"] = [t for t in d["titles"] if now - t[1] < TITLE_TTL][-4000:]
        d["flags"] = {k: v for k, v in d["flags"].items() if now - v < SEEN_TTL}
        d["headlines"] = [h for h in d["headlines"] if now - h["ts"] < 36 * 3600][-400:]
        closes = d["kse_closes"]
        d["kse_closes"] = dict(sorted(closes.items())[-30:])
        d["_saved"] = int(now)  # the workflow keeps whichever copy (cache or backup branch) is newer
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():  # keep the previous good copy
            try:
                self.path.with_suffix(".bak").write_bytes(self.path.read_bytes())
            except OSError:
                pass
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._sealed(d), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)
