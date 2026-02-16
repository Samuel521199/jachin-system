@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo Start Jachin-System Backend Service
echo ==========================================
echo.
echo NOTE: This window will stay open. Closing it will stop the backend service.
echo.

REM Change to script directory
cd /d "%~dp0"

REM Run PowerShell start script
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\start.ps1"

REM If script exits, pause to view error messages
echo.
echo Backend service stopped
pause
