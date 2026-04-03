@echo off
REM Tools(jpp) 独立验证：无需 Jachin 主项目，验证 stdin/stdout JSON 协议
REM 规范要求：echo '{"key":"val"}' | python src/main.py 输出正确 JSON
cd /d "%~dp0"
python verify_standalone.py
exit /b %ERRORLEVEL%
