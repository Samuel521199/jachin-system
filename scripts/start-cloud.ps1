# =============================================================================
# Cloud (Layer 1) - One-click start (Windows)
# cloud/nexus - Nexus Console @ http://localhost:3000
# 使用 Drizzle ORM + PostgreSQL
# 首次运行会创建 .env.local；若项目根 .env 有 DATABASE_URL 则自动继承
# =============================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
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

# .env.local: copy from .env.example if missing (Drizzle + PostgreSQL)
$EnvLocal = Join-Path $NexusDir ".env.local"
$EnvExample = Join-Path $NexusDir ".env.example"
$RootEnv = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvLocal)) {
    if (Test-Path $EnvExample) {
        Copy-Item $EnvExample $EnvLocal
        Add-Content $EnvLocal "`n# Auto-added for first run`nSKIP_AUTH=true"
        Write-Host "(INFO) Created .env.local from .env.example (SKIP_AUTH=true)" -ForegroundColor Gray
        # 若项目根 .env 有 DATABASE_URL 且 nexus 未配置，则继承（便于共用 PostgreSQL）
        if (Test-Path $RootEnv) {
            $DbLine = Get-Content $RootEnv | Where-Object { $_ -match "^\s*DATABASE_URL=" } | Select-Object -First 1
            if ($DbLine -and -not (Select-String -Path $EnvLocal -Pattern "^\s*DATABASE_URL=" -Quiet)) {
                Add-Content $EnvLocal "`n# Inherited from project root .env`n$DbLine"
                Write-Host "(INFO) Inherited DATABASE_URL from project root" -ForegroundColor Gray
            }
        }
    } else {
        Write-Host "(WARN) .env.example not found. Create .env.local manually." -ForegroundColor Yellow
    }
}

$UtcNow = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
Write-Host ""
Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
Write-Host "[$UtcNow]   Cloud (Layer 1) - Nexus Console" -ForegroundColor Cyan
Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
Write-Host "[$UtcNow]   http://localhost:3000"
Write-Host "[$UtcNow]   Drizzle + PostgreSQL"
Write-Host "[$UtcNow]   Press Ctrl+C to stop"
Write-Host ""

Push-Location $NexusDir
# 确保 schema 与 app 使用同一 DATABASE_URL：先迁移，再 init-store 补齐
$ErrBackup = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
npm run db:migrate 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "(WARN) db:migrate failed. Ensure PostgreSQL is running (localhost:5432)" -ForegroundColor Yellow
    Write-Host "  Ignore to continue; some features may be unavailable." -ForegroundColor Gray
}
npm run db:init-store 2>$null
$ErrorActionPreference = $ErrBackup
npm run dev
Pop-Location
