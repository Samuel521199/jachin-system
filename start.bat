@echo off
chcp 936 >nul
cd /d "%~dp0"

REM ??????????????????????
REM ?§Ó????????????????????????
set TARGET=%~1

if "%TARGET%"=="" (
    powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start-menu.ps1"
) else if "%TARGET%"=="cloud" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-cloud.ps1"
) else if "%TARGET%"=="daemon" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\run-daemon.ps1"
) else if "%TARGET%"=="layer2" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-layer2.ps1"
) else if "%TARGET%"=="layer3" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-layer3.ps1"
) else if "%TARGET%"=="full" (
    powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-full.ps1"
) else if "%TARGET%"=="pair" (
    powershell -ExecutionPolicy Bypass -File "%~dp0scripts\run-pair.ps1"
) else (
    echo Unknown option. Double-click start.bat to show menu.
)
