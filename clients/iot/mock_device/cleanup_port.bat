@echo off
REM Port Cleanup - 清理端口占用

cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0cleanup_port.ps1" %*

pause
