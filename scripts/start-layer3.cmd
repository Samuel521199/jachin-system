@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-layer3.ps1" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo [start-layer3] 启动失败，退出码 %EXITCODE%
  pause
)
exit /b %EXITCODE%
