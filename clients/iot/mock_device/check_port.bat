@echo off
REM Port Checker - 检查端口占用情况

cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0check_port.ps1" %*

pause
