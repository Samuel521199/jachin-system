@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   Build L1+L2 deploy bundle
echo ========================================
echo.

cd /d "%~dp0.."

echo [1/3] Building images...
docker compose -f docker-compose.deploy-l1-l2.yml build
if %errorlevel% neq 0 (
    echo Build failed
    pause
    exit /b 1
)

echo.
echo [2/3] Exporting images...
docker save -o deploy-bundle\jachin-l1-l2-images.tar ^
    jachin/l1-db-init:latest ^
    jachin/l1-nexus:latest ^
    jachin/l2-control:latest

echo.
echo [3/3] Done
echo.
echo Bundle: deploy-bundle\
echo Copy deploy-bundle folder to target machine, run 启动.ps1 or 启动.bat
echo.
pause
