@echo off
REM Mock IoT Device - Windows 启动脚本
REM 使用 Dapr Run 启动模拟设备

echo ============================================================
echo Mock IoT Device - Capability Discovery Test
echo ============================================================
echo.

REM 切换到项目根目录
cd /d %~dp0\..\..\..

REM 检查 Dapr 是否安装
where dapr >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Dapr CLI not found. Please install Dapr first.
    echo    Visit: https://docs.dapr.io/getting-started/install-dapr-cli/
    pause
    exit /b 1
)

REM 检查 Python 环境
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Python not found. Please activate conda environment first.
    echo    Run: conda activate jachin-dev
    pause
    exit /b 1
)

echo Starting Mock IoT Device with Dapr...
echo.

REM 使用 Dapr Run 启动
dapr run ^
  --app-id mock-iot-device ^
  --app-port 8001 ^
  --dapr-http-port 3501 ^
  --dapr-grpc-port 50002 ^
  --resources-path ./dapr/components ^
  --config ./dapr/config/config.yaml ^
  -- python clients/iot/mock_device/main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ Failed to start Mock IoT Device
    pause
    exit /b 1
)

pause
