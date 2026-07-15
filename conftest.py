"""pytest configuration for the esign-tools suite.

- Makes the tool modules importable from tests/ (parent dir on sys.path).
- Forces the Qt platform to 'offscreen' so GUI tests run headless (no display).
- Provides a `make_ipa` fixture that builds a minimal, valid .ipa file.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import plistlib
import pytest
from PIL import Image

# Headless Qt for CI / no-display environments.
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).parent
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))


def _build_ipa(path: Path, name: str = "Demo", bundle_id: str = "com.demo.app",
               version: str = "1.0", icon: bool = True) -> Path:
    """Create a minimal valid .ipa (zip) with a Payload/*.app/Info.plist + icon."""
    info = {
        "CFBundleDisplayName": name,
        "CFBundleName": name,
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "CFBundleIconName": "AppIcon",
        "CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon"]}},
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"Payload/{name}.app/Info.plist", plistlib.dumps(info))
        if icon:
            buf = io.BytesIO()
            # 120x120 red PNG — large enough to be selected as an app icon.
            Image.new("RGBA", (120, 120), (220, 40, 60, 255)).save(buf, "PNG")
            z.writestr(f"Payload/{name}.app/AppIcon.png", buf.getvalue())
    return path


@pytest.fixture
def make_ipa(tmp_path: Path):
    def _make(filename: str = "demo.ipa", **kwargs) -> Path:
        return _build_ipa(tmp_path / filename, **kwargs)
    return _make
