@echo off
REM Stop batch file - 停止服务批处理文件

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查脚本是否存在
if not exist "scripts\stop.ps1" (
    echo [ERROR] Script not found: scripts\stop.ps1
    echo Current directory: %CD%
    pause
    exit /b 1
)

REM 运行停止脚本
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"

REM 检查执行结果
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Stop failed with error code %errorlevel%
    pause
    exit /b %errorlevel%
)

echo.
echo [SUCCESS] Services stopped!
echo.
echo Press any key to close this window...
pause >nul
