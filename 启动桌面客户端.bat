@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo Start Jachin-System Desktop Client
echo ==========================================
echo.

REM Change to script directory
cd /d "%~dp0"

REM Enter desktop client directory
cd clients\desktop

REM Start desktop client
call npm run tauri:dev

pause
