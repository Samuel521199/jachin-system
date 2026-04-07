@echo off
cd /d "%~dp0"
if not exist "logs" mkdir logs
set JACHIN_LOG_DIR=%~dp0logs
set JACHIN_APP_ROOT=%~dp0
set MODE=--ws-only
if "%1"=="--gateway" set MODE=--gateway
for %%f in (bin\l3_node*.exe) do (
    echo [L3] Starting %%f %MODE%
    echo [L3] Logs: %~dp0logs\l3_debug.log
    start "" "%%f" %MODE%
    echo [L3] Started. Health: http://127.0.0.1:18991/api/health
    goto :done
)
echo [ERR] bin\l3_node*.exe not found. Run build first.
pause
:done
