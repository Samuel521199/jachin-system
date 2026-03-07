# =============================================================================
# Cloud (平台商) - 一键安装 (Windows)
# cloud/nexus - Nexus Console
# =============================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null
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
    Write-Host "(ERROR) cloud\nexus not found" -ForegroundColor Red
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "(ERROR) Node.js not found" -ForegroundColor Red
    exit 1
}

# 已安装则跳过（node_modules 存在且含 next 表示依赖已就绪）
$NodeModules = Join-Path $NexusDir "node_modules"
$NextInstalled = Test-Path (Join-Path $NodeModules "next")
if ($NextInstalled) {
    Write-Host "(OK) cloud\nexus deps installed, skip npm install" -ForegroundColor Green
} else {
    Push-Location $NexusDir
    npm install --silent
    Pop-Location
}

# Drizzle 迁移（PostgreSQL，需 DATABASE_URL）
Push-Location $NexusDir
if (Test-Path "drizzle") {
    Write-Host "> Drizzle migration..." -ForegroundColor Gray
    npm run db:migrate 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "(OK) Drizzle migrations applied" -ForegroundColor Green }
    else { Write-Host "(SKIP) DATABASE_URL 未配置或迁移失败，跳过" -ForegroundColor DarkGray }
}
Pop-Location

Write-Host "(OK) Cloud (Nexus Console) installed" -ForegroundColor Green
Write-Host '  Start: .\scripts\start-cloud.ps1'
Write-Host ""
