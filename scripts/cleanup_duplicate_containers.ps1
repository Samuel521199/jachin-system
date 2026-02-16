# Cleanup Duplicate and Stopped Containers
# 清理重复和已停止的容器

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Cleaning Up Docker Containers" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# List all jachin containers
Write-Host "[1/3] Finding all jachin-related containers..." -ForegroundColor Cyan
$allContainers = docker ps -a --filter "name=jachin" --format "{{.Names}}\t{{.Status}}\t{{.Image}}" 2>&1

if (-not $allContainers -or $allContainers -match "error") {
    Write-Host "  [ERROR] Could not list containers" -ForegroundColor Red
    Write-Host "  [INFO] Make sure Docker is running" -ForegroundColor Yellow
    exit 1
}

Write-Host "  Found containers:" -ForegroundColor Gray
$containers = @()
$allContainers | ForEach-Object {
    $parts = $_ -split "`t"
    $name = $parts[0]
    $status = $parts[1]
    $image = $parts[2]
    
    $container = @{
        Name = $name
        Status = $status
        Image = $image
    }
    $containers += $container
    
    if ($status -match "Up|Running") {
        Write-Host "    [RUNNING] $name" -ForegroundColor Green
    } elseif ($status -match "Exited|Stopped") {
        Write-Host "    [STOPPED] $name" -ForegroundColor Yellow
    } elseif ($status -match "Restarting") {
        Write-Host "    [RESTARTING] $name" -ForegroundColor Red
    } else {
        Write-Host "    [$status] $name" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "[2/3] Identifying containers to remove..." -ForegroundColor Cyan

# Expected containers (keep these)
$expectedContainers = @(
    "jachin-redis-dev",
    "jachin-mqtt-dev",
    "jachin-dapr-placement-dev",
    "jachin-dapr-scheduler-dev"
)

# Find containers to remove
$toRemove = @()
$duplicates = @{}

foreach ($container in $containers) {
    $name = $container.Name
    
    # Check if it's an expected container
    $isExpected = $false
    foreach ($expected in $expectedContainers) {
        if ($name -eq $expected) {
            $isExpected = $true
            break
        }
    }
    
    # If not expected, mark for removal
    if (-not $isExpected) {
        # Check if it's a duplicate (same base name but different suffix)
        $baseName = $name -replace '-dev$', '' -replace '^jachin-', ''
        $hasDuplicate = $false
        
        foreach ($expected in $expectedContainers) {
            $expectedBase = $expected -replace '-dev$', '' -replace '^jachin-', ''
            if ($baseName -eq $expectedBase -and $name -ne $expected) {
                $hasDuplicate = $true
                if (-not $duplicates.ContainsKey($expectedBase)) {
                    $duplicates[$expectedBase] = @()
                }
                $duplicates[$expectedBase] += $name
                break
            }
        }
        
        if (-not $hasDuplicate) {
            # Old or misconfigured container
            $toRemove += $container
        }
    } elseif ($container.Status -match "Exited|Stopped|Restarting") {
        # Expected container but stopped/restarting - ask user
        Write-Host "  [WARN] Expected container '$name' is $($container.Status)" -ForegroundColor Yellow
        Write-Host "    This container should be running. Do you want to remove and recreate it?" -ForegroundColor Gray
    }
}

# Also find stopped duplicates
foreach ($key in $duplicates.Keys) {
    foreach ($dupName in $duplicates[$key]) {
        $dupContainer = $containers | Where-Object { $_.Name -eq $dupName }
        if ($dupContainer) {
            $toRemove += $dupContainer
        }
    }
}

# Find stopped containers
$stoppedContainers = $containers | Where-Object { $_.Status -match "Exited|Stopped" }

Write-Host ""
if ($toRemove.Count -gt 0) {
    Write-Host "  Containers to remove:" -ForegroundColor Yellow
    foreach ($container in $toRemove) {
        Write-Host "    - $($container.Name) ($($container.Status))" -ForegroundColor Gray
    }
} else {
    Write-Host "  [OK] No duplicate containers found" -ForegroundColor Green
}

if ($stoppedContainers.Count -gt 0) {
    Write-Host ""
    Write-Host "  Stopped containers:" -ForegroundColor Yellow
    foreach ($container in $stoppedContainers) {
        $isExpected = $expectedContainers -contains $container.Name
        if ($isExpected) {
            Write-Host "    - $($container.Name) ($($container.Status)) [EXPECTED - will restart]" -ForegroundColor Gray
        } else {
            Write-Host "    - $($container.Name) ($($container.Status)) [CAN REMOVE]" -ForegroundColor DarkGray
            if ($toRemove -notcontains $container) {
                $toRemove += $container
            }
        }
    }
}

Write-Host ""
Write-Host "[3/3] Removing containers..." -ForegroundColor Cyan

if ($toRemove.Count -eq 0) {
    Write-Host "  [INFO] No containers to remove" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  [INFO] To remove all stopped containers:" -ForegroundColor Gray
    Write-Host "    docker container prune --filter 'name=jachin' -f" -ForegroundColor DarkGray
} else {
    Write-Host "  [WARN] About to remove $($toRemove.Count) container(s)" -ForegroundColor Yellow
    Write-Host ""
    $confirm = Read-Host "Continue? (y/N)"
    
    if ($confirm -eq "y" -or $confirm -eq "Y") {
        foreach ($container in $toRemove) {
            Write-Host "  Removing $($container.Name)..." -ForegroundColor Gray
            docker rm -f $container.Name 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    [OK] Removed" -ForegroundColor Green
            } else {
                Write-Host "    [ERROR] Failed to remove" -ForegroundColor Red
            }
        }
        Write-Host ""
        Write-Host "  [SUCCESS] Cleanup complete" -ForegroundColor Green
    } else {
        Write-Host "  [INFO] Cleanup cancelled" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Cleanup Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Check service status: .\scripts\check_dev_services.ps1" -ForegroundColor Gray
Write-Host "  2. Start missing services: .\scripts\start_all.ps1" -ForegroundColor Gray
Write-Host ""
