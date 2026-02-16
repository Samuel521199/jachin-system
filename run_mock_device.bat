@echo off
REM Mock IoT Device - 从项目根目录运行的包装脚本

cd /d "%~dp0"

if not exist "clients\iot\mock_device\run_with_heartbeat.bat" (
    echo [ERROR] Script not found: clients\iot\mock_device\run_with_heartbeat.bat
    echo Please ensure you're in the project root directory.
    pause
    exit /b 1
)

echo Running Mock IoT Device...
echo.

call "clients\iot\mock_device\run_with_heartbeat.bat"
