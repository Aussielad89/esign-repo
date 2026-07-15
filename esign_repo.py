"""esign_repo.py — shared ESign repo index core.

Single source of truth for the esign_source.json index format, IPA metadata
extraction, and append/update logic. Used by both esign_watcher.py (task 1)
and esign_gui.py (task 2) so they always emit a consistent index.

Everything here is stdlib + Pillow. No network, no GUI.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Index format / constants
# ---------------------------------------------------------------------------

DEFAULT_INDEX_NAME = "esign_source.json"
ICON_DIR_NAME = "icons"
IPA_DIR_NAME = "ipas"

# Plist keys we care about (kept tolerant of odd capitalisation).
_NAME_KEYS = ("CFBundleDisplayName", "CFBundleName")
_BUNDLE_KEY = "CFBundleIdentifier"
_VERSION_KEYS = ("CFBundleShortVersionString", "CFBundleVersion")
_MIN_IOS_KEY = "MinimumOSVersion"
_ICON_FILES_KEYS = ("CFBundleIcons", "CFBundleIcons~ipad", "CFBundleIconName")


@dataclass
class AppMeta:
    """Normalised metadata extracted from an .ipa (or supplied manually)."""

    name: str
    bundle_identifier: str
    version: str = "1.0"
    min_ios: str | None = None
    description: str = ""
    size: int = 0
    sha1: str = ""
    sha256: str = ""
    icon_path: str | None = None  # local path to extracted icon (served by HTTP)
    source_ipa: str | None = None  # original .ipa file name

    def to_entry(self, base_url: str) -> dict[str, Any]:
        """Build an ESign apps[] entry. `base_url` is the repo base, e.g.
        http://192.168.1.10:8080 — MUST NOT end with a slash."""
        name = (self.name or "Unknown App").strip()
        bid = (self.bundle_identifier or "com.example.unknown").strip()
        entry: dict[str, Any] = {
            "name": name,
            "bundleIdentifier": bid,
            "version": self.version or "1.0",
            "size": self.size,
            "downloadURL": f"{base_url}/{IPA_DIR_NAME}/{Path(self.source_ipa or '').name}",
        }
        if self.min_ios:
            entry["minOSVersion"] = self.min_ios
        if self.description:
            entry["localizedDescription"] = self.description
        if self.sha1:
            entry["sha1"] = self.sha1
        if self.sha256:
            entry["sha256"] = self.sha256
        if self.icon_path:
            icon_name = Path(self.icon_path).name
            entry["iconURL"] = f"{base_url}/{ICON_DIR_NAME}/{icon_name}"
        return entry


# ---------------------------------------------------------------------------
# Index load / save / validate
# ---------------------------------------------------------------------------

def new_index(name: str = "Local ESign Repo") -> dict[str, Any]:
    return {
        "name": name,
        "identifier": "com.local.esignrepo",
        "version": 1,
        "apps": [],
    }


def load_index(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return new_index()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Corrupt index — start fresh but keep a backup.
        backup = p.with_suffix(p.suffix + ".corrupt.bak")
        shutil.copy(p, backup)
        return new_index()
    if not isinstance(data, dict):
        return new_index()
    data.setdefault("name", "Local ESign Repo")
    data.setdefault("identifier", "com.local.esignrepo")
    data.setdefault("version", 1)
    data.setdefault("apps", [])
    if not isinstance(data["apps"], list):
        data["apps"] = []
    return data


def save_index(path: str | Path, index: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    # Atomic replace so a crash mid-write can't corrupt the index.
    shutil.move(str(tmp), str(p))


def validate_index(index: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(index.get("name"), str):
        errors.append("missing or invalid 'name'")
    if not isinstance(index.get("apps"), list):
        errors.append("'apps' must be a list")
        return False, errors
    seen: set[str] = set()
    for i, app in enumerate(index["apps"]):
        if not isinstance(app, dict):
            errors.append(f"apps[{i}] is not an object")
            continue
        bid = app.get("bundleIdentifier")
        if not bid:
            errors.append(f"apps[{i}] missing bundleIdentifier")
        elif bid in seen:
            errors.append(f"duplicate bundleIdentifier: {bid}")
        else:
            seen.add(bid)
        if not app.get("name"):
            errors.append(f"apps[{i}] missing name")
        if not app.get("downloadURL"):
            errors.append(f"apps[{i}] missing downloadURL")
    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# IPA metadata extraction
# ---------------------------------------------------------------------------

def _read_plist_member(zf: zipfile.ZipFile, name: str) -> dict | None:
    try:
        with zf.open(name) as fh:
            return plistlib.load(fh)  # type: ignore[arg-type]
    except Exception:
        return None


def _find_app_root(zf: zipfile.ZipFile) -> str | None:
    """Find the .app/ directory inside Payload/ by locating its Info.plist.

    Zips store file members, not always the directory entry, so we derive
    the app root from any `Payload/<x>.app/...` member.
    """
    for n in zf.namelist():
        lower = n.lower()
        if ".app/" in lower and lower.endswith("info.plist"):
            idx = n.rfind(".app/") + len(".app/")
            return n[:idx]
    # Fallback: any member ending in .app/ or .app
    for n in zf.namelist():
        if n.startswith("Payload/") and ".app" in n:
            idx = n.rfind(".app") + len(".app")
            return n[:idx] + ("/" if not n[idx:idx + 1] == "/" else "")
    return None


def _pick_icon_name(plist: dict) -> list[str]:
    """Return candidate icon base names (without extension) from the plist."""
    candidates: list[str] = []
    icon_name = plist.get("CFBundleIconName")
    if isinstance(icon_name, str):
        candidates.append(icon_name)
    for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        icons = plist.get(key)
        if isinstance(icons, dict):
            primary = icons.get("CFBundlePrimaryIcon")
            if isinstance(primary, dict):
                files = primary.get("CFBundleIconFiles")
                if isinstance(files, list):
                    candidates.extend([f for f in files if isinstance(f, str)])
    return candidates


def _extract_icon(zf: zipfile.ZipFile, app_root: str, candidates: list[str],
                  out_dir: Path, stem: str) -> str | None:
    """Find the best icon PNG inside the .app and write it to out_dir.

    Returns the written file path, or None if no suitable PNG was found.
    Uses Pillow to pick the largest-resolution candidate.
    """
    from PIL import Image
    import io

    names = zf.namelist()
    # Map candidate base names to actual png members (try @2x/@3x too).
    wanted: set[str] = set()
    for c in candidates:
        c = c.replace(".png", "")
        for suffix in ("", "@2x", "@3x", "-1", "-2", "-3"):
            wanted.add(f"{c}{suffix}.png")

    scored: list[tuple[int, int, str]] = []  # (pixels, -filesize, member)
    for m in names:
        if not m.startswith(app_root) or not m.lower().endswith(".png"):
            continue
        base = m.rsplit("/", 1)[-1].lower()
        # direct candidate match, or fall back to any reasonably-sized png
        is_candidate = base in wanted
        try:
            data = zf.read(m)
        except Exception:
            continue
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception:
            w = h = 0
        pixels = w * h
        # Only consider icons that look like app icons (skip tiny glyphs).
        if pixels < 1024 and not is_candidate:
            continue
        scored.append((pixels, -len(data), m))

    if not scored:
        return None
    scored.sort(reverse=True)
    best_member = scored[0][2]
    try:
        data = zf.read(best_member)
    except Exception:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    out_path.write_bytes(data)
    return str(out_path)


def hash_file(path: str | Path, alg: str = "sha256") -> str:
    h = hashlib.new(alg)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_ipa_metadata(ipa_path: str | Path, icon_out_dir: str | Path | None = None) -> AppMeta:
    """Extract name/bundle/version/icon from an .ipa file.

    `icon_out_dir` (if given) receives the extracted icon PNG.
    """
    ipa_path = Path(ipa_path)
    meta = AppMeta(
        name=ipa_path.stem,
        bundle_identifier="",
        version="1.0",
        size=ipa_path.stat().st_size,
        source_ipa=ipa_path.name,
        sha1=hash_file(ipa_path, "sha1"),
        sha256=hash_file(ipa_path, "sha256"),
    )
    with zipfile.ZipFile(ipa_path) as zf:
        app_root = _find_app_root(zf)
        if not app_root:
            return meta
        plist_member = app_root + "Info.plist"
        plist = _read_plist_member(zf, plist_member)
        if isinstance(plist, dict):
            for k in _NAME_KEYS:
                if plist.get(k):
                    meta.name = str(plist[k])
                    break
            if plist.get(_BUNDLE_KEY):
                meta.bundle_identifier = str(plist[_BUNDLE_KEY])
            for k in _VERSION_KEYS:
                if plist.get(k):
                    meta.version = str(plist[k])
                    break
            if plist.get(_MIN_IOS_KEY):
                meta.min_ios = str(plist[_MIN_IOS_KEY])
        if icon_out_dir is not None:
            cands = _pick_icon_name(plist) if isinstance(plist, dict) else []
            meta.icon_path = _extract_icon(zf, app_root, cands, Path(icon_out_dir), ipa_path.stem)
    return meta


# ---------------------------------------------------------------------------
# Index mutation
# ---------------------------------------------------------------------------

def add_or_update_app(index: dict[str, Any], entry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Insert entry, or replace an existing one with the same bundle id.

    Returns (index, is_new).
    """
    apps: list[dict] = index.setdefault("apps", [])
    bid = entry.get("bundleIdentifier")
    for i, existing in enumerate(apps):
        if existing.get("bundleIdentifier") == bid:
            apps[i] = entry
            return index, False
    apps.append(entry)
    return index, True


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "AppMeta", "new_index", "load_index", "save_index", "validate_index",
    "extract_ipa_metadata", "hash_file", "add_or_update_app", "now_iso",
    "DEFAULT_INDEX_NAME", "ICON_DIR_NAME", "IPA_DIR_NAME",
]


# ---------------------------------------------------------------------------
# Quick self-test when run directly
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    d = Path(tempfile.mkdtemp())
    idx = new_index("Test Repo")
    print("new index:", idx["name"], len(idx["apps"]))
    e = AppMeta(name="Demo", bundle_identifier="com.demo.app", version="2.0",
                source_ipa="demo.ipa").to_entry("http://1.2.3.4:8080")
    print("entry:", e)
    add_or_update_app(idx, e)
    add_or_update_app(idx, dict(e, version="2.1"))
    print("apps after upsert:", len(idx["apps"]), idx["apps"][0]["version"])
    ok, errs = validate_index(idx)
    print("valid:", ok, errs)
    save_index(d / "esign_source.json", idx)
    loaded = load_index(d / "esign_source.json")
    print("round-trip apps:", len(loaded["apps"]))
