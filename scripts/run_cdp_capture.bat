@echo off
REM Tongits settlement auto-capture via Chrome DevTools Protocol (no F12 needed)
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "ROOT=%~dp0.."
set "VENV_PY=%ROOT%\.venv-omniparser\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=python"
set "SCRIPT=%~dp0tongits_cdp_capture.py"

REM Launch a dedicated Chrome with debug port and open the game site.
REM First run: log in inside that Chrome window (it will be remembered next time).
REM Override anything by appending args after the bat name.
set "GAME_URL=https://www.herontest.xin/"
set "ARGS=--launch --discover --my-name victor --url %GAME_URL%"

echo [cdp] python=%VENV_PY%
echo [cdp] launching dedicated Chrome on debug port 9222; log in there on first run.
"%VENV_PY%" "%SCRIPT%" %ARGS% %*
endlocal
