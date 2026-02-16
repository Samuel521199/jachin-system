# Restart Ray Head container with new configuration
# 重启 Ray Head 容器以应用新配置（包括 shm_size 和 restart 策略）

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Restarting Ray Head Container" -ForegroundColor Cyan
Write-Host "重启 Ray Head 容器" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Docker Desktop
Write-Host "[1/4] Checking Docker Desktop..." -ForegroundColor Yellow
try {
    docker ps | Out-Null
    Write-Host "[OK] Docker Desktop is running" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Docker Desktop is not running!" -ForegroundColor Red
    Write-Host "Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}
Write-Host ""

# Stop and remove existing container
Write-Host "[2/4] Stopping and removing existing Ray Head container..." -ForegroundColor Yellow
docker-compose -f docker-compose.minimal.yml stop ray-head 2>&1 | Out-Null
docker-compose -f docker-compose.minimal.yml rm -f ray-head 2>&1 | Out-Null
Write-Host "[OK] Container stopped and removed" -ForegroundColor Green
Write-Host ""

# Recreate container with new configuration
Write-Host "[3/4] Creating Ray Head container with new configuration..." -ForegroundColor Yellow
Write-Host "  - shm_size: 4gb (shared memory)" -ForegroundColor Gray
Write-Host "  - restart: always (auto-restart on exit)" -ForegroundColor Gray
Write-Host "  - command: ray start + tail (keeps container running)" -ForegroundColor Gray
Write-Host ""

docker-compose -f docker-compose.minimal.yml up -d ray-head

if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Container created successfully" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Failed to create container" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Wait for Ray to start
Write-Host "[4/4] Waiting for Ray to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Check container status
Write-Host ""
Write-Host "Checking container status..." -ForegroundColor Yellow
docker ps --filter "name=jachin-ray-head" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

Write-Host ""
Write-Host "Checking Ray Dashboard..." -ForegroundColor Yellow
$maxRetries = 10
$retryCount = 0
$dashboardReady = $false

while ($retryCount -lt $maxRetries -and -not $dashboardReady) {
    Start-Sleep -Seconds 2
    $retryCount++
    
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8265" -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            $dashboardReady = $true
            Write-Host "[OK] Ray Dashboard is accessible at http://localhost:8265" -ForegroundColor Green
        }
    } catch {
        Write-Host "  Attempt ${retryCount}/${maxRetries}: Waiting for dashboard..." -ForegroundColor Gray
    }
}

if (-not $dashboardReady) {
    Write-Host "[WARNING] Ray Dashboard not responding yet. Check logs with:" -ForegroundColor Yellow
    Write-Host "  docker logs jachin-ray-head" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Ray Head container restarted successfully!" -ForegroundColor Green
Write-Host "Ray Head 容器已成功重启！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Yellow
Write-Host "  View logs:     docker logs -f jachin-ray-head" -ForegroundColor Gray
Write-Host "  Check status:  docker ps --filter name=jachin-ray-head" -ForegroundColor Gray
Write-Host "  Dashboard:     http://localhost:8265" -ForegroundColor Gray
Write-Host ""
