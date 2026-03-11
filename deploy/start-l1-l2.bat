@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   Jachin L1 + L2 一键部署
echo ========================================
echo.

:: 检测 Docker
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Docker，请先安装 Docker Desktop
    echo 下载: https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

:: 检测 Docker Compose
docker compose version >nul 2>nul
if %errorlevel% neq 0 (
    docker-compose --version >nul 2>nul
    if !errorlevel! neq 0 (
        echo [错误] 未检测到 Docker Compose
        pause
        exit /b 1
    )
    set COMPOSE_CMD=docker-compose
) else (
    set COMPOSE_CMD=docker compose
)

:: 切换到项目根目录（脚本在 deploy/ 下）
cd /d "%~dp0.."

echo [1/2] 构建并启动 L1 + L2...
%COMPOSE_CMD% -f docker-compose.deploy-l1-l2.yml up -d --build

if %errorlevel% neq 0 (
    echo.
    echo [失败] 启动失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo [2/2] 等待服务就绪...
timeout /t 15 /nobreak >nul

echo.
echo ========================================
echo   部署完成
echo ========================================
echo.
echo   L1 平台 (Nexus):  http://localhost:3000
echo   L2 控制面 API:    http://localhost:18888
echo.
echo   停止服务: %COMPOSE_CMD% -f docker-compose.deploy-l1-l2.yml down
echo.
pause
