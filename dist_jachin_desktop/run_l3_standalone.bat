@echo off
REM 独立模式：不连接 L2，适合新机器或 L2 未启动时使用。需 .env 配置 DASHSCOPE_API_KEY。
cd /d "%~dp0"
if not exist "logs" mkdir logs
set JACHIN_LOG_DIR=%~dp0logs
set JACHIN_APP_ROOT=%~dp0
for %%f in (bin\l3_node*.exe) do (
    echo [L3] Starting %%f --ws-only ^(standalone^)
    echo [L3] Logs: %~dp0logs\l3_debug.log
    start "" "%%f" --ws-only
    echo [L3] Started. Health: http://127.0.0.1:18991/api/health
    goto :done
)
echo [ERR] bin\l3_node*.exe not found. Run build first.
pause
:done
