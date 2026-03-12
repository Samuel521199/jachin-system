# Stop script - 一键停止所有服务
# 停止 Dapr 应用、后端进程(18888)、Docker 中间件；可选移除容器

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   停止所有服务" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# 1) 停止 Dapr 应用
Write-Host "[1/3] 停止后端服务..." -ForegroundColor Cyan
$daprCmd = Get-Command dapr -ErrorAction SilentlyContinue
if ($daprCmd) {
    Write-Host "  停止 Dapr 应用..." -ForegroundColor Gray
    dapr stop --app-id jachin-brain 2>&1 | Out-Null
    Write-Host "  [OK] Dapr 应用已停止" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Dapr 未安装，跳过" -ForegroundColor Gray
}

$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$backendProcess = Get-NetTCPConnection -LocalPort $appPort -ErrorAction SilentlyContinue
if ($backendProcess) {
    $processId = ($backendProcess | Select-Object -First 1).OwningProcess
    if ($processId) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止端口 $appPort 上的后端进程 (PID: $processId)" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] 后端服务未运行" -ForegroundColor Green
}

# 2) 停止 Docker 中间件
Write-Host ""
Write-Host "[2/3] 停止 Docker 中间件服务..." -ForegroundColor Cyan
$composeFile = Join-Path $ProjectRoot "docker-compose.dev.yml"
$projectName = "jachin-dev"
if (Test-Path $composeFile) {
    Write-Host "  停止 Docker Compose 服务..." -ForegroundColor Gray
    docker-compose -f $composeFile -p $projectName down --remove-orphans 2>$null | Out-Null
    Write-Host "  [OK] Docker 服务已停止" -ForegroundColor Green
} else {
    Write-Host "  [WARN] docker-compose.dev.yml 不存在" -ForegroundColor Yellow
}

# 3) 可选移除容器（默认不删，保留数据）
Write-Host ""
$remove = Read-Host "是否移除 Docker 容器？(y/N)"
if ($remove -eq "y" -or $remove -eq "Y") {
    Write-Host "[3/3] 移除 Docker 容器..." -ForegroundColor Cyan
    docker-compose -f $composeFile -p $projectName rm -f 2>$null | Out-Null
    Write-Host "  [OK] 容器已移除（数据卷保留）" -ForegroundColor Green
} else {
    Write-Host "[3/3] 保留 Docker 容器" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   所有服务已停止" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
exit 0
