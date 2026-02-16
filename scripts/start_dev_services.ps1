# Start Development Services with Progress Indicators
# 启动开发服务，带进度提示

param(
    [switch]$ForceRecreate,
    [switch]$NoProgress
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Starting Development Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if Docker is running
Write-Host "[1/5] Checking Docker..." -ForegroundColor Cyan
try {
    docker ps | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not running"
    }
    Write-Host "  [OK] Docker is running" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Docker is not running or not installed" -ForegroundColor Red
    Write-Host "  [INFO] Please start Docker Desktop and try again" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "[2/5] Checking for volume conflicts..." -ForegroundColor Cyan

# Check for existing volumes that might conflict
$conflictVolumes = docker volume ls --format "{{.Name}}" | Select-String -Pattern "jachin-system_redis_data|jachin-system_mqtt"
if ($conflictVolumes) {
    Write-Host "  [WARN] Found conflicting volumes:" -ForegroundColor Yellow
    $conflictVolumes | ForEach-Object { Write-Host "    - $_" -ForegroundColor Gray }
    
    if ($ForceRecreate) {
        Write-Host "  [INFO] Force recreate enabled, will recreate volumes" -ForegroundColor Gray
        $recreateFlag = "--force-recreate"
    } else {
        Write-Host "  [INFO] Volumes exist but may not match configuration" -ForegroundColor Yellow
        Write-Host "  [INFO] Use -ForceRecreate flag to recreate volumes (data will be lost)" -ForegroundColor Gray
        Write-Host "  [INFO] Continuing with existing volumes..." -ForegroundColor Gray
        $recreateFlag = ""
    }
} else {
    Write-Host "  [OK] No volume conflicts detected" -ForegroundColor Green
    $recreateFlag = ""
}

Write-Host ""
Write-Host "[3/5] Pulling Docker images (if needed)..." -ForegroundColor Cyan

# Pull images in background to show progress
$images = @(
    "redis:7-alpine",
    "eclipse-mosquitto:2.0",
    "daprio/dapr:latest"
)

$imageJobs = @()
foreach ($image in $images) {
    if (-not $NoProgress) {
        Write-Host "  [INFO] Checking $image..." -ForegroundColor Gray
    }
    $job = Start-Job -ScriptBlock {
        param($img)
        docker pull $img 2>&1 | Out-Null
        return $LASTEXITCODE
    } -ArgumentList $image
    $imageJobs += $job
}

# Wait for images with timeout
$timeout = 300 # 5 minutes
$elapsed = 0
$allDone = $false

while ($elapsed -lt $timeout -and -not $allDone) {
    Start-Sleep -Seconds 2
    $elapsed += 2
    
    $done = ($imageJobs | Where-Object { $_.State -eq "Completed" }).Count
    $total = $imageJobs.Count
    
    if (-not $NoProgress -and $elapsed % 10 -eq 0) {
        Write-Host "  [INFO] Pulling images... ($done/$total completed, ${elapsed}s elapsed)" -ForegroundColor Gray
    }
    
    if ($done -eq $total) {
        $allDone = $true
    }
}

if (-not $allDone) {
    Write-Host "  [WARN] Image pull timeout, continuing anyway..." -ForegroundColor Yellow
    $imageJobs | Stop-Job -ErrorAction SilentlyContinue
}

$imageJobs | Remove-Job -ErrorAction SilentlyContinue
Write-Host "  [OK] Images ready" -ForegroundColor Green

Write-Host ""
Write-Host "[4/5] Starting services..." -ForegroundColor Cyan

# Build docker-compose command with project name to avoid conflicts
$composeFile = "docker-compose.dev.yml"
$projectName = "jachin-dev"

# Try docker compose (v2) first, fallback to docker-compose (v1)
$dockerComposeCmd = "docker compose"
$testCmd = & docker compose version 2>&1
if ($LASTEXITCODE -ne 0) {
    $dockerComposeCmd = "docker-compose"
}

# Build command
$composeCmd = "$dockerComposeCmd -f $composeFile -p $projectName up -d"

if ($ForceRecreate) {
    $composeCmd += " --force-recreate"
}

if ($recreateFlag) {
    $composeCmd += " $recreateFlag"
}

Write-Host "  [INFO] Running: $composeCmd" -ForegroundColor Gray
Write-Host ""

# Run docker compose with progress indicator
$composeJob = Start-Job -ScriptBlock {
    param($cmd)
    Invoke-Expression $cmd 2>&1
    return $LASTEXITCODE
} -ArgumentList $composeCmd

# Show progress
$progressTimeout = 180 # 3 minutes
$progressElapsed = 0
$lastOutput = ""

while ($progressElapsed -lt $progressTimeout) {
    Start-Sleep -Seconds 1
    $progressElapsed++
    
    if ($composeJob.State -eq "Completed") {
        break
    }
    
    # Show progress every 5 seconds
    if ($progressElapsed % 5 -eq 0 -and -not $NoProgress) {
        Write-Host "  [INFO] Starting services... (${progressElapsed}s elapsed)" -ForegroundColor Gray
    }
}

# Get output
$composeOutput = Receive-Job -Job $composeJob -ErrorAction SilentlyContinue
$composeExitCode = $composeJob | Wait-Job -Timeout 5 | Receive-Job -ErrorAction SilentlyContinue
Remove-Job -Job $composeJob -ErrorAction SilentlyContinue

if ($composeExitCode -eq 0 -or $composeOutput -match "Created|Started|Up") {
    Write-Host "  [OK] Services started" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Some services may have issues, checking status..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[5/5] Checking service status..." -ForegroundColor Cyan

# Check service status
Start-Sleep -Seconds 3
    $services = & $dockerComposeCmd -f $composeFile -p $projectName ps --format json | ConvertFrom-Json

$allHealthy = $true
foreach ($service in $services) {
    $name = $service.Name
    $state = $service.State
    $health = $service.Health
    
    if ($state -eq "running") {
        if ($health -eq "healthy" -or [string]::IsNullOrEmpty($health)) {
            Write-Host "  [OK] $name - Running" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] $name - Running but not healthy ($health)" -ForegroundColor Yellow
            $allHealthy = $false
        }
    } elseif ($state -eq "exited") {
        Write-Host "  [ERROR] $name - Exited" -ForegroundColor Red
        $allHealthy = $false
    } else {
        Write-Host "  [INFO] $name - $state" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

if ($allHealthy) {
    Write-Host "  [SUCCESS] All services started successfully!" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Some services may need attention" -ForegroundColor Yellow
    Write-Host "  [INFO] Check logs: $dockerComposeCmd -f $composeFile logs" -ForegroundColor Gray
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  View logs:    $dockerComposeCmd -f $composeFile -p $projectName logs -f" -ForegroundColor Gray
Write-Host "  Stop services: $dockerComposeCmd -f $composeFile -p $projectName down" -ForegroundColor Gray
Write-Host "  Service status: $dockerComposeCmd -f $composeFile -p $projectName ps" -ForegroundColor Gray
Write-Host ""
