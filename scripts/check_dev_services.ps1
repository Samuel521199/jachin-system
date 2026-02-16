# Check Development Services Status
# 检查开发服务状态

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Development Services Status" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

# Check Docker services
Write-Host "[1/3] Docker Services:" -ForegroundColor Cyan

try {
    $servicesOutput = docker-compose -f $composeFile -p $projectName ps 2>&1
    if ($LASTEXITCODE -eq 0 -and $servicesOutput) {
        # Parse the output
        $lines = $servicesOutput | Where-Object { $_ -match "\S" }
        $headerFound = $false
        
        foreach ($line in $lines) {
            if ($line -match "NAME|Container" -and -not $headerFound) {
                $headerFound = $true
                continue
            }
            
            if ($headerFound -and $line -match "jachin-") {
                # Parse service line
                $parts = $line -split "\s+"
                if ($parts.Count -ge 3) {
                    $name = $parts[0]
                    $state = $parts[1]
                    $status = $parts[2..($parts.Count-1)] -join " "
                    
                    $statusColor = switch ($state) {
                        "Up" { "Green" }
                        "Exit" { "Red" }
                        default { "Yellow" }
                    }
                    
                    Write-Host "  [$state] $name" -ForegroundColor $statusColor
                    if ($status -and $status -ne "-") {
                        Write-Host "    $status" -ForegroundColor Gray
                    }
                }
            }
        }
        
        # Also check using docker ps directly
        $containers = docker ps -a --filter "name=jachin-" --format "{{.Names}}\t{{.Status}}" 2>&1
        if ($containers -and -not $headerFound) {
            foreach ($container in $containers) {
                if ($container -match "jachin-") {
                    $parts = $container -split "`t"
                    if ($parts.Count -eq 2) {
                        $name = $parts[0]
                        $status = $parts[1]
                        $state = if ($status -match "Up") { "Up" } elseif ($status -match "Exited") { "Exited" } else { "Unknown" }
                        $statusColor = if ($state -eq "Up") { "Green" } elseif ($state -eq "Exited") { "Red" } else { "Yellow" }
                        Write-Host "  [$state] $name" -ForegroundColor $statusColor
                        Write-Host "    $status" -ForegroundColor Gray
                    }
                }
            }
        }
        
        if (-not $headerFound -and -not $containers) {
            Write-Host "  [WARN] No services found" -ForegroundColor Yellow
            Write-Host "  [INFO] Services may not be running. Try: docker-compose -f $composeFile -p $projectName up -d" -ForegroundColor Gray
        }
    } else {
        Write-Host "  [WARN] Cannot get service status" -ForegroundColor Yellow
        Write-Host "  [INFO] Checking containers directly..." -ForegroundColor Gray
        
        # Fallback: check containers directly
        $containers = docker ps -a --filter "name=jachin-" --format "{{.Names}}\t{{.Status}}" 2>&1
        if ($containers) {
            foreach ($container in $containers) {
                if ($container -match "jachin-") {
                    $parts = $container -split "`t"
                    if ($parts.Count -eq 2) {
                        $name = $parts[0]
                        $status = $parts[1]
                        $state = if ($status -match "Up") { "Up" } elseif ($status -match "Exited") { "Exited" } else { "Unknown" }
                        $statusColor = if ($state -eq "Up") { "Green" } elseif ($state -eq "Exited") { "Red" } else { "Yellow" }
                        Write-Host "  [$state] $name" -ForegroundColor $statusColor
                        Write-Host "    $status" -ForegroundColor Gray
                    }
                }
            }
        } else {
            Write-Host "  [ERROR] No containers found" -ForegroundColor Red
        }
    }
} catch {
    Write-Host "  [ERROR] Error checking services: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "[2/3] Local Databases:" -ForegroundColor Cyan

# Check PostgreSQL
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    try {
        $env:PGPASSWORD = "secure_password"
        $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] PostgreSQL - Connected" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] PostgreSQL - Port open but connection failed" -ForegroundColor Yellow
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } catch {
        Write-Host "  [WARN] PostgreSQL - Cannot test connection" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [ERROR] PostgreSQL - Not running on port 5432" -ForegroundColor Red
}

# Check Qdrant
$qdrantPort = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($qdrantPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "  [OK] Qdrant - Running" -ForegroundColor Green
        }
    } catch {
        Write-Host "  [WARN] Qdrant - Port open but health check failed" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [ERROR] Qdrant - Not running on port 6333" -ForegroundColor Red
}

Write-Host ""
Write-Host "[3/3] Port Status:" -ForegroundColor Cyan

# 从环境变量读取应用端口（默认 18888）
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$daprHttpPort = if ($env:DAPR_HTTP_PORT) { $env:DAPR_HTTP_PORT } else { "13500" }
$daprGrpcPort = if ($env:DAPR_GRPC_PORT) { $env:DAPR_GRPC_PORT } else { "15001" }

$ports = @(
    @{Port=6379; Service="Redis"},
    @{Port=1883; Service="MQTT"},
    @{Port=9001; Service="MQTT WebSocket"},
    @{Port=6050; Service="Dapr Placement"},
    @{Port=6060; Service="Dapr Scheduler"},
    @{Port=[int]$appPort; Service="Backend API"},
    @{Port=[int]$daprHttpPort; Service="Dapr HTTP"},
    @{Port=[int]$daprGrpcPort; Service="Dapr gRPC"}
)

foreach ($portInfo in $ports) {
    $port = $portInfo.Port
    $service = $portInfo.Service
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "  [OK] Port $port ($service) - In use" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Port $port ($service) - Not in use" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Status Check Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Initialize database: .\installer\init_database.ps1" -ForegroundColor Gray
Write-Host "  2. Check database config: .\scripts\check_local_databases.ps1" -ForegroundColor Gray
Write-Host "  3. Start backend: .\scripts\start.ps1" -ForegroundColor Gray
Write-Host ""
