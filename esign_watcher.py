"""esign_watcher.py — Task 1: automated ESign repo builder & local server.

Watches a folder (default C:/Sideload/IPAs) for new .ipa files, extracts
their metadata (name, bundle id, version, icon) via esign_repo.py, and
maintains a local esign_source.json index. Serves that index + the IPAs
over HTTP on port 8080 so ESign on your phone can use it as a source.

Run:
    python esign_watcher.py                      # watch C:/Sideload/IPAs :8080
    python esign_watcher.py --dir D:/IPAs --port 9000 --host 0.0.0.0
    python esign_watcher.py --once              # one-shot scan, then exit

Background service:
    see install_service.bat (registers an nssm-based service) or just run
    `python esign_watcher.py` from a Startup shortcut.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import esign_repo as repo

try:  # optional, faster & event-driven; falls back to polling
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    _HAVE_WATCHDOG = True
except Exception:  # pragma: no cover - optional dep
    _HAVE_WATCHDOG = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class WatcherConfig:
    repo_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    poll_interval: float = 2.0
    use_watchdog: bool = True
    rescan_existing: bool = True

    @property
    def index_path(self) -> Path:
        return self.repo_dir / repo.DEFAULT_INDEX_NAME

    @property
    def icons_dir(self) -> Path:
        return self.repo_dir / repo.ICON_DIR_NAME

    def base_url(self) -> str:
        """Public base URL. Bind host may be 0.0.0.0; for the index we use
        the LAN IP ESign will actually reach."""
        if self.host in ("0.0.0.0", ""):
            return f"http://{detect_lan_ip()}:{self.port}"
        return f"http://{self.host}:{self.port}"


# ---------------------------------------------------------------------------
# LAN IP detection (for building correct download URLs)
# ---------------------------------------------------------------------------

def detect_lan_ip() -> str:
    """Best-effort local LAN IPv4 (the address ESign on the phone uses)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


# ---------------------------------------------------------------------------
# Repo maintenance
# ---------------------------------------------------------------------------

class RepoManager:
    """Rebuilds / updates the esign_source.json index from .ipa files."""

    def __init__(self, cfg: WatcherConfig) -> None:
        self.cfg = cfg

    def index_existing_ipas(self) -> tuple[int, int]:
        """Ingest + index every .ipa currently in repo_dir (idempotent upsert).

        IPAs dropped at the repo root are moved into the ipas/ store so the
        index's /ipas/<name> download URL resolves on the HTTP server. IPAs
        already inside ipas/ are indexed in place.
        """
        self.cfg.icons_dir.mkdir(exist_ok=True)
        ipas_dir = self.cfg.repo_dir / repo.IPA_DIR_NAME
        ipas_dir.mkdir(exist_ok=True)
        # Move any stray IPAs from the root into the ipas/ store.
        for ipa in sorted(self.cfg.repo_dir.glob("*.ipa")):
            dest = ipas_dir / ipa.name
            if ipa.resolve() != dest.resolve():
                shutil.move(str(ipa), str(dest))
        index = repo.load_index(self.cfg.index_path)
        new = updated = 0
        base = self.cfg.base_url()
        for ipa in sorted(ipas_dir.glob("*.ipa")):
            meta = repo.extract_ipa_metadata(ipa, icon_out_dir=self.cfg.icons_dir)
            if not meta.bundle_identifier:
                print(f"  ! skip {ipa.name}: no bundle id in Info.plist", flush=True)
                continue
            entry = meta.to_entry(base)
            index, is_new = repo.add_or_update_app(index, entry)
            new += int(is_new)
            updated += int(not is_new)
        repo.save_index(self.cfg.index_path, index)
        return new, updated

    def add_ipa(self, ipa_path: Path) -> bool:
        """Ingest (move into ipas/) + index a single .ipa, atomic index write."""
        ipa_path = Path(ipa_path)
        if ipa_path.suffix.lower() != ".ipa" or not ipa_path.exists():
            return False
        ipas_dir = self.cfg.repo_dir / repo.IPA_DIR_NAME
        ipas_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.icons_dir.mkdir(parents=True, exist_ok=True)
        dest = ipas_dir / ipa_path.name
        if ipa_path.resolve() != dest.resolve():
            shutil.move(str(ipa_path), str(dest))
        ipa_path = dest
        meta = repo.extract_ipa_metadata(ipa_path, icon_out_dir=self.cfg.icons_dir)
        if not meta.bundle_identifier:
            print(f"  ! skip {ipa_path.name}: no bundle id in Info.plist", flush=True)
            return False
        index = repo.load_index(self.cfg.index_path)
        entry = meta.to_entry(self.cfg.base_url())
        index, is_new = repo.add_or_update_app(index, entry)
        repo.save_index(self.cfg.index_path, index)
        verb = "added" if is_new else "updated"
        print(f"  + {verb}: {meta.name} ({meta.bundle_identifier} {meta.version})", flush=True)
        ok, errs = repo.validate_index(index)
        if not ok:
            print(f"  ! index validation warnings: {errs}", flush=True)
        return True


# ---------------------------------------------------------------------------
# File watching
# ---------------------------------------------------------------------------

class _Handler(FileSystemEventHandler if _HAVE_WATCHDOG else object):
    def __init__(self, manager: RepoManager) -> None:
        super().__init__()
        self.manager = manager

    def on_created(self, event):  # watchdog signature
        if event.is_directory:
            return
        self._maybe_add(event.src_path)

    def on_moved(self, event):  # some editors move tmp -> final
        if event.is_directory:
            return
        self._maybe_add(event.dest_path)

    def _maybe_add(self, path: str) -> None:
        p = Path(path)
        # Wait briefly in case the copy is still in flight.
        for _ in range(10):
            if p.exists() and p.suffix.lower() == ".ipa":
                try:
                    if p.stat().st_size > 0:
                        break
                except OSError:
                    pass
            time.sleep(0.3)
        self.manager.add_ipa(p)


def start_watcher(cfg: WatcherConfig, manager: RepoManager):
    """Return an observer-like object with .stop()/.join() (or a polling stub)."""
    if _HAVE_WATCHDOG and cfg.use_watchdog:
        observer = Observer()
        observer.schedule(_Handler(manager), str(cfg.repo_dir), recursive=False)
        observer.start()
        return observer

    # Polling fallback: simple loop comparing the set of seen .ipa files.
    class _Poller:
        def __init__(self) -> None:
            self._seen = {p.name for p in cfg.repo_dir.glob("*.ipa")}
            self._stop = threading.Event()

        def run(self) -> None:
            while not self._stop.is_set():
                current = {p.name for p in cfg.repo_dir.glob("*.ipa")}
                for name in current - self._seen:
                    manager.add_ipa(cfg.repo_dir / name)
                self._seen = current
                self._stop.wait(cfg.poll_interval)

        def stop(self) -> None:
            self._stop.set()

        def join(self, timeout: float | None = None) -> None:
            pass

    poller = _Poller()
    t = threading.Thread(target=poller.run, daemon=True)
    t.start()
    poller._thread = t
    return poller


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class RepoHTTPHandler(BaseHTTPRequestHandler):
    # Set by serve_forever via closure-free class attr.
    root_dir: Path = Path(".")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url_path = self.path.split("?", 1)[0].lstrip("/")
        if url_path in ("", "index.json"):
            url_path = repo.DEFAULT_INDEX_NAME
        target = (self.root_dir / url_path).resolve()
        # Prevent path traversal outside the repo dir.
        try:
            target.relative_to(self.root_dir.resolve())
        except ValueError:
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if not target.exists():
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        if target.is_dir():
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        try:
            data = target.read_bytes()
        except OSError as e:
            self._send(500, str(e).encode(), "text/plain; charset=utf-8")
            return
        ctype = "application/json; charset=utf-8" if target.suffix == ".json" else "application/octet-stream"
        if target.suffix in (".ipa", ".png"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(200, data, ctype)

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        sys.stdout.write("  [http] " + (fmt % args) + "\n")


def serve_forever(cfg: WatcherConfig) -> None:
    RepoHTTPHandler.root_dir = cfg.repo_dir
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), RepoHTTPHandler)
    base = cfg.base_url()
    print(f"\n  ESign source URL: {base}/{repo.DEFAULT_INDEX_NAME}")
    print(f"  Serving folder   : {cfg.repo_dir}")
    print(f"  Bind             : {cfg.host}:{cfg.port}")
    print("  Press Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> WatcherConfig:
    repo_dir = Path(args.dir)
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / repo.ICON_DIR_NAME).mkdir(exist_ok=True)
    return WatcherConfig(
        repo_dir=repo_dir,
        host=args.host,
        port=args.port,
        poll_interval=args.poll,
        use_watchdog=args.no_watchdog is False,
        rescan_existing=not args.no_rescan,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Automated ESign repo builder & local HTTP server")
    parser.add_argument("--dir", default=r"C:/Sideload/IPAs", help="Folder to watch for .ipa files")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (0.0.0.0 = all interfaces)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--poll", type=float, default=2.0, help="Polling interval (seconds, used if watchdog unavailable)")
    parser.add_argument("--no-watchdog", action="store_true", help="Force polling mode even if watchdog is installed")
    parser.add_argument("--no-rescan", action="store_true", help="Do not index IPAs already present at startup")
    parser.add_argument("--once", action="store_true", help="Index once and exit (no server, no watch)")
    args = parser.parse_args(argv)

    cfg = build_config(args)
    manager = RepoManager(cfg)

    if args.once:
        n, u = manager.index_existing_ipas()
        print(f"Indexed: {n} new, {u} updated -> {cfg.index_path}")
        return 0

    if cfg.rescan_existing:
        print("Scanning existing IPAs...")
        n, u = manager.index_existing_ipas()
        print(f"  initial scan: {n} new, {u} updated")
    else:
        # ensure index exists
        if not cfg.index_path.exists():
            repo.save_index(cfg.index_path, repo.new_index())

    observer = start_watcher(cfg, manager)

    try:
        serve_forever(cfg)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        observer.stop()
        if hasattr(observer, "join"):
            observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
