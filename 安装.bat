@echo off
chcp 936 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   首次安装 - 检查并安装依赖
echo ========================================
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\check-prerequisites.ps1" cloud
if errorlevel 1 goto :eof

powershell -ExecutionPolicy Bypass -File "%~dp0scripts\install-cloud.ps1"
if errorlevel 1 goto :eof

echo.
echo ========================================
echo   安装完成！可双击「启动配对Demo.bat」
echo ========================================
echo.
pause