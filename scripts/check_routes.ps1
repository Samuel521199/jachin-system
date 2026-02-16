# Check Routes - 检查后端路由注册情况

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Checking Backend Routes" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$baseUrl = 'http://localhost:8000'

# 检查健康状态
Write-Host "[1] Checking backend health..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 5
    Write-Host "   [OK] Backend is running" -ForegroundColor Green
} catch {
    Write-Host "   [ERROR] Backend is not running" -ForegroundColor Red
    Write-Host "   Please start backend: .\scripts\start.ps1" -ForegroundColor Yellow
    exit 1
}

# 检查路由列表
Write-Host ""
Write-Host "[2] Checking registered routes..." -ForegroundColor Yellow
try {
    $routes = Invoke-RestMethod -Uri "$baseUrl/routes" -Method Get -TimeoutSec 5
    $routeCount = ($routes.routes | Measure-Object).Count
    Write-Host "   [OK] Found $routeCount routes" -ForegroundColor Green
    
    # 检查设备相关路由
    $deviceRoutes = $routes.routes | Where-Object { $_.path -match '/api/v2/devices' }
    if ($deviceRoutes) {
        Write-Host ""
        Write-Host "   Device Registry Routes:" -ForegroundColor Cyan
        foreach ($route in $deviceRoutes) {
            $methods = $route.methods -join ', '
            Write-Host "     $methods $($route.path)" -ForegroundColor Gray
        }
    } else {
        Write-Host "   [WARNING] No device registry routes found" -ForegroundColor Yellow
        Write-Host "   Backend may need restart to load new routes" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   [ERROR] Failed to get routes: $_" -ForegroundColor Red
}

# 测试设备注册表 API
Write-Host ""
Write-Host "[3] Testing device registry API..." -ForegroundColor Yellow
try {
    $devices = Invoke-RestMethod -Uri "$baseUrl/api/v2/devices" -Method Get -TimeoutSec 5
    Write-Host "   [OK] Device Registry API is working" -ForegroundColor Green
    Write-Host "   Total devices: $($devices.total)" -ForegroundColor Gray
    Write-Host "   Online devices: $($devices.online)" -ForegroundColor Gray
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 404) {
        Write-Host "   [ERROR] 404 - Device Registry API not found" -ForegroundColor Red
        Write-Host "   Backend needs restart to load device_router" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "   Solution:" -ForegroundColor Cyan
        Write-Host "   1. Stop backend (Ctrl+C)" -ForegroundColor White
        Write-Host "   2. Run: .\scripts\start.ps1" -ForegroundColor White
        Write-Host "   3. Check logs for: 'Device API router registered'" -ForegroundColor White
    } else {
        Write-Host "   [ERROR] Device Registry API failed: $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Check Complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
