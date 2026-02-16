# Restart Dapr Scheduler with Fixed Configuration
# 使用修复后的配置重启 Dapr Scheduler

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
Write-Host "  [INFO] Using absolute path: /scheduler" -ForegroundColor Gray
Write-Host "  [INFO] Using data volume: dapr_scheduler_data:/data" -ForegroundColor Gray
Write-Host "  [INFO] Data directory: /data/scheduler" -ForegroundColor Gray
Write-Host "  [INFO] Running as root to avoid permission issues" -ForegroundColor Gray
Write-Host ""

docker-compose -f $composeFile -p $projectName up -d dapr-scheduler

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Scheduler container started" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "[3/3] Waiting for scheduler to initialize..." -ForegroundColor Cyan
    Start-Sleep -Seconds 15
    
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
            Write-Host "  [WARN] Port 6060 not listening yet (may need more time)" -ForegroundColor Yellow
        }
        
        # Check health endpoint
        try {
            $health = Invoke-WebRequest -Uri "http://localhost:8080/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($health.StatusCode -eq 200) {
                Write-Host "  [OK] Health check endpoint responding" -ForegroundColor Green
            }
        } catch {
            Write-Host "  [INFO] Health check not ready yet" -ForegroundColor Gray
        }
        
        Write-Host ""
        Write-Host "  [INFO] View logs: docker logs jachin-dapr-scheduler-dev" -ForegroundColor Gray
        Write-Host "  [INFO] Follow logs: docker logs -f jachin-dapr-scheduler-dev" -ForegroundColor Gray
    } elseif ($status -match "Restarting") {
        Write-Host "  [ERROR] Scheduler is still restarting" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Checking recent logs..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
        $errorLogs = docker logs jachin-dapr-scheduler-dev --tail 20 2>&1
        if ($errorLogs) {
            Write-Host ""
            Write-Host "  Recent logs:" -ForegroundColor Red
            $errorLogs | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
        }
        
        Write-Host ""
        Write-Host "  [TROUBLESHOOTING]" -ForegroundColor Yellow
        Write-Host "  1. Check full logs: docker logs jachin-dapr-scheduler-dev" -ForegroundColor Gray
        Write-Host "  2. Verify volume exists: docker volume ls | findstr scheduler" -ForegroundColor Gray
        Write-Host "  3. Check volume permissions: docker volume inspect jachin-dev_dapr_scheduler_data" -ForegroundColor Gray
    } else {
        Write-Host "  [WARN] Unexpected status: $status" -ForegroundColor Yellow
        Write-Host "  [INFO] Check logs: docker logs jachin-dapr-scheduler-dev" -ForegroundColor Gray
    }
} else {
    Write-Host "  [ERROR] Failed to start scheduler" -ForegroundColor Red
    Write-Host "  [INFO] Check configuration in docker-compose.dev.yml" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Restart Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
