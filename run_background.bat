@echo off
REM  run_background.bat — launch esign_watcher.py minimized in the background.
REM  No service manager needed. Put a shortcut to this file in your Startup
REM  folder (shell:startup) to auto-start on login.
start "" /MIN pythonw "%~dp0esign_watcher.py" --dir C:/Sideload/IPAs --host 0.0.0.0 --port 8080
