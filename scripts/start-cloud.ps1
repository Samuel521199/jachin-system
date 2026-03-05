# =============================================================================
# Cloud (Layer 1) - One-click start (Windows)
# cloud/nexus - Nexus Console @ http://localhost:3000
# =============================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$NexusDir = Join-Path $ProjectRoot "cloud\nexus"
if (-not (Test-Path $NexusDir)) {
    Write-Host "(ERROR) cloud\nexus not found. Run: .\scripts\install-cloud.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "(ERROR) Node.js not found. Run: .\scripts\install-cloud.ps1" -ForegroundColor Red
    exit 1
}

$NextInstalled = Test-Path (Join-Path $NexusDir "node_modules\next")
if (-not $NextInstalled) {
    Write-Host "(INFO) Installing deps (first run)..." -ForegroundColor Yellow
    Push-Location $NexusDir
    npm install --silent
    Pop-Location
}

# .env.local optional: copy from .env.example if missing (SKIP_AUTH for quick dev)
$EnvLocal = Join-Path $NexusDir ".env.local"
$EnvExample = Join-Path $NexusDir ".env.example"
if (-not (Test-Path $EnvLocal) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvLocal
    Add-Content $EnvLocal "`n# Auto-added for first run`nSKIP_AUTH=true"
    Write-Host "(INFO) Created .env.local with SKIP_AUTH=true" -ForegroundColor Gray
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Cloud (Layer 1) - Nexus Console" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  http://localhost:3000"
Write-Host "  Press Ctrl+C to stop"
Write-Host ""

Push-Location $NexusDir
npm run dev
Pop-Location
