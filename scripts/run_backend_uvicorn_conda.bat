@echo off
REM Start L2 FastAPI backend (uvicorn) - Admin panel at /admin/
REM Supports: jachin-dev, jachin-layer2 conda env, or system python

chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0\.."
set "PYTHONPATH=%~dp0\.."

set "PYEXE="
if exist "%USERPROFILE%\.conda\envs\jachin-layer2\python.exe" set "PYEXE=%USERPROFILE%\.conda\envs\jachin-layer2\python.exe"
if "%PYEXE%"=="" if exist "%USERPROFILE%\.conda\envs\jachin-dev\python.exe" set "PYEXE=%USERPROFILE%\.conda\envs\jachin-dev\python.exe"
if "%PYEXE%"=="" if exist "%LOCALAPPDATA%\conda\conda\envs\jachin-layer2\python.exe" set "PYEXE=%LOCALAPPDATA%\conda\conda\envs\jachin-layer2\python.exe"
if "%PYEXE%"=="" if exist "%LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe" set "PYEXE=%LOCALAPPDATA%\conda\conda\envs\jachin-dev\python.exe"
if "%PYEXE%"=="" set "PYEXE=python"

set "PORT=18888"
if defined APP_PORT set "PORT=%APP_PORT%"
if defined SERVER_PORT set "PORT=%SERVER_PORT%"

echo [Backend] Starting uvicorn on port %PORT% ...
echo [Backend] Admin panel: http://localhost:%PORT%/admin/
echo.
"%PYEXE%" -m uvicorn core.main:app --host 0.0.0.0 --port %PORT%
