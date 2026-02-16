# Setup Docker volumes on E: drive
# 在E盘设置Docker volumes

param(
    [string]$VolumesPath = "E:\docker\volumes"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Docker Volumes on E: Drive" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Create volumes directory structure
Write-Host "[1/2] Creating volumes directory structure..." -ForegroundColor Yellow

$volumeDirs = @(
    "postgres_data",
    "qdrant_data",
    "redis_data",
    "mqtt_data",
    "mqtt_logs",
    "ray_data"
)

foreach ($dir in $volumeDirs) {
    $dirPath = Join-Path $VolumesPath $dir
    if (-not (Test-Path $dirPath)) {
        New-Item -ItemType Directory -Path $dirPath -Force | Out-Null
        Write-Host "  [OK] Created: $dirPath" -ForegroundColor Green
    } else {
        Write-Host "  [INFO] Already exists: $dirPath" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[2/2] Next steps..." -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Edit docker-compose.yml and uncomment volume configurations" -ForegroundColor Cyan
Write-Host "2. Or use the example file: docker-compose.yml.e_drive" -ForegroundColor Cyan
Write-Host "3. Restart services: docker-compose down && docker-compose up -d" -ForegroundColor Cyan
Write-Host ""
Write-Host "Volumes will be stored at: $VolumesPath" -ForegroundColor Green
Write-Host ""
