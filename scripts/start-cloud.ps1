# =============================================================================
# Cloud (Layer 1) - One-click start (Windows)
# cloud/nexus - Nexus Console @ http://localhost:3000
# 使用 Drizzle ORM + PostgreSQL
# 首次运行会创建 .env.local；若项目根 .env 有 DATABASE_URL 则自动继承
#
# 双击 .ps1 时窗口会在脚本结束后立刻关闭，若「闪退」看不到报错，请从项目根打开 PowerShell 执行：
#   .\scripts\start-cloud.ps1
# 或保留窗口：powershell -NoExit -File .\scripts\start-cloud.ps1
# =============================================================================

param(
    [switch]$NonInteractive
)

# 勿用 Stop：Copy-Item/Get-Content 等一旦报错会整段终止，双击运行时窗口一闪而过看不到原因
$ErrorActionPreference = [System.Management.Automation.ActionPreference]::Continue
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$null = chcp 65001 2>$null

function Pause-End {
    if (-not $NonInteractive) { Read-Host "按 Enter 关闭" }
}

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
try {
    Set-Location -LiteralPath $ProjectRoot -ErrorAction Stop
} catch {
    Write-Host "(ERROR) 无法进入项目目录: $ProjectRoot" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Pause-End
    exit 1
}

$NexusDir = Join-Path $ProjectRoot "cloud\nexus"
if (-not (Test-Path -LiteralPath $NexusDir)) {
    Write-Host "(ERROR) cloud\nexus not found. Run: .\scripts\install-cloud.ps1" -ForegroundColor Red
    Pause-End
    exit 1
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "(ERROR) Node.js not found. Run: .\scripts\install-cloud.ps1" -ForegroundColor Red
    Pause-End
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
if (-not (Test-Path -LiteralPath $EnvLocal)) {
    if (Test-Path -LiteralPath $EnvExample) {
        try {
            Copy-Item -LiteralPath $EnvExample -Destination $EnvLocal -Force -ErrorAction Stop
        } catch {
            Write-Host "(ERROR) 无法复制 .env.example -> .env.local: $($_.Exception.Message)" -ForegroundColor Red
            Pause-End
            exit 1
        }
        Add-Content -LiteralPath $EnvLocal -Value "`n# Auto-added for first run`nSKIP_AUTH=true"
        Write-Host "(INFO) Created .env.local from .env.example (SKIP_AUTH=true)" -ForegroundColor Gray
        # 若项目根 .env 有 DATABASE_URL 且 nexus 未配置，则继承（便于共用 PostgreSQL）
        if (Test-Path -LiteralPath $RootEnv) {
            $DbLine = Get-Content -LiteralPath $RootEnv -ErrorAction SilentlyContinue | Where-Object { $_ -match "^\s*DATABASE_URL=" } | Select-Object -First 1
            if ($DbLine -and -not (Select-String -Path $EnvLocal -Pattern "^\s*DATABASE_URL=" -Quiet -ErrorAction SilentlyContinue)) {
                Add-Content -LiteralPath $EnvLocal -Value "`n# Inherited from project root .env`n$DbLine"
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
# UTF-8 BOM 会导致 package.json / drizzle meta 无法 JSON.parse（tsx、Next/webpack 报错）；在任意 npm 前先清理
node .\scripts\ensure-json-no-bom.cjs 2>$null
# 确保 schema 与 app 使用同一 DATABASE_URL：先迁移，再 init-store 幂等补齐（含 organizations.slug 等 migrate 漏跑项）
$ErrBackup = $ErrorActionPreference; $ErrorActionPreference = "SilentlyContinue"
npm run db:migrate
if ($LASTEXITCODE -ne 0) {
    Write-Host "(WARN) db:migrate failed. Ensure PostgreSQL is running (localhost:5432)" -ForegroundColor Yellow
    Write-Host "  Ignore to continue; some features may be unavailable." -ForegroundColor Gray
}
npm run db:init-store
if ($LASTEXITCODE -ne 0) {
    Write-Host "(WARN) db:init-store failed. Store / org columns may be incomplete." -ForegroundColor Yellow
}
$ErrorActionPreference = $ErrBackup
npm run dev
Pop-Location
