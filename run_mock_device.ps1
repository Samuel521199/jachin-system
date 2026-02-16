# Mock IoT Device - 从项目根目录运行的包装脚本

$scriptPath = Join-Path $PSScriptRoot "clients\iot\mock_device\run_with_heartbeat.ps1"

if (-not (Test-Path $scriptPath)) {
    Write-Host "[ERROR] Script not found: $scriptPath" -ForegroundColor Red
    Write-Host "Please ensure you're in the project root directory." -ForegroundColor Yellow
    exit 1
}

Write-Host "Running Mock IoT Device from: $scriptPath" -ForegroundColor Gray
Write-Host ""

# 运行实际的脚本
& $scriptPath
