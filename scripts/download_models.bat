@echo off
REM 一键下载 Jachin 所需模型（VAD / TTS / Whisper）
REM 用法: download_models.bat [vad|tts|whisper|all]
REM 无参数时默认下载全部

setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0.."
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%;%ROOT%\core"

set "ARG=%~1"
if "%ARG%"=="" set "ARG=all"

if "%ARG%"=="vad" goto do_vad
if "%ARG%"=="tts" goto do_tts
if "%ARG%"=="whisper" goto do_whisper
if "%ARG%"=="all" goto do_all
echo Usage: %~nx0 [vad^|tts^|whisper^|all]
exit /b 1

:do_vad
echo [Models] 正在下载 VAD 模型 (silero_vad.onnx)...
python scripts\download_vad_model.py
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
echo [Models] VAD 完成.
goto end

:do_tts
echo [Models] 正在下载 TTS 模型 (MOSS ONNX)...
call conda run -n jachin-dev python scripts\download_tts_models.py
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
echo [Models] TTS 完成.
goto end

:do_whisper
echo [Models] 正在下载 Whisper 模型...
where conda >nul 2>&1
if %ERRORLEVEL% neq 0 (
  echo [ERROR] 未找到 conda，请先安装并创建 jachin-dev 环境。
  exit /b 1
)
call conda run -n jachin-dev python scripts\download_whisper_model.py %*
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
echo [Models] Whisper 完成.
goto end

:do_all
echo ========================================
echo   Jachin 模型一键下载 (VAD + TTS + Whisper)
echo ========================================
echo.
call :do_vad
if errorlevel 1 exit /b 1
echo.
call :do_tts
if errorlevel 1 exit /b 1
echo.
echo [Models] 正在下载 Whisper 模型...
where conda >nul 2>&1
if errorlevel 1 (echo [ERROR] 未找到 conda.; exit /b 1)
call conda run -n jachin-dev python scripts\download_whisper_model.py
if errorlevel 1 exit /b 1
echo [Models] Whisper 完成.
echo.
echo [Models] 全部完成.
goto end

:end
endlocal
exit /b 0
