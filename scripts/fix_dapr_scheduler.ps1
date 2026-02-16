# Fix Dapr Scheduler Service
# 修复 Dapr Scheduler 服务

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fixing Dapr Scheduler Service" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

Write-Host "[1/3] Stopping existing scheduler..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName stop dapr-scheduler 2>&1 | Out-Null
docker-compose -f $composeFile -p $projectName rm -f dapr-scheduler 2>&1 | Out-Null

Write-Host "[2/3] Checking scheduler logs..." -ForegroundColor Cyan
$logs = docker logs jachin-dapr-scheduler-dev --tail 20 2>&1
if ($logs) {
    Write-Host "  [INFO] Recent logs:" -ForegroundColor Gray
    $logs | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
}

Write-Host ""
Write-Host "[3/3] Restarting scheduler with fixed configuration..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName up -d dapr-scheduler

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Scheduler restarted" -ForegroundColor Green
    Write-Host "  [INFO] Waiting for scheduler to start..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
    
    # Check status
    $status = docker ps --filter "name=jachin-dapr-scheduler-dev" --format "{{.Status}}"
    if ($status -match "Up") {
        Write-Host "  [SUCCESS] Scheduler is running!" -ForegroundColor Green
        Write-Host "    Status: $status" -ForegroundColor Gray
    } else {
        Write-Host "  [WARN] Scheduler may still be starting" -ForegroundColor Yellow
        Write-Host "  [INFO] Check logs: docker logs jachin-dapr-scheduler-dev" -ForegroundColor Gray
    }
} else {
    Write-Host "  [ERROR] Failed to restart scheduler" -ForegroundColor Red
    Write-Host "  [INFO] Check configuration in docker-compose.dev.yml" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fix Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Note: Dapr Scheduler is optional." -ForegroundColor Gray
Write-Host "      If it fails to start, your app will still work." -ForegroundColor Gray
Write-Host "      Scheduler is only needed for Actor Reminders." -ForegroundColor Gray
Write-Host ""
