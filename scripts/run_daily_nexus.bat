@echo off
REM Daily Nexus Commander — 双击或计划任务调用（仓库根目录执行）
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
python scripts\run_daily_nexus.py %*
set EXITCODE=%ERRORLEVEL%
exit /b %EXITCODE%
