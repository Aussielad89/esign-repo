# Architecture

How the three tools fit together and the contract they share.

## Shared core: `esign_repo.py`

Both repo tools (`esign_watcher.py`, `esign_gui.py`) import `esign_repo`.
It owns:

- **The index format** (`esign_source.json`) — `new_index()`, `load_index()`,
  `save_index()`, `validate_index()`.
- **IPA metadata extraction** — `extract_ipa_metadata()` reads the `.ipa`
  (a zip), locates `Payload/<App>.app/Info.plist`, parses it, and pulls the
  best-looking PNG icon out with Pillow.
- **Upsert logic** — `add_or_update_app()` dedupes by `bundleIdentifier`,
  so re-adding the same app upgrades it in place instead of duplicating.

Everything in `esign_repo` is stdlib + Pillow only: no network, no GUI.
That keeps the index logic unit-testable and guarantees the watcher and GUI
produce a **byte-compatible** `esign_source.json`.

## Repo directory layout

```
<repo>/
├── esign_source.json     # the index ESign consumes
├── ipas/                # the actual .ipa files (served at /ipas/<name>)
└── icons/              # extracted PNG icons (served at /icons/<name>.png)
```

The HTTP server serves `<repo>` at its root, so:

- index URL  → `http://HOST:PORT/esign_source.json`
- app download → `http://HOST:PORT/ipas/<name>.ipa`
- app icon    → `http://HOST:PORT/icons/<name>.png`

> Path traversal (`/../`) is rejected — the handler resolves the path inside
> the repo root and 404s anything that escapes.

## Data flow

```
            .ipa dropped                         user drags .ipa
                 │                                      │
                 ▼                                      ▼
        esign_watcher.py                       esign_gui.py
                 │  extract_ipa_metadata()             │  extract_ipa_metadata()
                 │  (move into ipas/)                  │  (copy into ipas/)
                 │  extract icon → icons/             │  extract icon → icons/
                 └──────────────┬───────────────────────┘
                                ▼
                       esign_repo.add_or_update_app()
                       esign_repo.save_index()   ← atomic write
                                │
                                ▼
                  esign_source.json  (served on :8080)
                                │
                                ▼
                    ESign (iOS) → "Add Source"
```

## Index entry contract

`esign_source.json.apps[]` each carry:

| Field | Source | Notes |
|-------|--------|-------|
| `name` | `CFBundleDisplayName` → `CFBundleName` | falls back to the file stem |
| `bundleIdentifier` | `CFBundleIdentifier` | **dedupe key**; empty → IPA skipped |
| `version` | `CFBundleShortVersionString` → `CFBundleVersion` | defaults `"1.0"` |
| `size` | `os.stat().st_size` of the stored IPA | bytes |
| `downloadURL` | `base_url + /ipas/<name>.ipa` | |
| `iconURL` | `base_url + /icons/<name>.png` | omitted if no icon found |
| `sha1` / `sha256` | streamed hash of the IPA | integrity / change detection |
| `minOSVersion` | `MinimumOSVersion` | included when present |
| `localizedDescription` | `description` field | included when present |

`base_url` is the watcher/GUI's `--host`/`--port` rendered as
`http://<LAN-IP>:<port>` (for `0.0.0.0` it resolves the LAN IP with
`detect_lan_ip()`).

## `netscan.py` (standalone)

Pure stdlib. No shared state with the repo tools.

1. `detect_subnet()` runs `ipconfig`, parses the IPv4 + subnet mask, skips
   loopback (`127.*`) and APIPA (`169.254.*`), and picks the first real
   adapter. Falls back to `--target` if given.
2. `scan()` fans out one ICMP ping per host across a `ThreadPoolExecutor`
   (default 150 workers) so a /24 finishes in a few seconds.
3. `parse_arp()` reads `arp -a` for MACs, `oui_vendor()` maps the OUI
   prefix to a vendor from a small built-in table, and a best-effort
   reverse-DNS pass fills hostnames.

> Windows `arp -a` is best-effort (entries age out). Hosts that don't
> answer ping (firewalled) still show if their MAC is in the ARP cache.
