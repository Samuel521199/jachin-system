# =============================================================================
# Layer3 (用户) - 一键启动 (Windows)
# clients/desktop - Jachin Terminal
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$DesktopDir = Join-Path $ProjectRoot "clients\desktop"
if (-not (Test-Path $DesktopDir)) {
    Write-Host "[ERROR] 未找到 clients\desktop，请先执行: .\scripts\install-layer3.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
    Write-Host "[INFO] 依赖未安装，正在安装..." -ForegroundColor Yellow
    Push-Location $DesktopDir
    npm install
    Pop-Location
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Layer3 (Desktop) 启动" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Tauri 需 Rust 环境，若无则尝试 Vite 开发模式"
Write-Host "  Press Ctrl+C to stop"
Write-Host ""

Push-Location $DesktopDir
try {
    if (Get-Command tauri -ErrorAction SilentlyContinue) {
        npm run tauri:dev
    } else {
        Write-Host "[INFO] Tauri 未安装，使用 Vite 开发模式" -ForegroundColor Yellow
        npm run dev
    }
} finally {
    Pop-Location
}
