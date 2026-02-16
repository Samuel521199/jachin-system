@echo off
REM Mock IoT Device with Heartbeat - Windows 启动脚本
REM 持续运行并定期发送心跳，自动过滤 scheduler 错误日志

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查 conda 环境
call conda info --envs | findstr "jachin-dev" >nul
if %errorlevel% neq 0 (
    echo [ERROR] jachin-dev conda environment not found.
    echo Please create it first: conda env create -f environment.yml
    pause
    exit /b 1
)

REM 激活 conda 环境
call conda activate jachin-dev
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate jachin-dev environment.
    pause
    exit /b 1
)

REM 验证 Python 环境
python -c "import sys; exit(0 if 'jachin-dev' in sys.executable else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Python is not from jachin-dev environment.
    echo Current Python: 
    python -c "import sys; print(sys.executable)"
    echo.
    echo Please ensure jachin-dev environment is activated.
    pause
    exit /b 1
)

REM 检查 PowerShell 是否可用
powershell -Command "exit 0" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] PowerShell not found. Please use PowerShell to run this script.
    pause
    exit /b 1
)

REM 使用 PowerShell 运行脚本（支持日志过滤）
powershell -ExecutionPolicy Bypass -File "%~dp0run_with_heartbeat.ps1"

if %errorlevel% neq 0 (
    pause
    exit /b 1
)

pause
