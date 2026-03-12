@echo off
chcp 936 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   Nexus ?????
echo ========================================
echo   ????????: http://localhost:3000
echo   ?? Ctrl+C ??
echo ========================================
echo.

powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-cloud.ps1"
