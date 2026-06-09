@echo off
REM 调试 L3 侧车：保持 PowerShell 窗口，便于查看 run_l3.ps1 输出（打包安装目录双击本文件）
cd /d "%~dp0"
if not exist "scripts\run_l3.ps1" (
    echo [ERR] 未找到 scripts\run_l3.ps1，请在本安装目录下运行（与 Jachin Desktop.exe 同级）
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\run_l3.ps1" %*
