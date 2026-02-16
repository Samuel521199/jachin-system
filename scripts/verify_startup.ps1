# Verify Startup and Show Next Steps
# 验证启动状态并显示下一步操作

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Verifying Startup Status" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

$allOk = $true

# 1. Check Backend Service
Write-Host "[1/4] Checking Backend Service..." -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        Write-Host "  [OK] Backend API is running" -ForegroundColor Green
        Write-Host "    URL: http://localhost:8000" -ForegroundColor Gray
    }
} catch {
    Write-Host "  [WARN] Backend API not responding yet" -ForegroundColor Yellow
    Write-Host "    This is normal if it just started. Wait a few seconds." -ForegroundColor Gray
    $allOk = $false
}

# 2. Check Dapr Sidecar
Write-Host "[2/4] Checking Dapr Sidecar..." -ForegroundColor Cyan
try {
    $daprHealth = Invoke-WebRequest -Uri "http://localhost:3500/v1.0/healthz" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    if ($daprHealth.StatusCode -eq 200) {
        Write-Host "  [OK] Dapr sidecar is running" -ForegroundColor Green
        Write-Host "    HTTP API: http://localhost:3500" -ForegroundColor Gray
    }
} catch {
    Write-Host "  [WARN] Dapr sidecar not responding" -ForegroundColor Yellow
    $allOk = $false
}

# 3. Check Middleware Services
Write-Host "[3/4] Checking Middleware Services..." -ForegroundColor Cyan

# Redis
$redisPort = Get-NetTCPConnection -LocalPort 6379 -ErrorAction SilentlyContinue
if ($redisPort) {
    Write-Host "  [OK] Redis - Running (port 6379)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Redis - Not running" -ForegroundColor Yellow
    $allOk = $false
}

# MQTT
$mqttPort = Get-NetTCPConnection -LocalPort 1883 -ErrorAction SilentlyContinue
if ($mqttPort) {
    Write-Host "  [OK] MQTT - Running (port 1883)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] MQTT - Not running" -ForegroundColor Yellow
    $allOk = $false
}

# Dapr Placement
$placementPort = Get-NetTCPConnection -LocalPort 6050 -ErrorAction SilentlyContinue
if ($placementPort) {
    Write-Host "  [OK] Dapr Placement - Running (port 6050)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Dapr Placement - Not running" -ForegroundColor Yellow
}

# 4. Check Local Databases
Write-Host "[4/4] Checking Local Databases..." -ForegroundColor Cyan

# PostgreSQL
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    try {
        $env:PGPASSWORD = "secure_password"
        $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1 | Out-Null
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
    Write-Host "  [WARN] PostgreSQL - Not running" -ForegroundColor Yellow
}

# Qdrant
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
    Write-Host "  [WARN] Qdrant - Not running" -ForegroundColor Yellow
}

# Backend API (从环境变量读取端口)
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$backendPort = Get-NetTCPConnection -LocalPort $appPort -ErrorAction SilentlyContinue
if ($backendPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:$appPort/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Host "  [OK] Backend API - Running (port $appPort)" -ForegroundColor Green
        }
    } catch {
        Write-Host "  [WARN] Backend API - Port $appPort open but health check failed" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [WARN] Backend API - Not running on port $appPort" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green

if ($allOk) {
    Write-Host "  [SUCCESS] All services are running!" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Some services may still be starting..." -ForegroundColor Yellow
    Write-Host "  [INFO] Wait a few seconds and check again" -ForegroundColor Gray
}

Write-Host "========================================" -ForegroundColor Green
Write-Host ""
