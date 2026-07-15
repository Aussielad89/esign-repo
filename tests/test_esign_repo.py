"""Test suite for esign_repo.py — the shared ESign index core."""
from __future__ import annotations

import json

import esign_repo as repo


def test_new_index_shape():
    idx = repo.new_index("My Repo")
    assert idx["name"] == "My Repo"
    assert idx["apps"] == []
    assert idx["version"] == 1
    assert idx["identifier"]


def test_load_missing_returns_fresh(tmp_path):
    idx = repo.load_index(tmp_path / "no_such.json")
    assert idx["apps"] == []


def test_load_corrupt_keeps_backup(tmp_path):
    p = tmp_path / "esign_source.json"
    p.write_text("{ this is : not json", encoding="utf-8")
    idx = repo.load_index(p)
    assert idx["apps"] == []
    assert (tmp_path / "esign_source.json.corrupt.bak").exists()


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "esign_source.json"
    idx = repo.new_index("RT")
    repo.save_index(p, idx)
    loaded = repo.load_index(p)
    assert loaded["name"] == "RT"
    # atomic tmp removed
    assert not p.with_suffix(".json.tmp").exists()


def test_validate_ok_and_bad():
    good = repo.new_index()
    good["apps"] = [{"name": "A", "bundleIdentifier": "com.a", "downloadURL": "x"}]
    ok, errs = repo.validate_index(good)
    assert ok and not errs

    bad = repo.new_index()
    bad["apps"] = [{"name": "A"},
                   {"bundleIdentifier": "com.a", "downloadURL": "x"},
                   {"name": "B", "bundleIdentifier": "com.a", "downloadURL": "x"}]
    ok2, errs2 = repo.validate_index(bad)
    assert not ok2
    assert any("missing bundleIdentifier" in e for e in errs2)
    assert any("missing name" in e for e in errs2)
    assert any("duplicate" in e for e in errs2)


def test_add_or_update_upsert():
    idx = repo.new_index()
    e = {"name": "A", "bundleIdentifier": "com.a", "downloadURL": "x"}
    _, is_new = repo.add_or_update_app(idx, e)
    assert is_new
    _, is_new2 = repo.add_or_update_app(idx, dict(e, name="A2"))
    assert not is_new2
    assert len(idx["apps"]) == 1
    assert idx["apps"][0]["name"] == "A2"


def test_appmeta_to_entry(make_ipa, tmp_path):
    ipa = make_ipa(name="Demo", bundle_id="com.demo", version="2.3")
    meta = repo.extract_ipa_metadata(ipa, icon_out_dir=tmp_path)
    assert meta.name == "Demo"
    assert meta.bundle_identifier == "com.demo"
    assert meta.version == "2.3"
    assert meta.icon_path and (tmp_path / f"{ipa.stem}.png").exists()
    entry = meta.to_entry("http://10.0.0.5:8080")
    assert entry["name"] == "Demo"
    assert entry["bundleIdentifier"] == "com.demo"
    assert entry["version"] == "2.3"
    assert entry["iconURL"] == "http://10.0.0.5:8080/icons/demo.png"
    assert entry["downloadURL"].endswith("/ipas/demo.ipa")


def test_extract_ipa_metadata_no_icon(make_ipa, tmp_path):
    ipa = make_ipa(icon=False)
    meta = repo.extract_ipa_metadata(ipa, icon_out_dir=tmp_path)
    assert meta.bundle_identifier
    assert meta.icon_path is None


def test_extract_ipa_metadata_missing_bundle(make_ipa, tmp_path):
    # Build an ipa without a bundle id to verify graceful fallback.
    ipa = make_ipa(bundle_id="")
    meta = repo.extract_ipa_metadata(ipa, icon_out_dir=tmp_path)
    assert meta.bundle_identifier == ""
    assert meta.name  # file stem fallback
