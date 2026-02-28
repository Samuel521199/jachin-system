# 重启 Dapr Scheduler 容器（修复配置后重启，一眼看懂用途）
# Restart Dapr Scheduler with Fixed Configuration

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Restarting Dapr Scheduler" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

Write-Host "[1/3] Stopping and removing old container..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName stop dapr-scheduler 2>&1 | Out-Null
docker-compose -f $composeFile -p $projectName rm -f dapr-scheduler 2>&1 | Out-Null
Write-Host "  [OK] Old container removed" -ForegroundColor Green

Write-Host ""
Write-Host "[2/3] Starting scheduler with fixed configuration..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName up -d dapr-scheduler

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Scheduler container started" -ForegroundColor Green
    Write-Host ""
    Write-Host "[3/3] Waiting for scheduler to initialize..." -ForegroundColor Cyan
    Start-Sleep -Seconds 15
    $status = docker ps --filter "name=jachin-dapr-scheduler-dev" --format "{{.Status}}" 2>&1
    if ($status -match "Up") {
        Write-Host "  [SUCCESS] Scheduler is running!" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Check logs: docker logs jachin-dapr-scheduler-dev" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [ERROR] Failed to start scheduler" -ForegroundColor Red
    exit 1
}
Write-Host ""
