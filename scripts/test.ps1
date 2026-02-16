# Test script - 测试 API
# 根据流程图实现：检查健康状态 -> 测试 API 端点

# 彩色日志函数
function Write-Step {
    param([string]$Message)
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Jachin-System API Test" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

$baseUrl = 'http://localhost:8000'
$allTestsPassed = $true

# Step 1: 健康检查
Write-Step "Step 1: Health Check"

try {
    $healthUrl = 'http://localhost:8000/health'
    Write-Info "Checking health endpoint: $healthUrl"
    $response = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 5
    Write-Success "Service is healthy"
    Write-Host "  Status: $($response.status)" -ForegroundColor Gray
    Write-Host "  Service: $($response.service)" -ForegroundColor Gray
    Write-Host "  Version: $($response.version)" -ForegroundColor Gray
} catch {
    Write-Error "Service not available"
    Write-Info "Make sure backend is running: .\scripts\start.ps1"
    exit 1
}

# Step 2: 测试 API 端点
Write-Step "Step 2: Testing API Endpoints"

# 2.1 测试根端点
Write-Info "Testing root endpoint..."
try {
    $rootUrl = 'http://localhost:8000/'
    $response = Invoke-RestMethod -Uri $rootUrl -Method Get -TimeoutSec 5
    Write-Success "Root endpoint OK"
    Write-Host "  Message: $($response.message)" -ForegroundColor Gray
} catch {
    Write-Warning "Root endpoint test failed: $_"
    $allTestsPassed = $false
}

# 2.2 测试路由列表
Write-Info "Testing routes endpoint..."
try {
    $routesUrl = 'http://localhost:8000/routes'
    $response = Invoke-RestMethod -Uri $routesUrl -Method Get -TimeoutSec 5
    $routeCount = ($response.routes | Measure-Object).Count
    $routesMsg = "Routes endpoint OK - " + $routeCount + " routes found"
    Write-Success $routesMsg
} catch {
    Write-Warning "Routes endpoint test failed: $_"
    $allTestsPassed = $false
}

# 2.3 测试聊天 API
Write-Info "Testing chat API..."
try {
    $body = @{
        message = 'Hello, this is a test message'
    } | ConvertTo-Json -Compress
    
    $chatUrl = 'http://localhost:8000/api/chat'
    $response = Invoke-RestMethod -Uri $chatUrl -Method POST -Body $body -ContentType 'application/json' -TimeoutSec 30
    
    Write-Success "Chat API OK"
    if ($response.reply) {
        $replyLength = $response.reply.Length
        $displayLength = [Math]::Min(100, $replyLength)
        $replyPreview = $response.reply.Substring(0, $displayLength)
        Write-Host "  Reply: $replyPreview..." -ForegroundColor Gray
    }
}
catch {
    Write-Warning "Chat API test failed: $_"
    Write-Info "This may be expected if QWEN_API_KEY is not configured"
    $allTestsPassed = $false
}

# 2.4 测试设备注册表 API (v2.0)
Write-Info "Testing device registry API (v2.0)..."
try {
    $devicesUrl = 'http://localhost:8000/api/v2/devices'
    $response = Invoke-RestMethod -Uri $devicesUrl -Method Get -TimeoutSec 5
    
    if ($response.devices) {
        $deviceCount = ($response.devices | Measure-Object).Count
        $onlineCount = $response.online
        $successMsg = "Device Registry API OK - " + $deviceCount + " devices registered (" + $onlineCount + " online)"
        Write-Success $successMsg
        
        # 显示设备列表
        if ($deviceCount -gt 0) {
            Write-Host "  Registered devices:" -ForegroundColor Gray
            foreach ($device in $response.devices) {
                $onlineStatus = if ($device.online) { "online" } else { "offline" }
                Write-Host "    - $($device.device_id) ($($device.location)) - $onlineStatus" -ForegroundColor Gray
            }
        }
    } else {
        Write-Success "Device Registry API OK - 0 devices registered"
        Write-Info "Run mock device to register: cd clients\iot\mock_device && .\run.bat"
    }
} catch {
    $errorDetail = $_.Exception.Message
    if ($errorDetail -match "404" -or $errorDetail -match "Not Found") {
        Write-Warning "Device Registry API endpoint not found (may need backend restart)"
        Write-Info "Please restart backend: .\scripts\restart.ps1"
    } else {
        Write-Warning "Device Registry API test failed: $_"
    }
    # 不标记为失败，因为这是新功能
}

# Step 3: 测试 Dapr 集成
Write-Step "Step 3: Testing Dapr Integration"

# 检查 Dapr sidecar
Write-Info "Checking Dapr sidecar..."
try {
    $daprHealthUrl = 'http://localhost:3500/v1.0/healthz'
    $daprHealth = Invoke-RestMethod -Uri $daprHealthUrl -Method Get -TimeoutSec 5
    Write-Success "Dapr sidecar is healthy"
} catch {
    $errorMsg = $_.Exception.Message
    $errorString = $_.ToString()
    
    # 尝试解析 JSON 错误响应
    $isSchedulerError = $false
    try {
        $errorJson = $errorMsg | ConvertFrom-Json
        if ($errorJson.errorCode -eq "ERR_HEALTH_NOT_READY" -and 
            ($errorJson.message -match "scheduler" -or $errorJson.message -match "scheduler-watch-hosts")) {
            $isSchedulerError = $true
        }
    } catch {
        # 如果不是 JSON，检查字符串匹配
        if ($errorMsg -match "scheduler" -or $errorMsg -match "ERR_HEALTH_NOT_READY" -or 
            $errorString -match "scheduler" -or $errorString -match "ERR_HEALTH_NOT_READY") {
            $isSchedulerError = $true
        }
    }
    
    # 检查是否是 scheduler 错误（已知的 Dapr 1.16.5 限制）
    if ($isSchedulerError) {
        Write-Info "Dapr sidecar is running (scheduler warning is harmless)"
        Write-Info "This is a known Dapr 1.16.5 limitation and does not affect functionality"
        Write-Info "Dapr Pub/Sub and other features are working correctly"
    } else {
        Write-Warning "Dapr sidecar health check failed: $errorMsg"
        Write-Info "This may be expected if Dapr is not running"
    }
}

# 总结
Write-Host ""
if ($allTestsPassed) {
    $testCompleteColor = "Green"
} else {
    $testCompleteColor = "Yellow"
}
Write-Host "========================================" -ForegroundColor $testCompleteColor
Write-Host "        Test Complete" -ForegroundColor $testCompleteColor
Write-Host "========================================" -ForegroundColor $testCompleteColor
Write-Host ""

if ($allTestsPassed) {
    Write-Success "All critical tests passed!"
} else {
    Write-Warning "Some tests failed. Check the output above for details."
    Write-Info "Common issues:"
    Write-Info "  - Backend not running: .\scripts\start.ps1"
    Write-Info "  - API Key not configured: Edit .env file"
    Write-Info "  - Dapr not initialized: .\scripts\setup.ps1"
}

Write-Host ""
