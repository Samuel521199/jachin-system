@echo off
chcp 65001 >nul
echo.
echo 正在生成无代码部署包...
echo.

cd /d "%~dp0.."

call deploy\export-images.bat
if %errorlevel% neq 0 exit /b 1

mkdir jachin-deploy-bundle 2>nul
copy docker-compose.deploy-l1-l2-images.yml jachin-deploy-bundle\
copy jachin-l1-l2-images.tar jachin-deploy-bundle\
copy deploy\import-and-run.bat jachin-deploy-bundle\
copy deploy\import-and-run.sh jachin-deploy-bundle\

echo.
echo 部署包已生成: jachin-deploy-bundle\
echo 将整个文件夹拷贝到目标机器，运行 import-and-run.bat 或 import-and-run.sh
echo.
pause
