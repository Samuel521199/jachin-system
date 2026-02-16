# Restart script - 重启所有服务

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Restarting services..." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Stop
Write-Host "[STEP 1/2] Stopping services..." -ForegroundColor Yellow
& "$ScriptDir\stop.ps1"
$stopExitCode = $LASTEXITCODE
if ($stopExitCode -ne 0) {
    Write-Host "[WARN] Stop script returned exit code $stopExitCode (may be harmless)" -ForegroundColor Yellow
    # 继续执行，因为停止服务时的一些错误是可以接受的（比如服务不存在）
}

Write-Host ""
Write-Host "[STEP 2/2] Starting services..." -ForegroundColor Yellow
Write-Host ""

# Start (这会启动长时间运行的服务，窗口会保持打开)
& "$ScriptDir\start.ps1"
