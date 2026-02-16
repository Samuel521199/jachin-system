@echo off
REM Desktop Sprite - 使用 Dapr 启动脚本（批处理版本）

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查 PowerShell 是否可用
powershell -Command "exit 0" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PowerShell not found. Please use PowerShell to run this script.
    pause
    exit /b 1
)

REM 使用 PowerShell 运行脚本
powershell -ExecutionPolicy Bypass -File "%~dp0run_with_dapr.ps1"

if %errorlevel% neq 0 (
    pause
    exit /b 1
)

pause
