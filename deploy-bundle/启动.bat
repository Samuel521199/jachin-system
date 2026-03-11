@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   Jachin L1 + L2 部署
echo ========================================
echo.

cd /d "%~dp0"
set "COMPOSE_FILE=docker-compose.deploy-l1-l2-images.yml"
set "TAR_FILE=jachin-l1-l2-images.tar"

if not exist "%TAR_FILE%" (
    echo [错误] 未找到 %TAR_FILE%
    echo 请先在有代码的机器上运行 deploy\生成部署包.bat 生成完整部署包
    pause
    exit /b 1
)

where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未安装 Docker，请先安装 Docker Desktop
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
echo   L1 平台: http://localhost:3000
echo   L2 API:  http://localhost:18888
echo.
pause
