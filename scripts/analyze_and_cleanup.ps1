# Analyze and Cleanup Docker Containers
# 分析和清理 Docker 容器

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Docker Container Analysis" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Based on Docker Desktop view, here's what we found:" -ForegroundColor Cyan
Write-Host ""

# Expected containers for jachin-dev project
Write-Host "[Expected Containers for jachin-dev project:]" -ForegroundColor Green
$expectedContainers = @(
    @{Name="jachin-redis-dev"; Status="Should be running"; Port="6379"},
    @{Name="jachin-mqtt-dev"; Status="Should be running"; Port="1883, 9001"},
    @{Name="jachin-dapr-placement-dev"; Status="Should be running"; Port="6050"},
    @{Name="jachin-dapr-scheduler-dev"; Status="Should be running"; Port="6060"}
)

foreach ($container in $expectedContainers) {
    Write-Host "  ✓ $($container.Name) - $($container.Status) (Port: $($container.Port))" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[Containers Found in Docker Desktop:]" -ForegroundColor Yellow

# Based on screenshot analysis
$foundContainers = @(
    @{Name="jachin-redis-c"; Expected="jachin-redis-dev"; Status="Running"; Action="Rename or keep if working"},
    @{Name="jachin-mqtt-d"; Expected="jachin-mqtt-dev"; Status="Running"; Action="Rename or keep if working"},
    @{Name="jachin-dapr-p"; Expected="jachin-dapr-placement-dev"; Status="Running"; Port="6050"; Action="Rename or keep if working"},
    @{Name="jachin-dapr-s"; Expected="jachin-dapr-scheduler-dev"; Status="Not running"; Action="Fix and start"},
    @{Name="jachin-system"; Expected="None"; Status="No image/ID"; Action="DELETE - Invalid container"},
    @{Name="jachin-postgr"; Expected="None (should be local)"; Status="Running"; Action="DELETE - Should use local PostgreSQL"},
    @{Name="jachin-ray-he"; Expected="None (optional)"; Status="Running"; Action="Keep if needed, or DELETE"}
)

foreach ($container in $foundContainers) {
    if ($container.Action -match "DELETE") {
        Write-Host "  [DELETE] $($container.Name) - $($container.Status)" -ForegroundColor Red
        Write-Host "           Reason: $($container.Action)" -ForegroundColor DarkRed
    } elseif ($container.Status -match "Not running") {
        Write-Host "  [FIX] $($container.Name) - $($container.Status)" -ForegroundColor Yellow
        Write-Host "        Expected: $($container.Expected)" -ForegroundColor Gray
    } else {
        Write-Host "  [KEEP] $($container.Name) - $($container.Status)" -ForegroundColor Green
        if ($container.Expected -ne "None") {
            Write-Host "          Expected name: $($container.Expected)" -ForegroundColor DarkGray
        }
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Recommendations" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[Containers to DELETE:]" -ForegroundColor Red
Write-Host "  1. jachin-system" -ForegroundColor Yellow
Write-Host "     Reason: No image, no ID, invalid container" -ForegroundColor Gray
Write-Host "     Command: docker rm -f jachin-system" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  2. jachin-postgr (if PostgreSQL should be local)" -ForegroundColor Yellow
Write-Host "     Reason: PostgreSQL should run locally, not in Docker for dev" -ForegroundColor Gray
Write-Host "     Command: docker stop jachin-postgr && docker rm jachin-postgr" -ForegroundColor DarkGray
Write-Host "     Note: Make sure local PostgreSQL is running first!" -ForegroundColor DarkYellow
Write-Host ""

Write-Host "[Containers to FIX:]" -ForegroundColor Yellow
Write-Host "  1. jachin-dapr-s (Dapr Scheduler)" -ForegroundColor Yellow
Write-Host "     Status: Not running" -ForegroundColor Gray
Write-Host "     Action: Run .\scripts\restart_dapr_scheduler.ps1" -ForegroundColor Gray
Write-Host ""

Write-Host "[Containers to RENAME (optional):]" -ForegroundColor Cyan
Write-Host "  If containers work but have wrong names:" -ForegroundColor Gray
Write-Host "  - jachin-redis-c → jachin-redis-dev" -ForegroundColor DarkGray
Write-Host "  - jachin-mqtt-d → jachin-mqtt-dev" -ForegroundColor DarkGray
Write-Host "  - jachin-dapr-p → jachin-dapr-placement-dev" -ForegroundColor DarkGray
Write-Host "  Note: Renaming requires recreating containers" -ForegroundColor DarkYellow
Write-Host ""

Write-Host "[Services Expected Locally (not in Docker):]" -ForegroundColor Green
Write-Host "  ✓ PostgreSQL (port 5432) - Should run locally" -ForegroundColor Gray
Write-Host "  ✓ Qdrant (port 6333) - Should run locally" -ForegroundColor Gray
Write-Host "  ✓ Backend API (port 18888) - Should run locally" -ForegroundColor Gray
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Quick Cleanup Commands" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "# Remove invalid container" -ForegroundColor Gray
Write-Host "docker rm -f jachin-system" -ForegroundColor DarkGray
Write-Host ""
Write-Host "# Remove PostgreSQL container (if using local PostgreSQL)" -ForegroundColor Gray
Write-Host "docker stop jachin-postgr && docker rm jachin-postgr" -ForegroundColor DarkGray
Write-Host ""
Write-Host "# Remove Ray container (if not needed)" -ForegroundColor Gray
Write-Host "docker stop jachin-ray-he && docker rm jachin-ray-he" -ForegroundColor DarkGray
Write-Host ""
Write-Host "# Remove all stopped containers" -ForegroundColor Gray
Write-Host "docker container prune --filter 'name=jachin' -f" -ForegroundColor DarkGray
Write-Host ""
Write-Host "# Fix Dapr Scheduler" -ForegroundColor Gray
Write-Host ".\scripts\restart_dapr_scheduler.ps1" -ForegroundColor DarkGray
Write-Host ""
