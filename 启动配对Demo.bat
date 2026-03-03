@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ========================================
echo   配对 Demo - 一键启动
echo ========================================
echo.

REM 1. 启动 Cloud（新窗口，保持打开）
echo [1/3] 正在启动 Nexus 控制台...
start "Nexus 控制台" cmd /k "cd /d "%~dp0" && powershell -ExecutionPolicy Bypass -NoExit -File "%~dp0scripts\start-cloud.ps1""

REM 2. 等待服务器启动
echo [2/3] 等待服务就绪...
timeout /t 12 /nobreak >nul

REM 3. 启动配对并打开浏览器（新窗口显示 6 位码）
echo [3/3] 打开配对页面...
start "配对 - 查看 6 位码" cmd /k "cd /d "%~dp0" && powershell -ExecutionPolicy Bypass -File "%~dp0scripts\run-pair.ps1""
timeout /t 4 /nobreak >nul
start http://localhost:3000/console/pair

echo.
echo ========================================
echo   已就绪！
echo ========================================
echo.
echo  1. 在「配对」窗口查看 6 位码
echo  2. 在浏览器输入 6 位码，点击「建立神经连接」
echo  3. 配对成功后，终端会显示绿色提示
echo.
pause
