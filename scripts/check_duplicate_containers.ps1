# Check for Duplicate Containers and Missing Services
# 检查重复容器和缺失服务

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Checking Docker Containers" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Get all containers (including stopped)
Write-Host "[1/4] Listing all jachin-related containers..." -ForegroundColor Cyan
$allContainers = docker ps -a --filter "name=jachin" --format "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}" 2>&1

if ($allContainers -and $allContainers -notmatch "error") {
    Write-Host "  Found containers:" -ForegroundColor Gray
    $allContainers | ForEach-Object {
        $parts = $_ -split "`t"
        $name = $parts[0]
        $status = $parts[1]
        $image = $parts[2]
        $ports = $parts[3]
        
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
} else {
    Write-Host "  [WARN] Could not list containers" -ForegroundColor Yellow
    Write-Host "  [INFO] Error: $allContainers" -ForegroundColor Gray
}

Write-Host ""
Write-Host "[2/4] Checking for duplicate containers..." -ForegroundColor Cyan

# Check for duplicates by name pattern
$containerNames = @()
if ($allContainers) {
    $containerNames = ($allContainers | ForEach-Object { ($_ -split "`t")[0] })
}

$duplicates = @{}
foreach ($name in $containerNames) {
    $baseName = $name -replace '-dev$', '' -replace '^jachin-', ''
    if (-not $duplicates.ContainsKey($baseName)) {
        $duplicates[$baseName] = @()
    }
    $duplicates[$baseName] += $name
}

$foundDuplicates = $false
foreach ($key in $duplicates.Keys) {
    if ($duplicates[$key].Count -gt 1) {
        $foundDuplicates = $true
        Write-Host "  [WARN] Found duplicates for '$key':" -ForegroundColor Yellow
        foreach ($dupName in $duplicates[$key]) {
            $status = ($allContainers | Where-Object { $_ -match $dupName } | ForEach-Object { ($_ -split "`t")[1] })
            Write-Host "    - $dupName ($status)" -ForegroundColor Gray
        }
    }
}

if (-not $foundDuplicates) {
    Write-Host "  [OK] No duplicate containers found" -ForegroundColor Green
}

Write-Host ""
Write-Host "[3/4] Checking expected Docker services..." -ForegroundColor Cyan

$expectedContainers = @(
    @{Name="jachin-redis-dev"; Service="Redis"; Port=6379},
    @{Name="jachin-mqtt-dev"; Service="MQTT"; Port=1883},
    @{Name="jachin-dapr-placement-dev"; Service="Dapr Placement"; Port=6050},
    @{Name="jachin-dapr-scheduler-dev"; Service="Dapr Scheduler"; Port=6060}
)

$missingContainers = @()
foreach ($expected in $expectedContainers) {
    $found = $containerNames | Where-Object { $_ -eq $expected.Name }
    if ($found) {
        $status = ($allContainers | Where-Object { $_ -match $expected.Name } | ForEach-Object { ($_ -split "`t")[1] })
        if ($status -match "Up|Running") {
            Write-Host "  [OK] $($expected.Service) ($($expected.Name)) - Running" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] $($expected.Service) ($($expected.Name)) - $status" -ForegroundColor Yellow
            $missingContainers += $expected
        }
    } else {
        Write-Host "  [ERROR] $($expected.Service) ($($expected.Name)) - Not found" -ForegroundColor Red
        $missingContainers += $expected
    }
}

Write-Host ""
Write-Host "[4/4] Checking local services (not in Docker)..." -ForegroundColor Cyan

# Check PostgreSQL (local)
Write-Host "  Checking PostgreSQL (local, port 5432)..." -ForegroundColor Gray
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    try {
        $env:PGPASSWORD = "secure_password"
        $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    [OK] PostgreSQL - Running and accessible" -ForegroundColor Green
        } else {
            Write-Host "    [WARN] PostgreSQL - Port open but connection failed" -ForegroundColor Yellow
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } catch {
        Write-Host "    [WARN] PostgreSQL - Cannot test connection" -ForegroundColor Yellow
    }
} else {
    Write-Host "    [ERROR] PostgreSQL - Not running on port 5432" -ForegroundColor Red
}

# Check Qdrant (local)
Write-Host "  Checking Qdrant (local, port 6333)..." -ForegroundColor Gray
$qdrantPort = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($qdrantPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "    [OK] Qdrant - Running and healthy" -ForegroundColor Green
        }
    } catch {
        try {
            $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($health.StatusCode -eq 200) {
                Write-Host "    [OK] Qdrant - Running and healthy" -ForegroundColor Green
            }
        } catch {
            Write-Host "    [WARN] Qdrant - Port open but health check failed" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "    [ERROR] Qdrant - Not running on port 6333" -ForegroundColor Red
}

# Check Backend API (local)
Write-Host "  Checking Backend API (local, port 18888)..." -ForegroundColor Gray
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$backendPort = Get-NetTCPConnection -LocalPort $appPort -ErrorAction SilentlyContinue
if ($backendPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:$appPort/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "    [OK] Backend API - Running on port $appPort" -ForegroundColor Green
        }
    } catch {
        Write-Host "    [WARN] Backend API - Port $appPort open but health check failed" -ForegroundColor Yellow
    }
} else {
    Write-Host "    [WARN] Backend API - Not running on port $appPort" -ForegroundColor Yellow
    Write-Host "    [INFO] Start it with: .\scripts\start_backend_dev.ps1" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($foundDuplicates) {
    Write-Host "  [ACTION REQUIRED] Duplicate containers found" -ForegroundColor Yellow
    Write-Host "  [INFO] You can remove stopped/old containers:" -ForegroundColor Gray
    Write-Host "    docker ps -a --filter 'name=jachin' --filter 'status=exited' -q | ForEach-Object { docker rm $_ }" -ForegroundColor DarkGray
} else {
    Write-Host "  [OK] No duplicate containers" -ForegroundColor Green
}

if ($missingContainers.Count -gt 0) {
    Write-Host ""
    Write-Host "  [ACTION REQUIRED] Missing or stopped containers:" -ForegroundColor Yellow
    foreach ($missing in $missingContainers) {
        Write-Host "    - $($missing.Service) ($($missing.Name))" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "  [INFO] Start missing services:" -ForegroundColor Gray
    Write-Host "    docker-compose -f docker-compose.dev.yml -p jachin-dev up -d" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  List all containers: docker ps -a --filter 'name=jachin'" -ForegroundColor Gray
Write-Host "  Remove stopped containers: docker container prune --filter 'name=jachin'" -ForegroundColor Gray
Write-Host "  View container logs: docker logs <container-name>" -ForegroundColor Gray
Write-Host ""
