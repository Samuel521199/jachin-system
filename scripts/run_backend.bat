@echo off
REM Wrapper script for running backend with conda environment
REM This script is called by dapr run

REM Set UTF-8 encoding for Windows console and Python stdio
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0\.."

REM Set PYTHONPATH (project root only - do NOT add core, or core/dapr shadows pip dapr package)
set "PYTHONPATH=%~dp0\.."

REM Ensure QWEN_AI_API_KEY is passed through (if set in parent process)
REM Environment variables should be inherited from dapr run, but we ensure they're available

REM Find conda Python executable
set "CONDA_PYTHON="
if exist "%USERPROFILE%\.conda\envs\jachin-dev\python.exe" (
    set "CONDA_PYTHON=%USERPROFILE%\.conda\envs\jachin-dev\python.exe"
) else if exist "%LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe" (
    set "CONDA_PYTHON=%LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe"
)

if "%CONDA_PYTHON%"=="" (
    echo [ERROR] Could not find conda Python executable
    echo   Please ensure jachin-dev conda environment exists
    echo   Expected locations:
    echo     %USERPROFILE%\.conda\envs\jachin-dev\python.exe
    echo     %LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe
    exit /b 1
)

REM Run uvicorn (use port from environment variable or default to 18888)
if defined APP_PORT (
    set "APP_PORT_VAL=%APP_PORT%"
) else if defined SERVER_PORT (
    set "APP_PORT_VAL=%SERVER_PORT%"
) else (
    set "APP_PORT_VAL=18888"
)
"%CONDA_PYTHON%" -m uvicorn core.main:app --host 0.0.0.0 --port %APP_PORT_VAL%
