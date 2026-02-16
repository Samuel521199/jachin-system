# Functionality Test Script
# 功能测试脚本 - 测试系统核心功能

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Jachin-System v3.2 功能测试" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 激活 Conda 环境
Write-Host "[1/5] 激活 Conda 环境..." -ForegroundColor Yellow
try {
    conda activate jachin-dev 2>$null
    Write-Host "  [OK] 环境已激活" -ForegroundColor Green
} catch {
    Write-Host "  [WARNING] 无法激活环境，继续测试..." -ForegroundColor Yellow
}

# 检查后端服务是否运行
Write-Host "[2/5] 检查后端服务..." -ForegroundColor Yellow
$backendUrl = "http://localhost:18888"
$healthCheck = $null

try {
    $healthCheck = Invoke-RestMethod -Uri "$backendUrl/health" -Method Get -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  [OK] 后端服务运行正常" -ForegroundColor Green
    Write-Host "  [INFO] 服务状态: $($healthCheck.status)" -ForegroundColor Blue
} catch {
    Write-Host "  [ERROR] 后端服务未运行或无法访问" -ForegroundColor Red
    Write-Host "  [INFO] 请先启动后端服务: .\scripts\start.ps1" -ForegroundColor Yellow
    exit 1
}

# 测试技能列表 API
Write-Host "[3/5] 测试技能列表 API..." -ForegroundColor Yellow
try {
    $skillsResponse = Invoke-RestMethod -Uri "$backendUrl/api/v3/skills/list" -Method Get -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  [OK] 技能列表 API 正常" -ForegroundColor Green
    if ($skillsResponse.skills) {
        Write-Host "  [INFO] 找到 $($skillsResponse.skills.Count) 个技能" -ForegroundColor Blue
        foreach ($skill in $skillsResponse.skills[0..2]) {
            Write-Host "    - $($skill.id)" -ForegroundColor Gray
        }
    }
} catch {
    Write-Host "  [WARNING] 技能列表 API 测试失败: $_" -ForegroundColor Yellow
}

# 测试自然语言查询
Write-Host "[4/5] 测试自然语言查询..." -ForegroundColor Yellow
try {
    $testQuery = @{
        user_query = "查看系统状态"
        trace_id = "test-$(Get-Date -Format 'yyyyMMddHHmmss')"
    } | ConvertTo-Json

    $invokeResponse = Invoke-RestMethod -Uri "$backendUrl/api/v3/orchestrator/invoke" `
        -Method Post `
        -ContentType "application/json" `
        -Body $testQuery `
        -TimeoutSec 30 `
        -ErrorAction Stop

    if ($invokeResponse.status_code -eq 200) {
        Write-Host "  [OK] 自然语言查询成功" -ForegroundColor Green
        if ($invokeResponse.ui_render_schema) {
            Write-Host "  [OK] SDUI Schema 已返回" -ForegroundColor Green
            try {
                $uiSchema = $invokeResponse.ui_render_schema | ConvertFrom-Json
                Write-Host "  [INFO] UI 类型: $($uiSchema.type)" -ForegroundColor Blue
            } catch {
                Write-Host "  [INFO] UI Schema 是字符串格式" -ForegroundColor Blue
            }
        }
    } else {
        Write-Host "  [WARNING] 查询返回状态码: $($invokeResponse.status_code)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [WARNING] 自然语言查询测试失败: $_" -ForegroundColor Yellow
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        Write-Host "  [ERROR] 响应: $responseBody" -ForegroundColor Red
    }
}

# 测试性能监控 API
Write-Host "[5/5] 测试性能监控 API..." -ForegroundColor Yellow
try {
    $monitoringResponse = Invoke-RestMethod -Uri "$backendUrl/api/v3/monitoring/stats" -Method Get -TimeoutSec 5 -ErrorAction Stop
    Write-Host "  [OK] 性能监控 API 正常" -ForegroundColor Green
    if ($monitoringResponse.metrics) {
        $metricCount = ($monitoringResponse.metrics | Measure-Object).Count
        Write-Host "  [INFO] 收集了 $metricCount 个性能指标" -ForegroundColor Blue
    }
} catch {
    Write-Host "  [WARNING] 性能监控 API 测试失败: $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  功能测试完成" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "下一步:" -ForegroundColor Yellow
Write-Host "  1. 启动桌面客户端测试 SDUI 渲染" -ForegroundColor White
Write-Host "  2. 查看 API 文档: $backendUrl/docs" -ForegroundColor White
Write-Host "  3. 查看性能监控: $backendUrl/api/v3/monitoring/stats" -ForegroundColor White
Write-Host ""
