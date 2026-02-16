@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo Jachin-System One-Click Start
echo ==========================================
echo.

REM Change to script directory
cd /d "%~dp0"

REM Check Docker Desktop
echo [1/4] Checking Docker Desktop...
docker ps >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker Desktop is not running!
    echo Please start Docker Desktop first, then run this script again.
    pause
    exit /b 1
)
echo [OK] Docker Desktop is running
echo.

REM Start backend service (in new window, keep open)
echo [2/4] Starting backend service...
start "Jachin Backend Service - DO NOT CLOSE THIS WINDOW" cmd /k "powershell.exe -ExecutionPolicy Bypass -File .\scripts\start.ps1"

REM Wait for backend service to start
echo [3/4] Waiting for backend service to start...
echo Checking backend service health...

REM Check both Dapr sidecar and backend API
set /a retries=0
:check_backend
REM Check Dapr sidecar
curl -s http://localhost:3500/v1.0/healthz >nul 2>&1
if %errorlevel% neq 0 (
    set /a retries+=1
    if %retries% geq 15 (
        echo [WARNING] Dapr sidecar not responding after 30 seconds
        echo Continuing anyway - check the backend service window for errors.
        goto start_client
    )
    timeout /t 2 /nobreak >nul
    goto check_backend
)

REM Check backend API health endpoint
curl -s http://localhost:8000/health >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Backend service is ready
    echo [OK] Dapr sidecar is running
    goto start_client
)

set /a retries+=1
if %retries% geq 15 (
    echo [WARNING] Backend API not responding after 30 seconds
    echo Continuing anyway - check the backend service window for errors.
    echo You can manually test: curl http://localhost:8000/health
    goto start_client
)
timeout /t 2 /nobreak >nul
goto check_backend

:start_client
echo.
echo [4/4] Starting desktop client...
echo.
echo ==========================================
echo NOTE:
echo - Keep the backend service window open
echo - Closing it will stop the backend service
echo ==========================================
echo.

REM Start desktop client
cd clients\desktop
call npm run tauri:dev

pause
