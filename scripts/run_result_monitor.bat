@echo off
REM Tongits 胜负结算监控（纯协议，不依赖视觉）launcher
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "ROOT=%~dp0.."
set "VENV_PY=%ROOT%\.venv-omniparser\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=python"
set "SCRIPT=%~dp0tongits_result_monitor.py"

REM 我方昵称（结算列表里定位本人用），可在命令后追加覆盖，例如：run_result_monitor.bat --my-name Drama
set "ARGS=--my-name victor --discover"

echo [result-monitor] python=%VENV_PY%
echo [result-monitor] 浏览器控制台请贴入 scripts\tongits_result_monitor_snippet.js
"%VENV_PY%" "%SCRIPT%" %ARGS% %*
endlocal
