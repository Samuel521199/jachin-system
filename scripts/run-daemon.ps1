# =============================================================================
# 边缘智能体守护进程 (轻量版) - 心跳 + 蓝图执行引擎
# 用法: .\scripts\run-daemon.ps1 [-BaseUrl http://localhost:3000]
# 若 conda 可用：自动创建 jachin-layer2 环境（不存在时），并在此环境中运行
# =============================================================================

param([string]$BaseUrl = $env:NEXUS_BASE_URL)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$EnvName = "jachin-layer2"
$UseConda = $false
$Python = $env:JACHIN_PYTHON
if (-not $Python) { $Python = "python" }

if (Get-Command conda -ErrorAction SilentlyContinue) {
    $null = conda run -n $EnvName python -c "pass" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INFO] Env $EnvName not found or broken, creating..." -ForegroundColor Cyan
        Write-Host "  Creating Python 3.11 env..." -ForegroundColor DarkGray
        conda env remove -n $EnvName -y 2>$null | Out-Null
        conda create -n $EnvName python=3.11 -y
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] Conda create failed (try: conda tos accept), using system Python" -ForegroundColor Yellow
        } else {
            Write-Host "  Installing deps (core/requirements.txt for Agent Loop + LLM)..." -ForegroundColor DarkGray
            $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
            if (Test-Path $CoreReq) {
                conda run -n $EnvName pip install -r $CoreReq
            } else {
                conda run -n $EnvName pip install httpx rich click wasmtime
            }
            $null = conda run -n $EnvName python -c "import httpx, rich, click" 2>$null
            if ($LASTEXITCODE -eq 0) { $UseConda = $true }
        }
    } else {
        $null = conda run -n $EnvName python -c "import httpx, rich, click" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $UseConda = $true
        } else {
            Write-Host "[INFO] Installing deps in $EnvName..." -ForegroundColor Cyan
            conda run -n $EnvName pip install httpx rich click wasmtime
            $null = conda run -n $EnvName python -c "import httpx, rich, click" 2>$null
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
    $null = & $Python -c "import httpx, rich, click" 2>$null
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
    Write-Host "[INFO] First-time setup: pairing required..." -ForegroundColor Cyan
    Write-Host "  Ensure Nexus Console is running: start.bat -> option 1" -ForegroundColor DarkGray
    & (Join-Path $ScriptDir "run-pair.ps1") -BaseUrl $BaseUrl
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] Pairing incomplete. Start Nexus Console (start.bat -> 1) and run daemon again." -ForegroundColor Yellow
        exit 1
    }
    Write-Host ""
}

if ($UseConda) {
    if ($BaseUrl) {
        conda run -n $EnvName python -m core.cli daemon --base-url $BaseUrl
    } else {
        conda run -n $EnvName python -m core.cli daemon
    }
} else {
    if ($BaseUrl) {
        & $Python -m core.cli daemon --base-url $BaseUrl
    } else {
        & $Python -m core.cli daemon
    }
}
