@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   生成 L1+L2 部署包
echo ========================================
echo.

cd /d "%~dp0.."

echo [1/3] 构建镜像...
docker compose -f docker-compose.deploy-l1-l2.yml build
if %errorlevel% neq 0 (
    echo 构建失败
    pause
    exit /b 1
)

echo.
echo [2/3] 导出镜像...
docker save -o deploy-bundle\jachin-l1-l2-images.tar ^
    jachin/l1-db-init:latest ^
    jachin/l1-nexus:latest ^
    jachin/l2-control:latest

echo.
echo [3/3] 完成
echo.
echo 部署包位置: deploy-bundle\
echo 将 deploy-bundle 整个文件夹拷贝到目标机器，运行 启动.bat 或 启动.sh
echo.
pause
