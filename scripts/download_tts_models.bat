@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo ========================================
echo   Jachin TTS 模型预下载
echo ========================================
echo.
set PYTHONPATH=%cd%;%cd%\core
call conda run -n jachin-dev python scripts\download_tts_models.py
pause
