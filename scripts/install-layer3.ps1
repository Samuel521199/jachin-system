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

# L3 Sidecar：首次安装时创建占位符或构建
$BinDir = Join-Path $DesktopDir "src-tauri\bin"
$L3Exe = Get-ChildItem -Path $BinDir -Filter "l3_node-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $L3Exe) {
    Write-Host "[Layer3] 创建 L3 Sidecar..." -ForegroundColor Gray
    & python (Join-Path $ScriptDir "build_l3_sidecar.py") 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & python (Join-Path $ScriptDir "create_l3_stub.py") 2>&1 | Out-Null
        Write-Host "[INFO] 已创建占位符。完整 L3 需: python scripts\build_l3_sidecar.py" -ForegroundColor Gray
    }
}

Write-Host "[OK] Layer3 (Desktop) 已安装" -ForegroundColor Green
Write-Host "  启动: .\scripts\start-layer3.ps1"
Write-Host "  完整构建需 Rust + Tauri CLI，见 clients\desktop\scripts\setup.ps1"
Write-Host ""
