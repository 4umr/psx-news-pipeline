"""Small JSON state store: what was already sent, latest indicator snapshots."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .common import ROOT

STATE_PATH = ROOT / "state" / "state.json"
SEEN_TTL = 7 * 86400        # remember sent items for 7 days
TITLE_TTL = 48 * 3600       # fuzzy-dedupe titles over 48h


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

    def add_title(self, tokens: list[str], topic: str = "") -> None:
        self.data["titles"].append([tokens, int(time.time()), topic])

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

    def save(self) -> None:
        now = time.time()
        d = self.data
        d["seen"] = {k: v for k, v in d["seen"].items() if now - v < SEEN_TTL}
        d["titles"] = [t for t in d["titles"] if now - t[1] < TITLE_TTL][-4000:]
        d["flags"] = {k: v for k, v in d["flags"].items() if now - v < SEEN_TTL}
        d["headlines"] = [h for h in d["headlines"] if now - h["ts"] < 36 * 3600][-400:]
        closes = d["kse_closes"]
        d["kse_closes"] = dict(sorted(closes.items())[-30:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():  # keep the previous good copy
            try:
                self.path.with_suffix(".bak").write_bytes(self.path.read_bytes())
            except OSError:
                pass
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)
