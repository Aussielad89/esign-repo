"""Test suite for esign_gui.py — Task 2 (logic; GUI is exercised headless)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import esign_repo as repo
import esign_gui as gui


@pytest.fixture
def repo_dir(tmp_path):
    d = tmp_path / "gui_repo"
    d.mkdir()
    return d


def test_build_meta_from_fields(make_ipa):
    ipa = make_ipa(name="A", bundle_id="com.a", version="1.0")
    meta = gui.build_meta_from_fields("MyApp", "com.a.clone", "9.9", ipa)
    assert meta.name == "MyApp"
    assert meta.bundle_identifier == "com.a.clone"
    assert meta.version == "9.9"
    assert meta.source_ipa == ipa.name


def test_add_to_repo_copies_ipa_and_icons(repo_dir, make_ipa):
    ipa = make_ipa(name="G", bundle_id="com.g", version="1.0", filename="g.ipa")
    meta = gui.build_meta_from_fields("", "com.g", "1.0", ipa)
    ok, is_new = gui.add_to_repo(repo_dir, ipa, meta, "http://10.0.0.1:8080")
    assert ok and is_new
    assert (repo_dir / "ipas" / "g.ipa").exists()
    assert (repo_dir / "icons" / "g.png").exists()
    idx = repo.load_index(repo_dir / "esign_source.json")
    assert len(idx["apps"]) == 1
    assert idx["apps"][0]["downloadURL"].endswith("/ipas/g.ipa")
    assert idx["apps"][0]["iconURL"].endswith("/icons/g.png")


def test_add_to_repo_clone_upserts(repo_dir, make_ipa):
    ipa = make_ipa(name="G", bundle_id="com.g", version="1.0")
    gui.add_to_repo(repo_dir, ipa, gui.build_meta_from_fields("", "com.g", "1.0", ipa),
                    "http://x:8080")
    gui.add_to_repo(repo_dir, ipa, gui.build_meta_from_fields("G Clone", "com.g.clone", "1.0", ipa),
                    "http://x:8080")
    idx = repo.load_index(repo_dir / "esign_source.json")
    assert len(idx["apps"]) == 2


def test_add_to_repo_rejects_non_ipa(repo_dir, tmp_path):
    not_ipa = tmp_path / "x.txt"
    not_ipa.write_text("nope")
    ok, _ = gui.add_to_repo(repo_dir, not_ipa, gui.build_meta_from_fields("x", "com.x", "1", not_ipa),
                            "http://x:8080")
    assert ok is False


def test_mainwindow_builds(qtbot, repo_dir):
    if "PySide6" not in sys.modules:
        pytest.skip("PySide6 not importable")
    w = gui.MainWindow(repo_dir, "0.0.0.0", 8080)
    qtbot.addWidget(w)
    assert w.windowTitle() == "Esign Repo Builder"
    assert w.add_btn.isEnabled() is False  # nothing loaded yet
    assert "esign_source.json" in w.url_edit.text()


def test_mainwindow_loads_ipa(qtbot, repo_dir, make_ipa):
    if "PySide6" not in sys.modules:
        pytest.skip("PySide6 not importable")
    w = gui.MainWindow(repo_dir, "0.0.0.0", 8080)
    qtbot.addWidget(w)
    ipa = make_ipa(name="Loaded", bundle_id="com.loaded", version="3.1")
    w.on_ipa(str(ipa))
    assert w.name_edit.text() == "Loaded"
    assert w.bid_edit.text() == "com.loaded"
    assert w.ver_edit.text() == "3.1"
    assert w.add_btn.isEnabled() is True


def test_mainwindow_add_flow(qtbot, repo_dir, make_ipa):
    if "PySide6" not in sys.modules:
        pytest.skip("PySide6 not importable")
    w = gui.MainWindow(repo_dir, "0.0.0.0", 8080)
    qtbot.addWidget(w)
    ipa = make_ipa(name="Addme", bundle_id="com.addme", version="1.0")
    w.on_ipa(str(ipa))
    w.ver_edit.setText("2.0")
    w.on_add()
    # after add, fields clear and button disabled
    assert w.add_btn.isEnabled() is False
    idx = repo.load_index(repo_dir / "esign_source.json")
    assert any(a["bundleIdentifier"] == "com.addme" and a["version"] == "2.0"
               for a in idx["apps"])
