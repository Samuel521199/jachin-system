@echo off
REM Restart batch file - 重启服务批处理文件

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查脚本是否存在
if not exist "scripts\restart.ps1" (
    echo [ERROR] Script not found: scripts\restart.ps1
    echo Current directory: %CD%
    pause
    exit /b 1
)

REM 运行重启脚本
REM 注意：start.ps1 会启动长时间运行的服务，所以窗口会保持打开
powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\restart.ps1"
