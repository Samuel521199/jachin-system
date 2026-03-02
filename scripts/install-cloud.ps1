# =============================================================================
# Cloud (平台商) - 一键安装 (Windows)
# cloud/nexus - Nexus Console
# =============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 前置依赖检查
& (Join-Path $ScriptDir "check-prerequisites.ps1") cloud -NoPrompt
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host '  Cloud - Install' -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$NexusDir = Join-Path $ProjectRoot "cloud\nexus"
if (-not (Test-Path $NexusDir)) {
    Write-Host '[ERROR] cloud\nexus not found' -ForegroundColor Red
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] Node.js not found' -ForegroundColor Red
    exit 1
}

Push-Location $NexusDir
npm install --silent
Pop-Location

# 可选：Supabase 迁移（悬赏大厅等表）
if (Get-Command npx -ErrorAction SilentlyContinue) {
    Push-Location $NexusDir
    try { npx supabase db push 2>$null; if ($LASTEXITCODE -eq 0) { Write-Host '[OK] Supabase migrations applied' -ForegroundColor Green } } catch { }
    Pop-Location
}

Write-Host '[OK] Cloud (Nexus Console) installed' -ForegroundColor Green
Write-Host '  Start: .\scripts\start-cloud.ps1'
Write-Host ""
