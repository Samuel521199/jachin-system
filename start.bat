@echo off
REM Quick start batch file

REM Switch to script directory
cd /d "%~dp0"

REM Check if script exists
if not exist "scripts\start.ps1" (
    echo [ERROR] Script not found: scripts\start.ps1
    echo Current directory: %CD%
    pause
    exit /b 1
)

REM Try to activate conda environment (may fail, this is normal)
call conda activate jachin-dev 2>nul
if errorlevel 1 (
    echo [INFO] Conda environment activation failed in cmd.exe
    echo [INFO] This is normal if conda is not initialized in cmd.exe
    echo [INFO] The PowerShell script will handle conda activation
)

REM Run startup script
REM Note: start.ps1 will start long-running services (dapr run), window will stay open
REM Use -NoExit to ensure window stays open even on error
powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start.ps1"
