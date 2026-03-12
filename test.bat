@echo off
REM Test batch file - 测试 API 批处理文件

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查脚本是否存在
if not exist "scripts\test.ps1" (
    echo [ERROR] Script not found: scripts\test.ps1
    echo Current directory: %CD%
    pause
    exit /b 1
)

REM 运行测试脚本
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\test.ps1"
