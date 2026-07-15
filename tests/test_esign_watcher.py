"""Test suite for esign_watcher.py — Task 1 (extraction + HTTP serving)."""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

import esign_repo as repo
import esign_watcher as watcher


@pytest.fixture
def repo_dir(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    return d


def test_detect_lan_ip_returns_ip():
    ip = watcher.detect_lan_ip()
    assert ip.count(".") == 3


def test_repo_manager_indexes_existing(repo_dir, make_ipa):
    make_ipa(name="App1", bundle_id="com.app1", version="1.0", filename="app1.ipa")
    make_ipa(name="App2", bundle_id="com.app2", version="1.2", filename="app2.ipa")
    # move them into the watched repo dir (user drops them here)
    import shutil
    shutil.move(str(repo_dir.parent / "app1.ipa"), str(repo_dir / "app1.ipa"))
    shutil.move(str(repo_dir.parent / "app2.ipa"), str(repo_dir / "app2.ipa"))
    cfg = watcher.WatcherConfig(repo_dir=repo_dir)
    mgr = watcher.RepoManager(cfg)
    n, u = mgr.index_existing_ipas()
    assert n == 2 and u == 0
    idx = repo.load_index(cfg.index_path)
    assert len(idx["apps"]) == 2
    assert any(a["bundleIdentifier"] == "com.app1" for a in idx["apps"])
    # IPAs were ingested into ipas/ (so /ipas/<name> download URL resolves)
    assert (repo_dir / "ipas" / "app1.ipa").exists()
    # and removed from the root so it stays clean
    assert not (repo_dir / "app1.ipa").exists()


def test_repo_manager_skips_no_bundle(repo_dir, make_ipa, capsys):
    make_ipa(bundle_id="", filename="broken.ipa")
    import shutil
    shutil.move(str(repo_dir.parent / "broken.ipa"), str(repo_dir / "broken.ipa"))
    cfg = watcher.WatcherConfig(repo_dir=repo_dir)
    mgr = watcher.RepoManager(cfg)
    n, u = mgr.index_existing_ipas()
    assert n == 0
    assert "no bundle id" in capsys.readouterr().out


def test_add_ipa_updates_existing(repo_dir, make_ipa):
    ipa = make_ipa(name="X", bundle_id="com.x", version="1.0")
    cfg = watcher.WatcherConfig(repo_dir=repo_dir)
    mgr = watcher.RepoManager(cfg)
    assert mgr.add_ipa(ipa) is True
    # update
    ipa2 = make_ipa(name="X", bundle_id="com.x", version="2.0", filename="x2.ipa")
    assert mgr.add_ipa(ipa2) is True
    idx = repo.load_index(cfg.index_path)
    assert len(idx["apps"]) == 1
    assert idx["apps"][0]["version"] == "2.0"


def test_watcherconfig_base_url():
    cfg = watcher.WatcherConfig(repo_dir=None, host="0.0.0.0", port=8080)  # type: ignore[arg-type]
    url = cfg.base_url()
    assert url.startswith("http://") and ":8080" in url


def _wait_for_port(host, port, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def test_http_server_serves_index_and_ipa(repo_dir, make_ipa):
    ipa = make_ipa(name="Web", bundle_id="com.web", version="1.0")
    # copy ipa into repo ipas dir so it is served
    (repo_dir / "ipas").mkdir(exist_ok=True)
    import shutil
    shutil.copy2(ipa, repo_dir / "ipas" / "web.ipa")

    cfg = watcher.WatcherConfig(repo_dir=repo_dir, host="127.0.0.1", port=0)
    mgr = watcher.RepoManager(cfg)
    mgr.add_ipa(repo_dir / "ipas" / "web.ipa")

    # bind to an ephemeral port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg.port = port

    t = threading.Thread(target=watcher.serve_forever, args=(cfg,), daemon=True)
    t.start()
    assert _wait_for_port("127.0.0.1", port)

    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/esign_source.json", timeout=3) as r:
        data = json.loads(r.read())
    assert any(a["bundleIdentifier"] == "com.web" for a in data["apps"])

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ipas/web.ipa", timeout=3) as r:
        assert len(r.read()) > 0
    # path traversal blocked
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/../escape.txt", timeout=3)
        assert False, "traversal should 404/403"
    except Exception:
        pass
