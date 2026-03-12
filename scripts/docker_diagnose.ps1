# 诊断服务未运行原因（查看容器状态与日志，一眼看懂）
# Diagnose why services are not running

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$projectName = "jachin-dev"
$composeFile = Join-Path $ProjectRoot "docker-compose.dev.yml"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Diagnosing Service Issues" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/4] Checking Docker containers..." -ForegroundColor Cyan
$allContainers = docker ps -a --format "{{.Names}}\t{{.Status}}\t{{.Image}}" 2>&1
$jachinContainers = $allContainers | Select-String -Pattern "jachin-"

if ($jachinContainers) {
    foreach ($container in $jachinContainers) {
        $parts = $container -split "`t"
        if ($parts.Count -ge 2) {
            $name = $parts[0]
            $status = $parts[1]
            if ($status -match "Up") { Write-Host "  [OK] $name - $status" -ForegroundColor Green }
            elseif ($status -match "Exited") { Write-Host "  [ERROR] $name - $status" -ForegroundColor Red }
            else { Write-Host "  [WARN] $name - $status" -ForegroundColor Yellow }
        }
    }
} else {
    Write-Host "  [ERROR] No jachin containers found" -ForegroundColor Red
}

Write-Host ""
Write-Host "[2/4] Checking container logs..." -ForegroundColor Cyan
$containers = @("jachin-redis-dev", "jachin-mqtt-dev", "jachin-dapr-placement-dev", "jachin-dapr-scheduler-dev")
foreach ($containerName in $containers) {
    $exists = docker ps -a --format "{{.Names}}" | Select-String -Pattern "^${containerName}$"
    if ($exists) {
        $logs = docker logs --tail 5 $containerName 2>&1
        $errorLines = $logs | Select-String -Pattern "error|Error|ERROR|failed|Failed"
        if ($errorLines) { Write-Host "  [ERROR] $containerName : errors in logs" -ForegroundColor Red }
        else { Write-Host "  [OK] $containerName" -ForegroundColor Green }
    }
}

Write-Host ""
Write-Host "[3/4] Docker Compose status..." -ForegroundColor Cyan
docker-compose -f $composeFile -p $projectName ps 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }

Write-Host ""
Write-Host "[4/4] Recommendations..." -ForegroundColor Cyan
$exitedContainers = $jachinContainers | Where-Object { $_ -match "Exited" }
if ($exitedContainers) {
    Write-Host "  Try: .\scripts\docker_fix_conflicts.ps1 then .\scripts\start.ps1" -ForegroundColor Yellow
} elseif (-not $jachinContainers) {
    Write-Host "  No containers. Start: .\scripts\start.ps1" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] All containers running" -ForegroundColor Green
}
Write-Host ""
