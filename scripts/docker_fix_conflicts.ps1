# 修复 Docker 容器冲突（容器名已占用时使用，一眼看懂）
# Fix Docker Container Conflicts - resolve "container name is already in use"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Fixing Docker Container Conflicts" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

try {
    docker ps > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Docker is not running or not accessible" -ForegroundColor Red
        exit 1
    }
    Write-Host "[SUCCESS] Docker is running" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Cannot connect to Docker: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[INFO] Looking for conflicting containers..." -ForegroundColor Gray
$conflictContainers = @(
    "jachin-dapr-scheduler-dev",
    "jachin-dapr-placement-dev",
    "jachin-redis-dev",
    "jachin-mqtt-dev"
)

foreach ($containerName in $conflictContainers) {
    $container = docker ps -a --filter "name=$containerName" --format "{{.ID}}" 2>$null
    if ($container) {
        Write-Host "  Stopping and removing: $containerName" -ForegroundColor Yellow
        docker stop $containerName 2>&1 | Out-Null
        docker rm $containerName 2>&1 | Out-Null
        Write-Host "    [OK] Removed" -ForegroundColor Green
    }
}

Write-Host ""
$composeFile = Join-Path $ProjectRoot "docker-compose.dev.yml"
if (Test-Path $composeFile) {
    docker-compose -f $composeFile down 2>&1 | Out-Null
    Write-Host "[SUCCESS] Docker Compose services stopped" -ForegroundColor Green
}
docker container prune -f 2>&1 | Out-Null
Write-Host ""
Write-Host "Fix completed. Restart: .\scripts\start.ps1" -ForegroundColor Green
Write-Host ""
