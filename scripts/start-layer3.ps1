# =============================================================================
# Layer3 (Desktop) - One-click start (Windows)
# clients/desktop - Jachin Terminal
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$DesktopDir = Join-Path $ProjectRoot "clients\desktop"
if (-not (Test-Path $DesktopDir)) {
    Write-Host "[ERROR] clients\desktop not found. Run: .\scripts\install-layer3.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
    Write-Host "[INFO] Installing dependencies..." -ForegroundColor Yellow
    Push-Location $DesktopDir
    npm install
    Pop-Location
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Layer3 (Desktop)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Tauri requires Rust. Falls back to Vite dev if not found."
Write-Host "  Press Ctrl+C to stop"
Write-Host ""

Push-Location $DesktopDir
if (Get-Command tauri -ErrorAction SilentlyContinue) {
    npm run tauri:dev
} else {
    Write-Host "[INFO] Tauri not found, using Vite dev mode" -ForegroundColor Yellow
    npm run dev
}
Pop-Location
