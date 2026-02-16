# Fix Docker Volume Conflicts
# 修复 Docker Volume 冲突

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fixing Docker Volume Conflicts" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

Write-Host "[1/3] Checking for conflicting volumes..." -ForegroundColor Cyan

# List volumes with old naming
$oldVolumes = docker volume ls --format "{{.Name}}" | Select-String -Pattern "jachin-system_"
if ($oldVolumes) {
    Write-Host "  [INFO] Found volumes with old naming:" -ForegroundColor Yellow
    $oldVolumes | ForEach-Object { Write-Host "    - $_" -ForegroundColor Gray }
    
    Write-Host ""
    Write-Host "Options:" -ForegroundColor Cyan
    Write-Host "  1. Remove old volumes (data will be lost)" -ForegroundColor Gray
    Write-Host "  2. Keep old volumes and use them" -ForegroundColor Gray
    Write-Host "  3. Cancel" -ForegroundColor Gray
    Write-Host ""
    
    $choice = Read-Host "Enter choice (1/2/3)"
    
    switch ($choice) {
        "1" {
            Write-Host ""
            Write-Host "[2/3] Removing old volumes..." -ForegroundColor Cyan
            foreach ($vol in $oldVolumes) {
                Write-Host "  [INFO] Removing $vol..." -ForegroundColor Gray
                docker volume rm $vol 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [OK] Removed $vol" -ForegroundColor Green
                } else {
                    Write-Host "  [WARN] Failed to remove $vol (may be in use)" -ForegroundColor Yellow
                }
            }
        }
        "2" {
            Write-Host ""
            Write-Host "[2/3] Keeping old volumes..." -ForegroundColor Cyan
            Write-Host "  [INFO] Old volumes will be reused" -ForegroundColor Gray
        }
        "3" {
            Write-Host "[INFO] Cancelled" -ForegroundColor Yellow
            exit 0
        }
        default {
            Write-Host "[ERROR] Invalid choice" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host "  [OK] No conflicting volumes found" -ForegroundColor Green
}

Write-Host ""
Write-Host "[3/3] Starting services with correct project name..." -ForegroundColor Cyan

# Stop any existing services
Write-Host "  [INFO] Stopping existing services..." -ForegroundColor Gray
docker-compose -f $composeFile -p $projectName down 2>&1 | Out-Null

# Start services
Write-Host "  [INFO] Starting services..." -ForegroundColor Gray
docker-compose -f $composeFile -p $projectName up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Services started successfully" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Failed to start services" -ForegroundColor Red
    Write-Host "  [INFO] Check logs: docker-compose -f $composeFile -p $projectName logs" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Volume conflict fixed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Services are now running with project name: $projectName" -ForegroundColor Cyan
Write-Host "Use this command for future operations:" -ForegroundColor Gray
Write-Host "  docker-compose -f $composeFile -p $projectName [command]" -ForegroundColor DarkGray
Write-Host ""
