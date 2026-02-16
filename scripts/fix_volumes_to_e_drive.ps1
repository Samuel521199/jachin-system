# Fix Docker Compose volumes to use E: drive bind mounts
# 修复 Docker Compose volumes 使用 E 盘 bind mount

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fix Volumes to E: Drive" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Stop and remove containers
Write-Host "[1/4] Stopping and removing containers..." -ForegroundColor Yellow

docker-compose -f docker-compose.minimal.yml down -v 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Containers stopped and removed" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Some containers may not exist" -ForegroundColor Yellow
}

# Step 2: Remove Docker-managed volumes
Write-Host ""
Write-Host "[2/4] Removing Docker-managed volumes..." -ForegroundColor Yellow

$volumesToRemove = @(
    "jachin-system_postgres_data",
    "jachin-system_qdrant_data",
    "jachin-system_redis_data",
    "jachin-system_ray_data"
)

foreach ($volume in $volumesToRemove) {
    $result = docker volume rm $volume 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Removed volume: $volume" -ForegroundColor Green
    } else {
        Write-Host "  [INFO] Volume not found: $volume" -ForegroundColor Gray
    }
}

# Step 3: Verify E: drive directories exist
Write-Host ""
Write-Host "[3/4] Verifying E: drive directories..." -ForegroundColor Yellow

$eDriveDirs = @(
    "E:\docker\volumes\postgres_data",
    "E:\docker\volumes\qdrant_data",
    "E:\docker\volumes\redis_data",
    "E:\docker\volumes\ray_data"
)

foreach ($dir in $eDriveDirs) {
    if (Test-Path $dir) {
        Write-Host "  [OK] Exists: $dir" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Missing: $dir" -ForegroundColor Yellow
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "    [OK] Created" -ForegroundColor Green
    }
}

# Step 4: Restart with E: drive bind mounts
Write-Host ""
Write-Host "[4/4] Restarting with E: drive bind mounts..." -ForegroundColor Yellow

Write-Host "  Starting containers..." -ForegroundColor Cyan
docker-compose -f docker-compose.minimal.yml up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host "    [OK] Containers started" -ForegroundColor Green
} else {
    Write-Host "    [ERROR] Failed to start containers" -ForegroundColor Red
    exit 1
}

# Wait a bit for containers to start
Start-Sleep -Seconds 5

# Verify volumes are using E: drive
Write-Host ""
Write-Host "Verifying volumes..." -ForegroundColor Yellow

$containers = @(
    "jachin-postgres",
    "jachin-qdrant",
    "jachin-redis",
    "jachin-ray-head"
)

foreach ($container in $containers) {
    $inspect = docker inspect $container --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' 2>&1
    if ($inspect -match "E:\\docker") {
        Write-Host "  [OK] $container is using E: drive" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] $container may not be using E: drive" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Fix Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Check container status:" -ForegroundColor Cyan
Write-Host "  docker-compose -f docker-compose.minimal.yml ps" -ForegroundColor Gray
Write-Host ""
Write-Host "Check volumes:" -ForegroundColor Cyan
Write-Host "  docker volume ls" -ForegroundColor Gray
Write-Host ""
