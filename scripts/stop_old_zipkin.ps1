# Stop old Zipkin container script
# 停止旧的Zipkin容器脚本

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Stopping Old Zipkin Container" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Find zipkin containers
Write-Host "[1/3] Finding Zipkin containers..." -ForegroundColor Yellow
$zipkinContainers = docker ps -a --filter "name=zipkin" --format "{{.Names}}"

if ($zipkinContainers) {
    Write-Host "  Found Zipkin containers:" -ForegroundColor Cyan
    $zipkinContainers | ForEach-Object {
        Write-Host "    - $_" -ForegroundColor White
    }
    
    Write-Host ""
    Write-Host "[2/3] Stopping containers..." -ForegroundColor Yellow
    
    $zipkinContainers | ForEach-Object {
        $containerName = $_
        Write-Host "  Stopping $containerName..." -ForegroundColor Yellow
        
        docker stop $containerName 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    [OK] Stopped $containerName" -ForegroundColor Green
        } else {
            Write-Host "    [WARN] Failed to stop $containerName (may already be stopped)" -ForegroundColor Yellow
        }
    }
    
    Write-Host ""
    Write-Host "[3/3] Removing containers..." -ForegroundColor Yellow
    
    $zipkinContainers | ForEach-Object {
        $containerName = $_
        Write-Host "  Removing $containerName..." -ForegroundColor Yellow
        
        docker rm $containerName 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    [OK] Removed $containerName" -ForegroundColor Green
        } else {
            Write-Host "    [WARN] Failed to remove $containerName" -ForegroundColor Yellow
        }
    }
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Old Zipkin containers cleaned up" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "You can now start services with:" -ForegroundColor Cyan
    Write-Host "  docker-compose up -d" -ForegroundColor White
    Write-Host ""
} else {
    Write-Host "  [INFO] No Zipkin containers found" -ForegroundColor Green
    Write-Host ""
}
