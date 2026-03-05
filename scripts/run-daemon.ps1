# =============================================================================
# 边缘智能体守护进程 (轻量版) - 心跳 + 蓝图执行引擎
# 用法: .\scripts\run-daemon.ps1 [-BaseUrl http://localhost:3000]
# 若 conda 可用：自动创建 jachin-layer2 环境（不存在时），并在此环境中运行
# =============================================================================

param([string]$BaseUrl = $env:NEXUS_BASE_URL)

$ErrorActionPreference = "Continue"
$env:PYTHONUNBUFFERED = "1"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "[Daemon] Script started at $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Gray

$EnvName = "jachin-layer2"
$UseConda = $false
$Python = $env:JACHIN_PYTHON
if (-not $Python) { $Python = "python" }

Write-Host "[Daemon] Checking conda env $EnvName..." -ForegroundColor Gray
if (Get-Command conda -ErrorAction SilentlyContinue) {
    $null = conda run -n $EnvName --no-capture-output python -c "pass" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INFO] Env $EnvName not found or broken, creating..." -ForegroundColor Cyan
        Write-Host "  Creating Python 3.11 env..." -ForegroundColor DarkGray
        conda env remove -n $EnvName -y 2>&1 | Out-Null
        conda create -n $EnvName python=3.11 -y
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] Conda create failed (try: conda tos accept), using system Python" -ForegroundColor Yellow
        } else {
            Write-Host "  Installing deps (core/requirements.txt for Agent Loop + LLM)..." -ForegroundColor DarkGray
            $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
            if (Test-Path $CoreReq) {
                conda run -n $EnvName --no-capture-output pip install -r $CoreReq
            } else {
                conda run -n $EnvName --no-capture-output pip install httpx rich click wasmtime
            }
            $null = conda run -n $EnvName --no-capture-output python -c "import httpx, rich, click" 2>&1
            if ($LASTEXITCODE -eq 0) { $UseConda = $true }
        }
    } else {
        $null = conda run -n $EnvName --no-capture-output python -c "import httpx, rich, click" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $UseConda = $true
        } else {
            Write-Host "[INFO] Installing deps in $EnvName..." -ForegroundColor Cyan
            conda run -n $EnvName pip install httpx rich click wasmtime
            $null = conda run -n $EnvName --no-capture-output python -c "import httpx, rich, click" 2>&1
            if ($LASTEXITCODE -eq 0) { $UseConda = $true }
        }
    }
    if (-not $UseConda) {
        Write-Host "[INFO] Using system Python" -ForegroundColor DarkGray
    }
}

if (-not $UseConda) {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Python not found. Install Python or conda first.' -ForegroundColor Red
        exit 1
    }
    $null = & $Python -c "import httpx, rich, click" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[INFO] Installing daemon deps (httpx, rich, click, wasmtime)...' -ForegroundColor Cyan
        $env:PYTHONUTF8 = 1
        & $Python -m pip install httpx rich click wasmtime
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[ERROR] pip install failed' -ForegroundColor Red
            exit 1
        }
    }
}

# 配对检查：未配对则自动执行
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
    Write-Host "[Daemon] ========== Pairing Required ==========" -ForegroundColor Cyan
    Write-Host "[Daemon] Running pairing script..." -ForegroundColor Cyan
    Write-Host "[Daemon] Ensure Nexus Console is running: start.bat -> option 1" -ForegroundColor DarkGray
    Write-Host ""
    & (Join-Path $ScriptDir "run-pair.ps1") -BaseUrl $BaseUrl
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[Daemon] [FAILED] Pairing incomplete. Start Nexus Console and run again." -ForegroundColor Red
        exit 1
    }
    Write-Host "[Daemon] [OK] Pairing completed." -ForegroundColor Green
    Write-Host ""
}

Write-Host "[Daemon] Launching daemon (logs below)..." -ForegroundColor Green
Write-Host ""
if ($UseConda) {
    if ($BaseUrl) {
        conda run -n $EnvName --no-capture-output python -m core.cli daemon --base-url $BaseUrl
    } else {
        conda run -n $EnvName --no-capture-output python -m core.cli daemon
    }
} else {
    if ($BaseUrl) {
        & $Python -m core.cli daemon --base-url $BaseUrl
    } else {
        & $Python -m core.cli daemon
    }
}
$daemonExit = $LASTEXITCODE
Write-Host ""
if ($daemonExit -ne 0) {
    Write-Host "[Daemon] [FAILED] Daemon exited with code $daemonExit" -ForegroundColor Red
} else {
    Write-Host "[Daemon] Daemon stopped." -ForegroundColor Gray
}
exit $daemonExit
