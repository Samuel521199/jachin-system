# Check Missing Services
# 检查缺失的服务

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  服务状态检查报告" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectName = "jachin-dev"
$composeFile = "docker-compose.dev.yml"

# 预期的 Docker 容器服务
$expectedDockerServices = @{
    "jachin-redis-dev" = @{Service="Redis"; Port=6379; Required=$true}
    "jachin-mqtt-dev" = @{Service="MQTT Broker"; Port=1883; Required=$true}
    "jachin-dapr-placement-dev" = @{Service="Dapr Placement"; Port=6050; Required=$true}
    "jachin-dapr-scheduler-dev" = @{Service="Dapr Scheduler"; Port=6060; Required=$true}
}

# 预期的本地服务
$expectedLocalServices = @{
    "PostgreSQL" = @{Port=5432; Required=$true; Type="Database"}
    "Qdrant" = @{Port=6333; Required=$true; Type="Database"}
    "Backend API" = @{Port=18888; Required=$true; Type="Application"}
}

Write-Host "[Docker 容器服务]" -ForegroundColor Cyan
Write-Host ""

$missingDockerServices = @()
$runningDockerServices = @()

try {
    $containers = docker ps -a --filter "name=jachin-" --format "{{.Names}}|{{.Status}}" 2>&1
    $containerMap = @{}
    
    if ($containers) {
        foreach ($line in $containers) {
            if ($line -match "jachin-") {
                $parts = $line -split '\|'
                if ($parts.Count -eq 2) {
                    $name = $parts[0].Trim()
                    $status = $parts[1].Trim()
                    $containerMap[$name] = $status
                }
            }
        }
    }
    
    foreach ($serviceName in $expectedDockerServices.Keys) {
        $service = $expectedDockerServices[$serviceName]
        $isRunning = $false
        
        if ($containerMap.ContainsKey($serviceName)) {
            $status = $containerMap[$serviceName]
            if ($status -match "Up|Running") {
                $isRunning = $true
                Write-Host "  [✓] $($service.Service) ($serviceName)" -ForegroundColor Green
                Write-Host "      状态: $status" -ForegroundColor Gray
                $runningDockerServices += $serviceName
            } else {
                Write-Host "  [✗] $($service.Service) ($serviceName)" -ForegroundColor Red
                Write-Host "      状态: $status (已停止)" -ForegroundColor Yellow
                $missingDockerServices += $serviceName
            }
        } else {
            Write-Host "  [✗] $($service.Service) ($serviceName)" -ForegroundColor Red
            Write-Host "      状态: 未找到容器" -ForegroundColor Yellow
            $missingDockerServices += $serviceName
        }
    }
} catch {
    Write-Host "  [错误] 无法检查 Docker 容器: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "[本地服务]" -ForegroundColor Cyan
Write-Host ""

$missingLocalServices = @()
$runningLocalServices = @()

# 检查 PostgreSQL
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    try {
        $env:PGPASSWORD = "secure_password"
        $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [✓] PostgreSQL (端口 5432)" -ForegroundColor Green
            Write-Host "      状态: 运行中且可连接" -ForegroundColor Gray
            $runningLocalServices += "PostgreSQL"
        } else {
            Write-Host "  [✗] PostgreSQL (端口 5432)" -ForegroundColor Yellow
            Write-Host "      状态: 端口开放但连接失败" -ForegroundColor Yellow
            $missingLocalServices += "PostgreSQL"
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } catch {
        Write-Host "  [✗] PostgreSQL (端口 5432)" -ForegroundColor Yellow
        Write-Host "      状态: 无法测试连接" -ForegroundColor Yellow
        $missingLocalServices += "PostgreSQL"
    }
} else {
    Write-Host "  [✗] PostgreSQL (端口 5432)" -ForegroundColor Red
    Write-Host "      状态: 未运行" -ForegroundColor Red
    $missingLocalServices += "PostgreSQL"
}

# 检查 Qdrant
$qdrantPort = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($qdrantPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "  [✓] Qdrant (端口 6333)" -ForegroundColor Green
            Write-Host "      状态: 运行中且健康" -ForegroundColor Gray
            $runningLocalServices += "Qdrant"
        }
    } catch {
        try {
            $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($health.StatusCode -eq 200) {
                Write-Host "  [✓] Qdrant (端口 6333)" -ForegroundColor Green
                Write-Host "      状态: 运行中且健康" -ForegroundColor Gray
                $runningLocalServices += "Qdrant"
            }
        } catch {
            Write-Host "  [✗] Qdrant (端口 6333)" -ForegroundColor Yellow
            Write-Host "      状态: 端口开放但健康检查失败" -ForegroundColor Yellow
            $missingLocalServices += "Qdrant"
        }
    }
} else {
    Write-Host "  [✗] Qdrant (端口 6333)" -ForegroundColor Red
    Write-Host "      状态: 未运行" -ForegroundColor Red
    $missingLocalServices += "Qdrant"
}

# 检查 Backend API
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$backendPort = Get-NetTCPConnection -LocalPort $appPort -ErrorAction SilentlyContinue
if ($backendPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:$appPort/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "  [✓] Backend API (端口 $appPort)" -ForegroundColor Green
            Write-Host "      状态: 运行中" -ForegroundColor Gray
            $runningLocalServices += "Backend API"
        }
    } catch {
        Write-Host "  [✗] Backend API (端口 $appPort)" -ForegroundColor Yellow
        Write-Host "      状态: 端口开放但健康检查失败" -ForegroundColor Yellow
        $missingLocalServices += "Backend API"
    }
} else {
    Write-Host "  [✗] Backend API (端口 $appPort)" -ForegroundColor Red
    Write-Host "      状态: 未运行" -ForegroundColor Red
    $missingLocalServices += "Backend API"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  缺失服务总结" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($missingDockerServices.Count -eq 0 -and $missingLocalServices.Count -eq 0) {
    Write-Host "  [✓] 所有服务都在运行！" -ForegroundColor Green
    Write-Host ""
} else {
    if ($missingDockerServices.Count -gt 0) {
        Write-Host "[缺失的 Docker 容器:]" -ForegroundColor Yellow
        foreach ($service in $missingDockerServices) {
            $serviceInfo = $expectedDockerServices[$service]
            Write-Host "  - $($serviceInfo.Service) ($service)" -ForegroundColor Red
            Write-Host "    端口: $($serviceInfo.Port)" -ForegroundColor Gray
        }
        Write-Host ""
        Write-Host "  启动命令:" -ForegroundColor Cyan
        Write-Host "    docker-compose -f $composeFile -p $projectName up -d" -ForegroundColor Gray
        Write-Host ""
    }
    
    if ($missingLocalServices.Count -gt 0) {
        Write-Host "[缺失的本地服务:]" -ForegroundColor Yellow
        foreach ($service in $missingLocalServices) {
            $serviceInfo = $expectedLocalServices[$service]
            Write-Host "  - $service (端口 $($serviceInfo.Port))" -ForegroundColor Red
            Write-Host "    类型: $($serviceInfo.Type)" -ForegroundColor Gray
            
            if ($service -eq "PostgreSQL") {
                Write-Host "    启动: 请确保 PostgreSQL 服务已启动" -ForegroundColor Gray
                Write-Host "    检查: .\scripts\check_local_databases.ps1" -ForegroundColor DarkGray
            } elseif ($service -eq "Qdrant") {
                Write-Host "    启动: 请确保 Qdrant 服务已启动" -ForegroundColor Gray
                Write-Host "    检查: .\scripts\check_local_databases.ps1" -ForegroundColor DarkGray
            } elseif ($service -eq "Backend API") {
                Write-Host "    启动: .\scripts\start_backend_dev.ps1" -ForegroundColor Gray
                Write-Host "    或: .\scripts\start_dev.ps1 (启动所有服务)" -ForegroundColor DarkGray
            }
        }
        Write-Host ""
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  快速启动命令" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  启动所有 Docker 服务:" -ForegroundColor Gray
Write-Host "    docker-compose -f $composeFile -p $projectName up -d" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  启动后端 API (如果 Docker 服务已运行):" -ForegroundColor Gray
Write-Host "    .\scripts\start_backend_dev.ps1" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  一键启动所有服务 (推荐):" -ForegroundColor Gray
Write-Host "    .\scripts\start_dev.ps1" -ForegroundColor DarkGray
Write-Host ""
