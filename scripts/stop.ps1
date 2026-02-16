# Stop script - 停止所有服务

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "Stopping all services..." -ForegroundColor Cyan

$hasErrors = $false

# Stop Docker services and remove orphan containers
# 忽略错误，因为服务可能不存在
Write-Host "  Stopping Docker services..." -ForegroundColor Gray
docker-compose -f docker-compose.dev.yml down --remove-orphans 2>$null | Out-Null
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
    # 退出码 1 通常表示没有找到文件，这是可以接受的
    $hasErrors = $true
}

docker-compose down --remove-orphans 2>$null | Out-Null
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
    $hasErrors = $true
}

# Stop backend process
Write-Host "  Stopping backend process..." -ForegroundColor Gray
$backendProcess = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($backendProcess) {
    $processId = ($backendProcess | Select-Object -First 1).OwningProcess
    if ($processId) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Host "    Backend process stopped (PID: $processId)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "    No backend process found on port 8000" -ForegroundColor DarkGray
}

# Stop Dapr sidecar
Write-Host "  Stopping Dapr sidecar..." -ForegroundColor Gray
$daprProcess = Get-NetTCPConnection -LocalPort 3500 -ErrorAction SilentlyContinue
if ($daprProcess) {
    $processId = ($daprProcess | Select-Object -First 1).OwningProcess
    if ($processId) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Host "    Dapr sidecar stopped (PID: $processId)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "    No Dapr sidecar found on port 3500" -ForegroundColor DarkGray
}

# Stop Dapr applications
Write-Host "  Stopping Dapr applications..." -ForegroundColor Gray
$daprApps = dapr list --output json 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
if ($daprApps) {
    foreach ($app in $daprApps) {
        if ($app.'APP ID') {
            Write-Host "    Stopping $($app.'APP ID')..." -ForegroundColor DarkGray
            dapr stop --app-id $app.'APP ID' 2>$null | Out-Null
        }
    }
} else {
    Write-Host "    No Dapr applications found" -ForegroundColor DarkGray
}

Write-Host "[OK] All services stopped" -ForegroundColor Green

# 显式返回成功退出码
exit 0
