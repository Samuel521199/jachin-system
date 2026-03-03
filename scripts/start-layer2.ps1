# =============================================================================
# Layer2 (用户) - 一键启动 (Windows)
# 支持选择：nexus_daemon (完整版) 或 daemon (轻量版)
# =============================================================================

param(
    [ValidateSet("nexus","daemon","")]
    [string]$Mode = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Show menu when no mode specified
if (-not $Mode) {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Layer2 - Start Options" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Full (nexus_daemon)" -ForegroundColor White
    Write-Host "      Event Bus + Ingress + Telemetry | port 9000" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [2] Light (daemon)" -ForegroundColor White
    Write-Host "      Heartbeat + Blueprint engine | pairing required first" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Choose [1/2] (default 1): " -NoNewline -ForegroundColor Yellow
    $choice = Read-Host
    if ($choice -eq "2") { $Mode = "daemon" } else { $Mode = "nexus" }
}

# 轻量版 daemon
if ($Mode -eq "daemon") {
    & (Join-Path $ScriptDir "run-daemon.ps1")
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
if (Get-Command conda -ErrorAction SilentlyContinue) {
    $null = conda run -n $EnvName python -c "import fastapi, aiohttp" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INFO] Conda env $EnvName not found or deps missing, creating..." -ForegroundColor Cyan
        Write-Host "  Step 1/2: Creating Python 3.11 env (may take 1-2 min)..." -ForegroundColor DarkGray
        conda env remove -n $EnvName -y 2>$null | Out-Null
        conda create -n $EnvName python=3.11 -y
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] Conda create failed (try: conda tos accept), using system Python" -ForegroundColor Yellow
        } else {
            $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
            Write-Host "  Step 2/2: Installing deps from core/requirements.txt" -ForegroundColor DarkGray
            Write-Host "  (First run may take 3-5 min, pip will show progress below)" -ForegroundColor DarkGray
            Write-Host ""
            conda run -n $EnvName pip install -r $CoreReq
            Write-Host ""
            Write-Host "[INFO] Verifying install..." -ForegroundColor Cyan
            $null = conda run -n $EnvName python -c "import fastapi, aiohttp" 2>$null
            if ($LASTEXITCODE -eq 0) { $UseConda = $true }
        }
    } else {
        $UseConda = $true
    }
    if (-not $UseConda) {
        Write-Host "[INFO] Using system Python" -ForegroundColor DarkGray
    }
}

if ($UseConda) {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Layer2 (nexus_daemon) starting" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Ingress API: http://127.0.0.1:9000"
    Write-Host "  (Web UI: start.bat -> 1 -> http://localhost:3000)"
    Write-Host "  Press Ctrl+C to stop"
    Write-Host ""
    conda run -n $EnvName python -m core.nexus_daemon
} else {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Python not found. Install Python or conda first.' -ForegroundColor Red
        exit 1
    }
    $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
    if ((Test-Path $CoreReq)) {
        $null = & $Python -c "import fastapi, aiohttp" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[INFO] Installing deps from core/requirements.txt (may take 3-5 min)...' -ForegroundColor Yellow
            $env:PYTHONUTF8 = 1
            & $Python -m pip install -r $CoreReq
        }
    }
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Layer2 (nexus_daemon) starting" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Ingress API: http://127.0.0.1:9000"
    Write-Host "  (Web UI: start.bat -> 1 -> http://localhost:3000)"
    Write-Host "  Press Ctrl+C to stop"
    Write-Host ""
    & $Python -m core.nexus_daemon
}
