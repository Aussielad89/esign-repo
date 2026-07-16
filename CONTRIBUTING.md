# Contributing

Thanks for looking at **esign-repo**! This is a small, focused toolkit — here's
how to keep changes clean.

## Layout

| Path | Role |
|------|------|
| `esign_repo.py` | Index format + IPA extraction. **No network, no GUI.** |
| `esign_watcher.py` | Folder watch → index → HTTP server (Task 1) |
| `esign_gui.py` | PySide6 desktop builder (Task 2) |
| `netscan.py` | stdlib-only LAN scanner (Task 3) |
| `tests/` | pytest suite (29 tests) |

## Dev setup

```bat
pip install -e .[dev]      REM installs pyside6/pillow/watchdog + pytest/pytest-qt
python -m pytest tests/ -q
```

GUI tests run **headless** via the `offscreen` Qt platform (set in
`conftest.py`), so they don't need a display.

## Rules of thumb

- **Keep `esign_repo.py` dependency-free** (stdlib + Pillow only). Both repo
  tools rely on it being testable and consistent.
- **Don't fork the index format.** If you change `esign_source.json`, update
  both `extract_ipa_metadata` → `to_entry` and `validate_index`, and add a
  test in `tests/test_esign_repo.py`.
- **`netscan.py` stays pure stdlib.** No `pip install` deps there.
- Run `python -m pytest tests/ -q` and make sure it's green before opening a
  PR. CI runs the same on Windows × Python 3.10–3.12.

## Adding a feature

1. Add/extend the unit test first.
2. Implement.
3. Update the README / `ARCHITECTURE.md` if behavior or the index changes.
4. Open a PR using the template (it checks the two boxes above).

## Releasing

Tagging `vX.Y.Z` is the trigger if a release workflow is added later.
Until then, `main` is the shipped state.
