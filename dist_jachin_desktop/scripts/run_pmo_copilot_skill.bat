@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1

REM 项目根 = scripts 的上一级（与 run_pmo_copilot_skill.py 一致）
cd /d "%~dp0\.."
if errorlevel 1 (
  echo [PMO Copilot] 错误：无法切换到项目根目录
  pause
  exit /b 1
)

set "JACHIN_APP_ROOT=%CD%"
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1

echo ========================================
echo   PMO Copilot
echo   工作目录: %CD%
echo ========================================
echo.

if exist "%CD%\scripts\run_pmo_copilot_skill.py" (
  echo [信息] 脚本: scripts\run_pmo_copilot_skill.py
) else (
  echo [错误] 缺少 scripts\run_pmo_copilot_skill.py
  pause
  exit /b 1
)

if exist "%CD%\.env" (
  echo [信息] 已检测到 .env
) else (
  echo [警告] 未找到 .env，请确认 API Key 已配置
)
echo.

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
  echo [信息] 执行: py -3 -u scripts\run_pmo_copilot_skill.py
  echo.
  py -3 -u scripts\run_pmo_copilot_skill.py
  goto :done
)

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
  echo [信息] 执行: python -u scripts\run_pmo_copilot_skill.py
  echo.
  python -u scripts\run_pmo_copilot_skill.py
  goto :done
)

echo [错误] 未找到 py 或 python。请安装 Python 3.11+ 并加入 PATH。
pause
exit /b 1

:done
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 (
  echo [PMO Copilot] 失败，退出码 %EXITCODE%
) else (
  echo [PMO Copilot] 已完成
)
echo 详细调试日志: %USERPROFILE%\.jachin\jachin_debug\健康skill
echo.
pause
exit /b %EXITCODE%
