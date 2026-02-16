@echo off
chcp 65001 >nul 2>&1
echo ============================================================
echo Whisper 模型预下载工具
echo ============================================================
echo.
echo 此脚本将预下载 Whisper 模型，避免首次使用时等待。
echo.
echo 模型大小参考：
echo   - tiny:   ~39 MB  (最快，准确度较低)
echo   - base:   ~74 MB  (推荐，平衡速度和准确度) [默认]
echo   - small:  ~244 MB (更准确)
echo   - medium: ~769 MB (高准确度)
echo   - large:  ~1550 MB (最高准确度，但较慢)
echo.
echo ============================================================
echo.

REM 查找 conda 环境中的 python.exe
set "CONDA_ENV_PATH=%USERPROFILE%\.conda\envs\jachin-dev"
set "PYTHON_EXE="

if exist "%CONDA_ENV_PATH%\python.exe" (
    set "PYTHON_EXE=%CONDA_ENV_PATH%\python.exe"
) else (
    REM 尝试使用 conda run
    where conda >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo 使用 conda run 执行...
        conda run -n jachin-dev python "%~dp0download_whisper_model.py" %*
        goto :end
    ) else (
        echo 错误: 找不到 Python 环境
        echo 请确保已安装 conda 并创建了 jachin-dev 环境
        pause
        exit /b 1
    )
)

if "%PYTHON_EXE%"=="" (
    echo 错误: 找不到 Python 可执行文件
    pause
    exit /b 1
)

echo 使用 Python: %PYTHON_EXE%
echo.

REM 设置 PYTHONPATH
set "PROJECT_ROOT=%~dp0.."
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\backend"

REM 执行下载脚本，直接传递所有参数给 Python 脚本
"%PYTHON_EXE%" "%~dp0download_whisper_model.py" %*

:end
pause
