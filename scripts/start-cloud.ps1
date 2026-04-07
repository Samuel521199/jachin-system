# =============================================================================
# Cloud (Layer 1) - One-click start (Windows)
# cloud/nexus - Nexus Console @ http://localhost:3000
# 使用 Drizzle ORM + PostgreSQL
# 首次运行会创建 .env.local；若项目根 .env 有 DATABASE_URL 则自动继承
#
# 用法: .\scripts\start-cloud.ps1
#       若窗口闪退，请用 PowerShell 执行: powershell -NoExit -File .\scripts\start-cloud.ps1
# =============================================================================

param(
    [switch]$NonInteractive
)

$ErrorActionPreference = [System.Management.Automation.ActionPreference]::Continue
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$null = chcp 65001 2>$null

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Write-ErrAndPause {
    param([string]$Message, [int]$ExitCode = 1)
    Write-Host ""
    Write-Host "(ERROR) $Message" -ForegroundColor Red
    if (-not $NonInteractive) {
        Read-Host "Press Enter to close"
    }
    exit $ExitCode
}

$NexusDir = Join-Path $ProjectRoot "cloud\nexus"
if (-not (Test-Path $NexusDir)) {
    Write-ErrAndPause "cloud\nexus not found. Run: .\scripts\install-cloud.ps1"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-ErrAndPause "Node.js not found in PATH. Install Node 18+ or run: .\scripts\install-cloud.ps1"
}

$NextInstalled = Test-Path (Join-Path $NexusDir "node_modules\next")
if (-not $NextInstalled) {
    Write-Host "(INFO) Installing deps (first run)..." -ForegroundColor Yellow
    Push-Location $NexusDir
    & npm install --silent
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-ErrAndPause "npm install failed. Check network / Node version (18+)."
    }
    Pop-Location
}

# .env.local: copy from .env.example if missing (Drizzle + PostgreSQL)
$EnvLocal = Join-Path $NexusDir ".env.local"
$EnvExample = Join-Path $NexusDir ".env.example"
$RootEnv = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvLocal)) {
    if (Test-Path $EnvExample) {
        try {
            Copy-Item $EnvExample $EnvLocal -Force -ErrorAction Stop
            Add-Content $EnvLocal "`n# Auto-added for first run`nSKIP_AUTH=true"
            Write-Host "(INFO) Created .env.local from .env.example (SKIP_AUTH=true)" -ForegroundColor Gray
            if (Test-Path $RootEnv) {
                $DbLine = Get-Content -LiteralPath $RootEnv -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*DATABASE_URL=" } | Select-Object -First 1
                if ($DbLine -and -not (Select-String -Path $EnvLocal -Pattern "^\s*DATABASE_URL=" -Quiet -ErrorAction SilentlyContinue)) {
                    Add-Content $EnvLocal "`n# Inherited from project root .env`n$DbLine"
                    Write-Host "(INFO) Inherited DATABASE_URL from project root" -ForegroundColor Gray
                }
            }
        } catch {
            Write-ErrAndPause "Failed to create .env.local: $($_.Exception.Message)"
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
# 确保 schema 与 app 使用同一 DATABASE_URL：先迁移，再 init-store 幂等补齐（含 organizations.slug 等 migrate 漏跑项）
# 若未起 PostgreSQL，会出现 ECONNREFUSED：可先启动 Docker 中的 PG，或安装本机 Postgres，并核对 cloud/nexus/.env.local 的 DATABASE_URL。
# 备份 ErrorAction 必须用枚举：部分环境下 $ErrorActionPreference 读出来为 $null，直接还原会触发 ActionPreference 转换错误。
$PrevErrorAction = [System.Management.Automation.ActionPreference]::Continue
if ($null -ne $ErrorActionPreference) {
    $PrevErrorAction = $ErrorActionPreference
}
$ErrorActionPreference = [System.Management.Automation.ActionPreference]::SilentlyContinue
npm run db:migrate
if ($LASTEXITCODE -ne 0) {
    Write-Host "(WARN) db:migrate failed. Ensure PostgreSQL is running (localhost:5432)" -ForegroundColor Yellow
    Write-Host "  Ignore to continue; some features may be unavailable." -ForegroundColor Gray
}
npm run db:init-store
if ($LASTEXITCODE -ne 0) {
    Write-Host "(WARN) db:init-store failed. Store / org columns may be incomplete." -ForegroundColor Yellow
}
$ErrorActionPreference = $PrevErrorAction
npm run dev
Pop-Location
