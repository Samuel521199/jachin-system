@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   Nexus 控制台
echo ========================================
echo   启动后访问: http://localhost:3000
echo   按 Ctrl+C 停止
echo ========================================
echo.

powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-cloud.ps1"
