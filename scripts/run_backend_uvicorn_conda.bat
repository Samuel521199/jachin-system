@echo off
REM 启动后端 uvicorn (Conda jachin-dev 环境)
REM 被 start-full.ps1 通过 dapr run 调用，一般无需单独运行

chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not defined RAY_BACKEND_LOG_LEVEL set RAY_BACKEND_LOG_LEVEL=error

cd /d "%~dp0\.."
set "PYTHONPATH=%~dp0\.."

set "CONDA_PYTHON="
if exist "%USERPROFILE%\.conda\envs\jachin-dev\python.exe" (
    set "CONDA_PYTHON=%USERPROFILE%\.conda\envs\jachin-dev\python.exe"
) else if exist "%LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe" (
    set "CONDA_PYTHON=%LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe"
)

if "%CONDA_PYTHON%"=="" (
    echo [ERROR] 未找到 Conda jachin-dev 环境
    exit /b 1
)

if defined APP_PORT (set "APP_PORT_VAL=%APP_PORT%") else if defined SERVER_PORT (set "APP_PORT_VAL=%SERVER_PORT%") else (set "APP_PORT_VAL=18888")
"%CONDA_PYTHON%" -m uvicorn core.main:app --host 0.0.0.0 --port %APP_PORT_VAL%
