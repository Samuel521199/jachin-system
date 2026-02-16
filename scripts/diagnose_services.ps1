# Diagnose why services are not running
# 诊断服务未运行的原因

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Diagnosing Service Issues" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

Write-Host "[1/4] Checking Docker containers..." -ForegroundColor Cyan
$allContainers = docker ps -a --format "{{.Names}}\t{{.Status}}\t{{.Image}}" 2>&1
$jachinContainers = $allContainers | Select-String -Pattern "jachin-"

if ($jachinContainers) {
    Write-Host "  [INFO] Found containers:" -ForegroundColor Yellow
    foreach ($container in $jachinContainers) {
        $parts = $container -split "`t"
        if ($parts.Count -ge 2) {
            $name = $parts[0]
            $status = $parts[1]
            $image = if ($parts.Count -ge 3) { $parts[2] } else { "" }
            
            if ($status -match "Up") {
                Write-Host "    [OK] $name - $status" -ForegroundColor Green
            } elseif ($status -match "Exited") {
                Write-Host "    [ERROR] $name - $status" -ForegroundColor Red
                Write-Host "      Image: $image" -ForegroundColor Gray
            } else {
                Write-Host "    [WARN] $name - $status" -ForegroundColor Yellow
            }
        }
    }
} else {
    Write-Host "  [ERROR] No jachin containers found" -ForegroundColor Red
    Write-Host "  [INFO] Services may not have started" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[2/4] Checking container logs..." -ForegroundColor Cyan

$containers = @("jachin-redis-dev", "jachin-mqtt-dev", "jachin-dapr-placement-dev", "jachin-dapr-scheduler-dev")

foreach ($containerName in $containers) {
    $exists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^${containerName}$"
    if ($exists) {
        Write-Host "  [INFO] Checking $containerName..." -ForegroundColor Gray
        $logs = docker logs --tail 5 $containerName 2>&1
        if ($logs) {
            $errorLines = $logs | Select-String -Pattern "error|Error|ERROR|failed|Failed|FAILED"
            if ($errorLines) {
                Write-Host "    [ERROR] Found errors:" -ForegroundColor Red
                $errorLines | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkRed }
            } else {
                Write-Host "    [OK] No obvious errors in recent logs" -ForegroundColor Green
            }
        }
    }
}

Write-Host ""
Write-Host "[3/4] Checking docker-compose status..." -ForegroundColor Cyan

$composeStatus = docker-compose -f $composeFile -p $projectName ps 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [INFO] Docker Compose status:" -ForegroundColor Gray
    Write-Host $composeStatus -ForegroundColor Gray
} else {
    Write-Host "  [ERROR] Cannot get docker-compose status: $composeStatus" -ForegroundColor Red
}

Write-Host ""
Write-Host "[4/4] Recommendations..." -ForegroundColor Cyan

$exitedContainers = $jachinContainers | Where-Object { $_ -match "Exited" }
if ($exitedContainers) {
    Write-Host "  [WARN] Found exited containers" -ForegroundColor Yellow
    Write-Host "  [INFO] Try restarting services:" -ForegroundColor Gray
    Write-Host "    docker-compose -f $composeFile -p $projectName up -d" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [INFO] Or check detailed logs:" -ForegroundColor Gray
    Write-Host "    docker-compose -f $composeFile -p $projectName logs" -ForegroundColor DarkGray
} elseif (-not $jachinContainers) {
    Write-Host "  [INFO] No containers found. Starting services..." -ForegroundColor Yellow
    Write-Host "  [INFO] Running: docker-compose -f $composeFile -p $projectName up -d" -ForegroundColor Gray
    
    docker-compose -f $composeFile -p $projectName up -d
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Services started" -ForegroundColor Green
        Write-Host "  [INFO] Waiting 5 seconds for services to initialize..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
        
        # Check again
        $newContainers = docker ps --format "{{.Names}}" | Select-String -Pattern "jachin-"
        if ($newContainers) {
            Write-Host "  [SUCCESS] Services are now running:" -ForegroundColor Green
            $newContainers | ForEach-Object { Write-Host "    - $_" -ForegroundColor Gray }
        }
    } else {
        Write-Host "  [ERROR] Failed to start services" -ForegroundColor Red
        Write-Host "  [INFO] Check logs: docker-compose -f $composeFile -p $projectName logs" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] All containers appear to be running" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Diagnosis Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
