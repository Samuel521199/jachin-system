# =============================================================================
# Layer3 (用户) - 一键安装 (Windows)
# clients/desktop - Jachin Terminal (Tauri + React)
# =============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 前置依赖检查
& (Join-Path $ScriptDir "check-prerequisites.ps1") layer3 -NoPrompt
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Layer3 (用户) - 一键安装" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$DesktopDir = Join-Path $ProjectRoot "clients\desktop"
if (-not (Test-Path $DesktopDir)) {
    Write-Host "[ERROR] 未找到 clients\desktop" -ForegroundColor Red
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 未找到 Node.js" -ForegroundColor Red
    exit 1
}

Push-Location $DesktopDir
npm install --silent
Pop-Location

Write-Host "[OK] Layer3 (Desktop) 已安装" -ForegroundColor Green
Write-Host "  启动: .\scripts\start-layer3.ps1"
Write-Host "  完整构建需 Rust + Tauri CLI，见 clients\desktop\scripts\setup.ps1"
Write-Host ""
