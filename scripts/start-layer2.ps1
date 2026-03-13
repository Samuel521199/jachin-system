# =============================================================================
# Layer2 (用户) - 一键启动 (Windows)
# 支持选择：nexus_daemon (完整版) 或 daemon (轻量版)
# =============================================================================

param(
    [ValidateSet("nexus","daemon","gateway","")]
    [string]$Mode = ""
)

$ErrorActionPreference = "Continue"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 实时输出：Python 不缓冲
$env:PYTHONUNBUFFERED = "1"

try {
Write-Host ""
Write-Host "[Layer2] Script started at $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Gray

# Show menu when no mode specified
if (-not $Mode) {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Layer2 - Start Options" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Full (nexus_daemon)" -ForegroundColor White
    Write-Host "      Event Bus + Ingress + Telemetry + Sensory (18881) | port 9000" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [2] Light (daemon)" -ForegroundColor White
    Write-Host "      Heartbeat + Blueprint + Sensory (18881) | pairing required first" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [3] Gateway (L2 审批网关)" -ForegroundColor White
    Write-Host "      FastAPI + Admin 面板 (18888) | 审批 L3 节点，Token 自动配置" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Choose [1/2/3] (default 1): " -NoNewline -ForegroundColor Yellow
    $choice = Read-Host
    if ($choice -eq "2") { $Mode = "daemon" }
    elseif ($choice -eq "3") { $Mode = "gateway" }
    else { $Mode = "nexus" }
}

# Gateway 模式：L2 审批网关，自动配置 Token，直接审批 L3
if ($Mode -eq "gateway") {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "run-gateway.ps1")
    exit $LASTEXITCODE
}

# 轻量版 daemon
if ($Mode -eq "daemon") {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "run-daemon.ps1")
    exit $LASTEXITCODE
}

# Full mode: nexus_daemon
$DaemonDir = Join-Path $ProjectRoot "core\nexus_daemon"
if (-not (Test-Path $DaemonDir)) {
    Write-Host ""
    Write-Host "[TIP] nexus_daemon not found. Use light version:" -ForegroundColor Yellow
    Write-Host "  Re-run and choose [2] Light" -ForegroundColor Gray
    Write-Host "  Or start.bat -> option 2" -ForegroundColor Gray
    Write-Host ""
    Write-Host "[ERROR] core\nexus_daemon not found. Run: .\scripts\install-layer2.ps1" -ForegroundColor Red
    exit 1
}

$EnvName = "jachin-layer2"
$UseConda = $false
$Python = $env:JACHIN_PYTHON
if (-not $Python) { $Python = "python" }

# Conda detection: create jachin-layer2 if missing (Python 3.11 supports ray, etc.)
Write-Host "[Layer2] Checking conda env $EnvName..." -ForegroundColor Gray
if (Get-Command conda -ErrorAction SilentlyContinue) {
    $null = conda run -n $EnvName --no-capture-output python -c "import fastapi, aiohttp" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[Layer2] Conda env $EnvName not found or deps missing, creating..." -ForegroundColor Cyan
        Write-Host "  Step 1/2: Creating Python 3.11 env (may take 1-2 min)..." -ForegroundColor DarkGray
        conda env remove -n $EnvName -y 2>&1 | Out-Null
        conda create -n $EnvName python=3.11 -y
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] Conda create failed (try: conda tos accept), using system Python" -ForegroundColor Yellow
        } else {
            $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
            Write-Host "  Step 2/2: Installing deps from core/requirements.txt" -ForegroundColor DarkGray
            Write-Host "  (First run may take 3-5 min, pip will show progress below)" -ForegroundColor DarkGray
            Write-Host ""
            conda run -n $EnvName --no-capture-output pip install -r $CoreReq
            Write-Host ""
            Write-Host "[Layer2] Verifying install..." -ForegroundColor Cyan
            $null = conda run -n $EnvName --no-capture-output python -c "import fastapi, aiohttp" 2>&1
            if ($LASTEXITCODE -eq 0) { $UseConda = $true }
        }
    } else {
        $UseConda = $true
    }
    if (-not $UseConda) {
        Write-Host "[INFO] Using system Python" -ForegroundColor DarkGray
    }
}

# 配对检查：未配对则自动执行（与 run-daemon.ps1 一致）
$ConfigPath = Join-Path $env:USERPROFILE ".jachin\nexus_config.json"
$AlreadyPaired = $false
if (Test-Path $ConfigPath) {
    try {
        $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($cfg.instance_id -and $cfg.access_token) { $AlreadyPaired = $true }
    } catch { }
}
if (-not $AlreadyPaired) {
    Write-Host ""
    Write-Host "[Layer2] ========== Pairing Required ==========" -ForegroundColor Cyan
    Write-Host "[Layer2] First-time setup: running pairing script..." -ForegroundColor Cyan
    Write-Host "[Layer2] Ensure Nexus Console is running: start.bat -> option 1, or: cd cloud\nexus; npm run dev" -ForegroundColor DarkGray
    Write-Host ""
    & powershell -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "run-pair.ps1")
    $pairExit = $LASTEXITCODE
    Write-Host ""
    if ($pairExit -ne 0) {
        Write-Host "[Layer2] [FAILED] Pairing incomplete (exit code $pairExit)." -ForegroundColor Red
        Write-Host "[Layer2] Start Nexus Console first, then run start-layer2.ps1 again." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[Layer2] [OK] Pairing completed successfully." -ForegroundColor Green
    Write-Host ""
}

$UtcNow = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
Write-Host ""
Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
Write-Host "[$UtcNow]   Layer2 (nexus_daemon) - Starting" -ForegroundColor Cyan
Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
Write-Host "[$UtcNow]   Ingress API: http://127.0.0.1:9000"
Write-Host "[$UtcNow]   Web UI: http://localhost:3000"
Write-Host "[$UtcNow]   Press Ctrl+C to stop"
Write-Host ""
Write-Host "[Layer2] Launching nexus_daemon (logs below)..." -ForegroundColor Green
Write-Host ""

if ($UseConda) {
    conda run -n $EnvName --no-capture-output python -m core.nexus_daemon
    $daemonExit = $LASTEXITCODE
} else {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Python not found. Install Python or conda first.' -ForegroundColor Red
        exit 1
    }
    $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
    if ((Test-Path $CoreReq)) {
        $null = & $Python -c "import fastapi, aiohttp" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[Layer2] Installing deps from core/requirements.txt (may take 3-5 min)...' -ForegroundColor Yellow
            $env:PYTHONUTF8 = 1
            & $Python -m pip install -r $CoreReq
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[Layer2] [FAILED] pip install failed." -ForegroundColor Red
                exit 1
            }
        }
    }
    & $Python -m core.nexus_daemon
    $daemonExit = $LASTEXITCODE
}

Write-Host ""
if ($daemonExit -ne 0) {
    Write-Host "[Layer2] [FAILED] nexus_daemon exited with code $daemonExit" -ForegroundColor Red
    exit $daemonExit
}
Write-Host "[Layer2] Daemon stopped (Ctrl+C or normal exit)" -ForegroundColor Gray
} finally {
    Write-Host ""
    Read-Host "Press Enter to exit"
}
