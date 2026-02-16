@echo off
REM 安装 Mock Device 所需的依赖

echo ============================================================
echo Installing Mock Device Dependencies
echo ============================================================
echo.

REM 检查是否在 conda 环境中
python -c "import sys; exit(0 if 'conda' in sys.executable.lower() else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Not in conda environment. Please activate jachin-dev first:
    echo    conda activate jachin-dev
    echo.
)

echo Installing dapr-ext-grpc...
pip install dapr-ext-grpc

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to install dapr-ext-grpc
    echo Trying alternative: dapr
    pip install dapr
)

echo.
echo Installing pydantic...
pip install pydantic

echo.
echo ============================================================
echo Installation complete!
echo ============================================================
echo.
echo Verify installation:
python -c "from dapr.clients import DaprClient; print('DaprClient import OK')"

pause
