@echo off
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\run-daemon.ps1"
