# esign-repo

Self-contained Python toolkit for running your **own offline ESign app source**
on Windows — plus a stdlib-only LAN scanner. Three tools, one shared index format.

```
┌─────────────────────────────────────────────────────────────┐
│  ESign Repo Builder                          ● 192.168.1.106:8080 │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│   ┌─────────────────────────────────────────────────────┐  │
│   │        ⬇  Drop an .ipa here  (or click to browse) │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                               │
│   🟢  ┌──────────────┐  Name      [ ProtonVPN          ]  │
│       │   (icon)     │  Bundle ID  [ com.protonvpn.ios ]  │
│       └──────────────┘  Version    [ 4.2.1              ]  │
│                           (edit any field — e.g. append .clone)│
│                                                               │
│   [  +  Add to Repo  ]   [  Start Server  ]                │
│                                                               │
│   Source URL:  http://192.168.1.106:8080/esign_source.json │
└─────────────────────────────────────────────────────────────┘
```

## Tools

| File / script | Task | Deps |
|----------------|------|------|
| `esign_watcher.py` (`esign-watcher`) | Watches a folder for new `.ipa` files, extracts metadata, maintains `esign_source.json`, serves it + the IPAs over HTTP :8080 | stdlib + Pillow (+ optional `watchdog`) |
| `esign_gui.py` (`esign-gui`) | Dark-themed PySide6 desktop app: drag-drop a `.ipa`, edit name/Bundle ID, "Add to Repo" | PySide6, Pillow |
| `netscan.py` (`esign-netscan`) | Multi-threaded LAN subnet scanner (auto-detects adapter, pings all hosts <10s, resolves MAC/vendor/hostname) | **stdlib only** |

Both repo tools share `esign_repo.py` (the index format + IPA metadata
extraction), so the watcher and the GUI produce a byte-compatible
`esign_source.json`.

## Install

From source (editable, gives you the `esign-watcher` / `esign-gui` / `esign-netscan` commands):

```bat
pip install -e .
```

Or just the raw deps and run the scripts directly:

```bat
pip install pyside6 pillow watchdog
REM netscan.py needs nothing extra (pure stdlib)
```

## 1 — Automated watcher + HTTP server

```bat
esign-watcher                          REM watch C:/Sideload/IPAs :8080
esign-watcher --dir D:/IPAs --port 9000
esign-watcher --once                   REM one-shot scan, then exit
python esign_watcher.py               REM same, without installing
```

On your phone, in ESign, add the source:

```
http://<YOUR-PC-IP>:8080/esign_source.json
```

Find `<YOUR-PC-IP>` with `esign-netscan` (printed at the top) or `ipconfig`.

### Run as a background service

- **Easiest (no admin):** copy `run_background.bat` into your Startup folder
  (`Win+R` → `shell:startup`). Launches the watcher minimized on login.
- **Proper service:** run `install_service.bat` **as Administrator** (needs
  [nssm](https://nssm.cc) — `winget install nssm`). Creates an auto-start
  Windows service "Esign Repo Watcher".

## 2 — GUI repo builder

```bat
esign-gui
esign-gui --repo D:/MyRepo --host 0.0.0.0 --port 8080
python esign_gui.py
```

Drag a `.ipa` onto the drop zone. Name / Bundle ID / Version auto-fill from
the file — edit them (e.g. append `.clone` to the Bundle ID to clone an app),
then hit **Add to Repo**. The IPA is stored, its icon extracted, and the entry
is upserted into `esign_source.json`. Click **Start Server** to serve it, then
copy the Source URL into ESign.

## 3 — LAN scanner

```bat
esign-netscan                REM auto-detect subnet, scan it
esign-netscan -t 10.0.0.0/24
esign-netscan --json         REM machine-readable
esign-netscan -w 200        REM more worker threads
python netscan.py
```

> MAC/vendor resolution on Windows parses `arp -a` (best-effort) and matches
> the OUI prefix against a small built-in vendor table. Hostnames come from
> reverse-DNS where available.

## Tests

```bat
pip install pytest pytest-qt
python -m pytest tests/ -q
```

29 tests cover metadata extraction, index upsert, HTTP serving (incl. path
traversal block), GUI logic (headless `offscreen` Qt), and scanner parsing.

## Index format (`esign_source.json`)

```json
{
  "name": "Local ESign Repo",
  "identifier": "com.local.esignrepo",
  "version": 1,
  "apps": [
    {
      "name": "App Name",
      "bundleIdentifier": "com.developer.app",
      "version": "1.0",
      "size": 12345,
      "downloadURL": "http://192.168.1.106:8080/ipas/app.ipa",
      "iconURL": "http://192.168.1.106:8080/icons/app.png",
      "sha1": "...", "sha256": "..."
    }
  ]
}
```

## License

MIT — see [LICENSE](LICENSE).
