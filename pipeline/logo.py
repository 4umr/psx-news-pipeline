"""Company logos for the Stock of the Day cards, taken from the company's own website (as listed on PSX).

Used only to identify the company. Order of preference: an <img> marked as the logo, the site's
apple-touch-icon, an og:image whose address says 'logo', then the favicon. SVG files cannot be drawn by
the card engine, so they are skipped. Cached in state/sotd/logos; if nothing usable is found, the card
draws the ticker's initials instead (no error).
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .common import ROOT, http, log

DIR = ROOT / "state" / "sotd" / "logos"
INDEX = DIR / "index.json"
MAX_AGE = 180 * 86400
_LOOSE = False  # review helper only: accept any image marked as the logo


def _get(url: str, timeout: int = 20):
    try:
        return http().get(url, timeout=timeout, allow_redirects=True)
    except Exception:  # noqa: BLE001  some company sites have broken certificates: logos are public, retry plainly
        try:
            import requests
            return requests.get(url, timeout=timeout, allow_redirects=True, verify=False,
                                headers={"User-Agent": "Mozilla/5.0"})
        except Exception:  # noqa: BLE001
            return None


_GENERIC = {"limited", "company", "pakistan", "industries", "corporation", "holdings", "group", "the", "and",
            "mills", "bank", "power", "cement", "energy", "foods", "autos", "systems", "products", "services"}


def _name_words(name: str, home: str) -> set[str]:
    """Words that must appear in a logo image's file name or label: company name words and the web address stem."""
    import re
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", name or "") if w.lower() not in _GENERIC}
    host = re.sub(r"^https?://(www\.)?", "", home).split("/")[0].split(".")[0].lower()
    if len(host) >= 3:
        words.add(host)
    return words


def _candidates(home: str, html: bytes, name: str = "") -> list[str]:
    """Logo images that name the company (a bare 'logo' image is often a product brand or an award badge),
    then the site's own icons."""
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    words = _name_words(name, home)

    def add(u):
        if u and not u.startswith("data:"):
            full = urljoin(home, u.strip())
            if full not in out:
                out.append(full)
    for img in soup.find_all("img")[:80]:
        attrs = " ".join(str(img.get(a, "")) for a in ("src", "data-src", "alt", "class", "id", "title")).lower()
        parent = " ".join(str(p.get("class", "")) + str(p.get("id", "")) for p in img.parents if hasattr(p, "get"))[:300]
        named = any(w in attrs for w in words) or not words or _LOOSE
        bad = any(b in attrs for b in ("award", "forbes", "badge", "certif", "partner", "client", "brand"))
        if named and not bad and ("logo" in attrs or "logo" in parent.lower()):
            add(img.get("src") or img.get("data-src"))
    for link in soup.find_all("link"):
        if "apple-touch-icon" in " ".join(link.get("rel") or []).lower():
            add(link.get("href"))
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and "logo" in (og.get("content") or "").lower():
        add(og.get("content"))
    for link in soup.find_all("link"):
        if "icon" in " ".join(link.get("rel") or []).lower():
            add(link.get("href"))
    add("/favicon.ico")
    return out


def _usable(data: bytes):
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001  SVG, HTML error pages, broken files
        return None
    im = im.convert("RGBA")
    if max(im.size) < 48:
        return None
    # trim empty (transparent or near-white) borders
    bg = Image.new("RGBA", im.size, (255, 255, 255, 0))
    alpha = im.getchannel("A")
    box = alpha.getbbox() if alpha.getextrema()[0] < 250 else None
    if not box:
        from PIL import ImageChops
        diff = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, (255, 255, 255)))
        box = diff.getbbox()
    del bg
    if box:
        im = im.crop(box)
    return im if min(im.size) >= 16 else None


def get(symbol: str, website: str, name: str = "") -> Path | None:
    """Path to a cached PNG logo, fetching it if needed. None if no usable logo was found."""
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    except (OSError, json.JSONDecodeError):
        idx = {}
    path = DIR / f"{symbol}.png"
    rec = idx.get(symbol)
    if rec and time.time() - rec.get("ts", 0) < MAX_AGE:
        return path if rec.get("ok") and path.exists() else None
    ok, src = False, ""
    site = (website or "").strip().split()[0] if website else ""
    if site:
        home = site if site.startswith("http") else "https://" + site
        r = _get(home)
        if r is not None and r.ok:
            for u in _candidates(r.url, r.content, name)[:8]:
                if u.lower().split("?")[0].endswith(".svg"):
                    continue
                g = _get(u)
                if g is None or not g.ok or len(g.content) > 3_000_000 or "svg" in g.headers.get("content-type", ""):
                    continue
                im = _usable(g.content)
                if im is not None:
                    im.save(path, "PNG")
                    ok, src = True, u
                    break
    idx[symbol] = {"ok": ok, "src": src, "ts": int(time.time())}
    INDEX.write_text(json.dumps(idx, indent=1), encoding="utf-8")
    if not ok:
        log.info("logo for %s not found (website %s): initials will be used", symbol, site or "none")
    return path if ok else None
