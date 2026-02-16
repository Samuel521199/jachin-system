@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo Check Jachin-System Service Status
echo ==========================================
echo.

echo [1] Checking Docker Desktop...
docker ps >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Docker Desktop is running
) else (
    echo [ERROR] Docker Desktop is not running
)
echo.

echo [2] Checking Dapr Sidecar (port 3500)...
curl -s http://localhost:3500/v1.0/healthz >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Dapr Sidecar is running
) else (
    echo [ERROR] Dapr Sidecar is not running
    echo Please run: start_backend.bat
)
echo.

echo [3] Checking Backend Service (port 8000)...
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Backend Service is running
) else (
    echo [ERROR] Backend Service is not running
    echo Please run: start_backend.bat
)
echo.

echo ==========================================
pause
