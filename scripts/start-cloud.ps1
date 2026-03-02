# =============================================================================
# Cloud (平台商) - 一键启动 (Windows)
# cloud/nexus - Nexus Console @ http://localhost:3000
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$NexusDir = Join-Path $ProjectRoot "cloud\nexus"
if (-not (Test-Path $NexusDir)) {
    Write-Host "[ERROR] 未找到 cloud\nexus，请先执行: .\scripts\install-cloud.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $NexusDir "node_modules"))) {
    Write-Host "[INFO] 依赖未安装，正在安装..." -ForegroundColor Yellow
    Push-Location $NexusDir
    npm install
    Pop-Location
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host '  Cloud (Nexus Console) starting' -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  http://localhost:3000"
Write-Host "  Press Ctrl+C to stop"
Write-Host ""

Push-Location $NexusDir
npm run dev
Pop-Location
