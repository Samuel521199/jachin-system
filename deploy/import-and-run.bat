@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   L1+L2 无代码部署（目标机器）
echo ========================================
echo.

:: 优先使用脚本所在目录，其次当前目录
cd /d "%~dp0"
set "COMPOSE_FILE=docker-compose.deploy-l1-l2-images.yml"
set "TAR_FILE=jachin-l1-l2-images.tar"
if not exist "%COMPOSE_FILE%" (
    cd ..
    set "COMPOSE_FILE=docker-compose.deploy-l1-l2-images.yml"
    set "TAR_FILE=jachin-l1-l2-images.tar"
)

if not exist "%TAR_FILE%" (
    echo [错误] 未找到 %TAR_FILE%
    echo 请确保与 docker-compose.deploy-l1-l2-images.yml 放在同一目录
    pause
    exit /b 1
)

where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未安装 Docker
    pause
    exit /b 1
)

docker compose version >nul 2>nul
if %errorlevel% neq 0 (set COMPOSE_CMD=docker-compose) else (set COMPOSE_CMD=docker compose)

echo [1/2] 导入镜像...
docker load -i "%TAR_FILE%"

echo.
echo [2/2] 启动服务...
%COMPOSE_CMD% -f "%COMPOSE_FILE%" up -d

echo.
echo ========================================
echo   部署完成
echo ========================================
echo   L1: http://localhost:3000
echo   L2: http://localhost:18888
echo.
pause
