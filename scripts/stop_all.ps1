# Stop All Services
# 停止所有服务

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   停止所有服务" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# 停止后端服务（如果通过 Dapr 运行）
Write-Host "[1/3] 停止后端服务..." -ForegroundColor Cyan
$daprCmd = Get-Command dapr -ErrorAction SilentlyContinue
if ($daprCmd) {
    Write-Host "  停止 Dapr 应用..." -ForegroundColor Gray
    dapr stop --app-id jachin-brain 2>&1 | Out-Null
    Write-Host "  [OK] Dapr 应用已停止" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Dapr 未安装，跳过" -ForegroundColor Gray
}

# 检查并停止后端进程（端口 18888 或环境变量中的端口）
if ($env:APP_PORT) {
    $appPort = $env:APP_PORT
} elseif ($env:SERVER_PORT) {
    $appPort = $env:SERVER_PORT
} else {
    $appPort = "18888"
}

$backendProcess = Get-NetTCPConnection -LocalPort $appPort -ErrorAction SilentlyContinue
if ($backendProcess) {
    Write-Host "  检测到后端进程在端口 $appPort 运行" -ForegroundColor Yellow
    Write-Host "  请手动停止后端进程（按 Ctrl+C 在运行后端服务的终端中）" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] 后端服务未运行" -ForegroundColor Green
}

# 停止 Docker 服务
Write-Host ""
Write-Host "[2/3] 停止 Docker 中间件服务..." -ForegroundColor Cyan
$composeFile = "docker-compose.dev.yml"
$projectName = "jachin-dev"

if (Test-Path $composeFile) {
    Write-Host "  停止 Docker Compose 服务..." -ForegroundColor Gray
    docker-compose -f $composeFile -p $projectName stop
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Docker 服务已停止" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] 部分服务可能未运行" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [WARN] Docker Compose 文件不存在" -ForegroundColor Yellow
}

# 可选：移除容器（不删除数据）
Write-Host ""
$remove = Read-Host "是否移除 Docker 容器？(y/N)"
if ($remove -eq "y" -or $remove -eq "Y") {
    Write-Host "[3/3] 移除 Docker 容器..." -ForegroundColor Cyan
    docker-compose -f $composeFile -p $projectName rm -f
    Write-Host "  [OK] 容器已移除（数据卷保留）" -ForegroundColor Green
} else {
    Write-Host "[3/3] 保留 Docker 容器" -ForegroundColor Cyan
    Write-Host "  [INFO] 容器已停止但未移除，数据保留" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   所有服务已停止" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "注意：" -ForegroundColor Yellow
Write-Host "  - Docker 数据卷已保留" -ForegroundColor Gray
Write-Host "  - 本地数据库（PostgreSQL, Qdrant）仍在运行" -ForegroundColor Gray
Write-Host "  - 要停止本地数据库，请手动停止相应服务" -ForegroundColor Gray
Write-Host ""
