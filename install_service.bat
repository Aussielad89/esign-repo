@echo off
REM  install_service.bat — register esign_watcher as a Windows service (nssm).
REM
REM  Prereqs:
REM    1) Install nssm (Non-Sucking Service Manager) once:
REM         winget install nssm  (or download from https://nssm.cc)
REM    2) Run THIS file as Administrator.
REM
REM  What it does: creates a service "EsignRepo" that launches
REM  esign_watcher.py on boot and keeps it running in the background.

SETLOCAL
SET SCRIPT_DIR=%~dp0
SET PYTHON=%SYSTEMROOT%\py.exe
IF NOT EXIST "%PYTHON%" (SET PYTHON=python.exe)

WHERE nssm >nul 2>&1 || (
    echo ERROR: nssm not found on PATH. Install it first: winget install nssm
    pause
    exit /b 1
)

nssm stop EsignRepo 2>nul
nssm remove EsignRepo confirm 2>nul

nssm install EsignRepo "%PYTHON%" "%SCRIPT_DIR%esign_watcher.py"
nssm set EsignRepo AppDirectory "%SCRIPT_DIR%"
nssm set EsignRepo AppParameters "--dir C:/Sideload/IPAs --host 0.0.0.0 --port 8080"
nssm set EsignRepo DisplayName "Esign Repo Watcher"
nssm set EsignRepo Description "Watches C:/Sideload/IPAs, builds esign_source.json, serves on :8080"
nssm set EsignRepo Start SERVICE_AUTO_START
nssm set EsignRepo AppExit Default Restart

nssm start EsignRepo
echo.
echo Service installed and started. Source URL will be http://YOUR-PC-IP:8080/esign_source.json
echo Manage it in services.msc (look for "Esign Repo Watcher") or:
echo   nssm stop  EsignRepo
echo   nssm start EsignRepo
pause
