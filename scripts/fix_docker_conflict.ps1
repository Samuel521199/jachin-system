# Fix Docker Container Conflicts
# Resolve "container name is already in use" errors

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Fixing Docker Container Conflicts" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check if Docker is running
Write-Host "[INFO] Checking Docker..." -ForegroundColor Gray
try {
    docker ps > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Docker is not running or not accessible" -ForegroundColor Red
        Write-Host "  Please ensure Docker Desktop is running" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[SUCCESS] Docker is running" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Cannot connect to Docker: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Stop and remove conflicting containers
Write-Host "[INFO] Looking for conflicting containers..." -ForegroundColor Gray
$conflictContainers = @(
    "jachin-dapr-scheduler-dev",
    "jachin-dapr-placement-dev",
    "jachin-redis-dev",
    "jachin-mqtt-dev"
)

foreach ($containerName in $conflictContainers) {
    Write-Host "  Checking container: $containerName" -ForegroundColor DarkGray
    
    # Check if container exists
    $container = docker ps -a --filter "name=$containerName" --format "{{.ID}}" 2>$null
    if ($container) {
        Write-Host "    [FOUND] Found container: $containerName" -ForegroundColor Yellow
        
        # Stop container
        Write-Host "    [INFO] Stopping container..." -ForegroundColor DarkGray
        docker stop $containerName 2>&1 | Out-Null
        
        # Remove container
        Write-Host "    [INFO] Removing container..." -ForegroundColor DarkGray
        docker rm $containerName 2>&1 | Out-Null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    [SUCCESS] Removed container: $containerName" -ForegroundColor Green
        } else {
            Write-Host "    [WARN] Failed to remove container, may not exist" -ForegroundColor Yellow
        }
    } else {
        Write-Host "    [OK] Container does not exist" -ForegroundColor DarkGray
    }
}

Write-Host ""

# Stop all related services
Write-Host "[INFO] Stopping Docker Compose services..." -ForegroundColor Gray
$composeFile = "docker-compose.dev.yml"
if (Test-Path $composeFile) {
    docker-compose -f $composeFile down 2>&1 | Out-Null
    Write-Host "[SUCCESS] Docker Compose services stopped" -ForegroundColor Green
} else {
    Write-Host "[WARN] docker-compose.dev.yml not found" -ForegroundColor Yellow
}

Write-Host ""

# Clean up unused containers
Write-Host "[INFO] Cleaning up unused containers..." -ForegroundColor Gray
docker container prune -f 2>&1 | Out-Null
Write-Host "[SUCCESS] Cleanup completed" -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Fix completed!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "You can now restart the services:" -ForegroundColor Yellow
Write-Host "  .\start.bat" -ForegroundColor Cyan
Write-Host "  or" -ForegroundColor Gray
Write-Host "  .\scripts\start.ps1" -ForegroundColor Cyan
Write-Host ""
