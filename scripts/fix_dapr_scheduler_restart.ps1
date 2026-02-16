# Fix Dapr Scheduler Restart Issue
# 修复 Dapr Scheduler 一直重启的问题

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fixing Dapr Scheduler Restart Issue" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

Write-Host "[1/4] Stopping scheduler container..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName stop dapr-scheduler 2>&1 | Out-Null
docker-compose -f $composeFile -p $projectName rm -f dapr-scheduler 2>&1 | Out-Null
Write-Host "  [OK] Container stopped and removed" -ForegroundColor Green

Write-Host ""
Write-Host "[2/4] Checking scheduler logs (last 50 lines)..." -ForegroundColor Cyan
$logs = docker logs jachin-dapr-scheduler-dev --tail 50 2>&1
if ($logs -and $logs -notmatch "No such container") {
    Write-Host "  Recent logs:" -ForegroundColor Gray
    $logs | Select-Object -Last 20 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
} else {
    Write-Host "  [INFO] No logs available (container already removed)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[3/4] Configuration updated..." -ForegroundColor Cyan
Write-Host "  [INFO] Fixed command format: '--port 6060' (space-separated)" -ForegroundColor Gray
Write-Host "  [INFO] Added data volume: dapr_scheduler_data:/data" -ForegroundColor Gray
Write-Host "  [INFO] Added --etcd-data-dir /data/scheduler parameter" -ForegroundColor Gray
Write-Host "  [INFO] Added working_dir and user settings for permissions" -ForegroundColor Gray
Write-Host "  [INFO] This fixes the permission denied error" -ForegroundColor Gray

Write-Host ""
Write-Host "[4/4] Restarting scheduler..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName up -d dapr-scheduler

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Scheduler restarted" -ForegroundColor Green
    Write-Host "  [INFO] Waiting 10 seconds for scheduler to start..." -ForegroundColor Gray
    Start-Sleep -Seconds 10
    
    Write-Host ""
    Write-Host "Checking scheduler status..." -ForegroundColor Cyan
    $status = docker ps --filter "name=jachin-dapr-scheduler-dev" --format "{{.Status}}" 2>&1
    
    if ($status -match "Up") {
        Write-Host "  [SUCCESS] Scheduler is running!" -ForegroundColor Green
        Write-Host "    Status: $status" -ForegroundColor Gray
        
        # Check port
        $port6060 = Get-NetTCPConnection -LocalPort 6060 -ErrorAction SilentlyContinue
        if ($port6060) {
            Write-Host "  [OK] Port 6060 is listening" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Port 6060 is not listening yet" -ForegroundColor Yellow
        }
    } elseif ($status -match "Restarting") {
        Write-Host "  [ERROR] Scheduler is still restarting" -ForegroundColor Red
        Write-Host "  [INFO] Checking logs for errors..." -ForegroundColor Yellow
        
        Start-Sleep -Seconds 3
        $errorLogs = docker logs jachin-dapr-scheduler-dev --tail 30 2>&1
        if ($errorLogs) {
            Write-Host ""
            Write-Host "  Recent error logs:" -ForegroundColor Red
            $errorLogs | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
        }
        
        Write-Host ""
        Write-Host "  [SOLUTION] Scheduler may have compatibility issues" -ForegroundColor Yellow
        Write-Host "  Option 1: Disable scheduler (recommended for development)" -ForegroundColor Gray
        Write-Host "    Comment out dapr-scheduler service in docker-compose.dev.yml" -ForegroundColor Gray
        Write-Host "  Option 2: Check Dapr version compatibility" -ForegroundColor Gray
        Write-Host "    docker pull daprio/dapr:latest" -ForegroundColor Gray
    } else {
        Write-Host "  [WARN] Scheduler status: $status" -ForegroundColor Yellow
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
Write-Host "      If it keeps restarting, you can disable it." -ForegroundColor Gray
Write-Host "      It only affects Actor Reminders feature." -ForegroundColor Gray
Write-Host "      Main API functions will work without it." -ForegroundColor Gray
Write-Host ""
