"""esign_gui.py — Task 2: offline ESign repo builder (PySide6).

A clean, dark-themed desktop GUI: drag a .ipa onto the drop zone, see the
extracted App Name / Bundle ID / Version pre-filled (editable), tweak the
Bundle ID (e.g. append `.clone`), hit "Add to Repo" — the IPA is copied into
the repo, its icon extracted, and the entry upserted into esign_source.json.

It reuses esign_repo.py so the output is byte-for-byte compatible with the
watcher (esign_watcher.py) and the HTTP server.

Run:
    python esign_gui.py
    python esign_gui.py --repo D:/MyRepo      # custom repo folder
    python esign_gui.py --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

import esign_repo as repo
import esign_watcher as watcher  # for WatcherConfig + detect_lan_ip

from PySide6.QtCore import Qt, QSize, QUrl, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QFileDialog, QSizePolicy, QMessageBox, QProgressBar,
)
from PySide6.QtCore import Signal


# ---------------------------------------------------------------------------
# Repo interaction (kept outside the GUI widget so it is unit-testable)
# ---------------------------------------------------------------------------

def build_meta_from_fields(name: str, bundle: str, version: str,
                           ipa_path: Path, size: int = 0,
                           sha1: str = "", sha256: str = "") -> repo.AppMeta:
    """Construct an AppMeta from GUI-edited fields."""
    return repo.AppMeta(
        name=name.strip() or ipa_path.stem,
        bundle_identifier=bundle.strip(),
        version=version.strip() or "1.0",
        size=size,
        sha1=sha1,
        sha256=sha256,
        source_ipa=ipa_path.name,
    )


def add_to_repo(repo_dir: Path, ipa_path: Path, meta: repo.AppMeta,
                base_url: str) -> tuple[bool, bool]:
    """Copy the IPA into repo_dir/ipas, extract icon, upsert index.

    Returns (ok, is_new).
    """
    if not ipa_path.exists() or ipa_path.suffix.lower() != ".ipa":
        return False, False
    repo_dir.mkdir(parents=True, exist_ok=True)
    ipas_dir = repo_dir / repo.IPA_DIR_NAME
    icons_dir = repo_dir / repo.ICON_DIR_NAME
    ipas_dir.mkdir(exist_ok=True)
    icons_dir.mkdir(exist_ok=True)

    dest = ipas_dir / ipa_path.name
    import shutil
    if ipa_path.resolve() != dest.resolve():
        shutil.copy2(ipa_path, dest)

    # Re-extract icon from the (possibly cloned) ipa into the repo icons dir.
    full = repo.extract_ipa_metadata(dest, icon_out_dir=icons_dir)
    meta.icon_path = full.icon_path
    meta.size = dest.stat().st_size
    meta.source_ipa = dest.name

    index = repo.load_index(repo_dir / repo.DEFAULT_INDEX_NAME)
    entry = meta.to_entry(base_url)
    index, is_new = repo.add_or_update_app(index, entry)
    repo.save_index(repo_dir / repo.DEFAULT_INDEX_NAME, index)
    return True, is_new


# ---------------------------------------------------------------------------
# Dark theme
# ---------------------------------------------------------------------------

DARK_STYLE = """
QWidget {
    background-color: #14161a;
    color: #e6e9ef;
    font-family: 'Segoe UI', 'San Francisco', 'Helvetica Neue', Arial, sans-serif;
}
QFrame#drop {
    background-color: #1c1f26;
    border: 2px dashed #3a4150;
    border-radius: 12px;
}
QFrame#drop.hover {
    border: 2px dashed #5b8def;
    background-color: #202634;
}
QLabel#dropLabel {
    color: #8a93a6;
    font-size: 15px;
}
QLabel#title { font-size: 20px; font-weight: 600; color: #ffffff; }
QLabel#subtitle { color: #8a93a6; font-size: 12px; }
QLabel#field { color: #aab2c2; font-size: 12px; }
QLineEdit {
    background-color: #1c1f26;
    border: 1px solid #2c313c;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    color: #e6e9ef;
}
QLineEdit:focus { border: 1px solid #5b8def; }
QPushButton {
    background-color: #2d6cdf;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background-color: #3a7bee; }
QPushButton:disabled { background-color: #2c313c; color: #6b7488; }
QPushButton#ghost {
    background-color: #23272f;
    color: #c7cedb;
    border: 1px solid #2c313c;
}
QPushButton#ghost:hover { background-color: #2c313c; }
QPushButton#accent { background-color: #2eae6b; }
QPushButton#accent:hover { background-color: #36c47c; }
QTextEdit, QPlainTextEdit {
    background-color: #11141a;
    border: 1px solid #2c313c;
    border-radius: 8px;
    color: #9fe6b8;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
}
"""


# ---------------------------------------------------------------------------
# Drop zone
# ---------------------------------------------------------------------------

class DropZone(QFrame):
    ipa_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self.label = QLabel("Drag & drop a .ipa here\nor click to browse")
        self.label.setObjectName("dropLabel")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("class", "hover")
            self.setStyleSheet("")  # force re-eval of stylesheet
            self.label.setText("Release to load .ipa")
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.label.setText("Drag & drop a .ipa here\nor click to browse")
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                p = Path(u.toLocalFile())
                if p.suffix.lower() == ".ipa":
                    self.ipa_dropped.emit(str(p))
                    break
        self.label.setText("Drag & drop a .ipa here\nor click to browse")
        event.acceptProposedAction()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        path, _ = QFileDialog.getOpenFileName(
            self, "Select .ipa file", "", "IPA files (*.ipa)")
        if path:
            self.ipa_dropped.emit(path)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QWidget):
    def __init__(self, repo_dir: Path, host: str, port: int) -> None:
        super().__init__()
        self.repo_dir = Path(repo_dir)
        self.host = host
        self.port = port
        self.current_ipa: Path | None = None
        self.server_thread: threading.Thread | None = None
        self._build()

    # ---- layout ----
    def _build(self) -> None:
        self.setWindowTitle("Esign Repo Builder")
        self.setMinimumWidth(520)
        self.resize(620, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Header
        header = QVBoxLayout()
        t = QLabel("Esign Repo Builder")
        t.setObjectName("title")
        s = QLabel("Offline .ipa repository for ESign — drag, edit, add.")
        s.setObjectName("subtitle")
        header.addWidget(t)
        header.addWidget(s)
        root.addLayout(header)

        # Drop zone
        self.drop = DropZone()
        self.drop.ipa_dropped.connect(self.on_ipa)
        root.addWidget(self.drop)

        # Icon preview + fields
        mid = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(72, 72)
        self.icon_label.setStyleSheet("border:1px solid #2c313c;border-radius:10px;background:#1c1f26;")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setText("")
        mid.addWidget(self.icon_label)

        fields = QVBoxLayout()
        fields.setSpacing(8)
        name_row = QVBoxLayout()
        l1 = QLabel("App Name"); l1.setObjectName("field")
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("App Name")
        name_row.addWidget(l1); name_row.addWidget(self.name_edit)

        bid_row = QVBoxLayout()
        l2 = QLabel("Bundle ID  (append .clone to clone an app)")
        l2.setObjectName("field")
        self.bid_edit = QLineEdit(); self.bid_edit.setPlaceholderText("com.developer.app")
        bid_row.addWidget(l2); bid_row.addWidget(self.bid_edit)

        ver_row = QVBoxLayout()
        l3 = QLabel("Version"); l3.setObjectName("field")
        self.ver_edit = QLineEdit("1.0")
        ver_row.addWidget(l3); ver_row.addWidget(self.ver_edit)

        fields.addLayout(name_row); fields.addLayout(bid_row); fields.addLayout(ver_row)
        mid.addLayout(fields, 1)
        root.addLayout(mid)

        # Buttons
        btns = QHBoxLayout()
        self.add_btn = QPushButton("Add to Repo")
        self.add_btn.setObjectName("accent")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.on_add)
        self.serve_btn = QPushButton("Start Server")
        self.serve_btn.setObjectName("ghost")
        self.serve_btn.clicked.connect(self.on_serve_toggle)
        btns.addWidget(self.add_btn, 1)
        btns.addWidget(self.serve_btn, 1)
        root.addLayout(btns)

        # Source URL
        url_row = QHBoxLayout()
        l4 = QLabel("Source URL"); l4.setObjectName("field")
        self.url_edit = QLineEdit(self._source_url())
        self.url_edit.setReadOnly(True)
        copy_btn = QPushButton("Copy"); copy_btn.setObjectName("ghost")
        copy_btn.clicked.connect(self.on_copy)
        url_row.addWidget(l4)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(copy_btn)
        root.addLayout(url_row)

        # Status log
        self.log = QLabel("Ready. Drop an .ipa to begin.")
        self.log.setObjectName("subtitle")
        self.log.setWordWrap(True)
        root.addWidget(self.log)

        root.addStretch(1)

    # ---- helpers ----
    def _base_url(self) -> str:
        if self.host in ("0.0.0.0", ""):
            return f"http://{watcher.detect_lan_ip()}:{self.port}"
        return f"http://{self.host}:{self.port}"

    def _source_url(self) -> str:
        return f"{self._base_url()}/{repo.DEFAULT_INDEX_NAME}"

    # ---- slots ----
    def on_ipa(self, path: str) -> None:
        ipa = Path(path)
        if ipa.suffix.lower() != ".ipa" or not ipa.exists():
            self.log.setText(f"Not a valid .ipa: {path}")
            return
        self.current_ipa = ipa
        try:
            meta = repo.extract_ipa_metadata(ipa)  # no icon write here
        except Exception as e:  # noqa: BLE001
            self.log.setText(f"Failed to read {ipa.name}: {e}")
            return
        self.name_edit.setText(meta.name)
        self.bid_edit.setText(meta.bundle_identifier)
        self.ver_edit.setText(meta.version)
        self.add_btn.setEnabled(bool(meta.bundle_identifier))
        # Quick icon preview from a temporary extraction.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            prev = repo.extract_ipa_metadata(ipa, icon_out_dir=Path(td))
            if prev.icon_path and Path(prev.icon_path).exists():
                pm = QPixmap(prev.icon_path).scaled(
                    72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.icon_label.setPixmap(pm)
        self.log.setText(
            f"Loaded {ipa.name}  •  name={meta.name}  •  id={meta.bundle_identifier}  •  v{meta.version}")

    def on_add(self) -> None:
        if not self.current_ipa:
            return
        meta = build_meta_from_fields(
            self.name_edit.text(), self.bid_edit.text(), self.ver_edit.text(),
            self.current_ipa)
        ok, is_new = add_to_repo(self.repo_dir, self.current_ipa, meta, self._base_url())
        if ok:
            verb = "Added" if is_new else "Updated"
            self.log.setText(f"{verb} {meta.name} → {self._source_url()}")
            self.add_btn.setEnabled(False)
            self.icon_label.clear()
            self.current_ipa = None
        else:
            self.log.setText("Add failed — check the .ipa file.")

    def on_copy(self) -> None:
        QApplication.clipboard().setText(self.url_edit.text())
        self.log.setText("Source URL copied to clipboard.")

    def on_serve_toggle(self) -> None:
        if self.server_thread and self.server_thread.is_alive():
            self.log.setText("Server already running on a background thread.")
            return
        import esign_watcher as watcher
        cfg = watcher.WatcherConfig(
            repo_dir=self.repo_dir, host=self.host, port=self.port,
            use_watchdog=False, rescan_existing=False)
        # Make sure index exists.
        if not (self.repo_dir / repo.DEFAULT_INDEX_NAME).exists():
            repo.save_index(self.repo_dir / repo.DEFAULT_INDEX_NAME, repo.new_index())
        t = threading.Thread(target=watcher.serve_forever, args=(cfg,), daemon=True)
        t.start()
        self.server_thread = t
        self.serve_btn.setText("Server Running")
        self.serve_btn.setEnabled(False)
        self.log.setText(f"Serving {self._source_url()}")


# ---------------------------------------------------------------------------
# CLI / bootstrap (DPI aware)
# ---------------------------------------------------------------------------

def build_app(repo_dir: Path, host: str, port: int) -> QApplication:
    # High-DPI aware on Windows.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Esign Repo Builder")
    app.setStyleSheet(DARK_STYLE)
    # Scale base font with system DPI so layout stays proportional.
    base = app.font()
    base.setPointSize(base.pointSize() + 1)
    app.setFont(base)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Esign Repo Builder GUI")
    parser.add_argument("--repo", default=str(Path.home() / "EsignRepo"),
                        help="Repo folder (holds esign_source.json + ipas/ + icons/)")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    args = parser.parse_args(argv)

    app = build_app(Path(args.repo), args.host, args.port)
    window = MainWindow(Path(args.repo), args.host, args.port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
