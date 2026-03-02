@echo off
REM 启动入口 - 各层独立
REM 用法: start.bat [cloud|layer2|layer3|full|pair]
REM   默认 layer2

cd /d "%~dp0"
set TARGET=%~1
if "%TARGET%"=="" set TARGET=layer2

if "%TARGET%"=="cloud" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-cloud.ps1"
) else if "%TARGET%"=="layer2" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-layer2.ps1"
) else if "%TARGET%"=="layer3" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-layer3.ps1"
) else if "%TARGET%"=="full" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-full.ps1"
) else if "%TARGET%"=="pair" (
    powershell -ExecutionPolicy Bypass -File "%~dp0scripts\run-pair.ps1"
) else (
    echo 用法: start.bat [cloud^|layer2^|layer3^|full^|pair]
    echo   cloud  - Cloud (平台商) Nexus Console
    echo   layer2 - Layer2 (用户) nexus_daemon
    echo   layer3 - Layer3 (用户) Desktop Terminal
    echo   full   - 完整栈 Docker+Dapr+后端
    echo   pair   - 边缘智能体配对 (6位码)
    echo   默认: layer2
)
