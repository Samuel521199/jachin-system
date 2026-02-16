@echo off
chcp 65001 >nul 2>&1
echo ==========================================
echo Test Backend API
echo ==========================================
echo.

echo [1] Testing direct backend (port 8000)...
echo.
curl -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d "{\"message\": \"test\"}"
echo.
echo.

echo [2] Testing via Dapr (port 3500)...
echo.
curl -X POST http://localhost:3500/v1.0/invoke/jachin-brain/method/api/chat -H "Content-Type: application/json" -d "{\"message\": \"test\"}"
echo.
echo.

echo [3] Testing health endpoint...
echo.
curl http://localhost:8000/health
echo.
echo.

pause
