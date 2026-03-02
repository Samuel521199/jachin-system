# =============================================================================
# Layer2 (用户) - 一键安装 (Windows)
# nexus_daemon + Qdrant (Docker)
# =============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 前置依赖检查
& (Join-Path $ScriptDir "check-prerequisites.ps1") layer2 -NoPrompt
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host '  Layer2 - Install' -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Qdrant
Write-Host "[1/3] Qdrant (Docker)..." -ForegroundColor Blue
if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker ps 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $QdrantData = $env:JACHIN_QDRANT_DATA
        if (-not $QdrantData) {
            if (Test-Path "E:\") { $QdrantData = "E:\docker\volumes\qdrant_data" }
            elseif (Test-Path "D:\") { $QdrantData = "D:\docker\volumes\qdrant_data" }
            else { $QdrantData = (Join-Path $ProjectRoot "qdrant_storage") }
        }
        if (-not (Test-Path $QdrantData)) { New-Item -ItemType Directory -Path $QdrantData -Force | Out-Null }
        $env:JACHIN_QDRANT_DATA = $QdrantData
        docker-compose -f (Join-Path $ProjectRoot "docker-compose.qdrant.yml") up -d
        Write-Host '  [OK] Qdrant started' -ForegroundColor Green
    } else { Write-Host '  [WARN] Docker not running, skip' -ForegroundColor Yellow }
} else { Write-Host '  [WARN] Docker not installed, skip' -ForegroundColor Yellow }

# nexus_daemon (Conda env for Python 3.11 compatibility with Ray, etc.)
Write-Host "[2/3] nexus_daemon (Python)..." -ForegroundColor Blue
$CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
$UseConda = $false
if (Get-Command conda -ErrorAction SilentlyContinue) {
    $condaEnv = conda env list 2>$null | Select-String "jachin-layer2"
    if (-not $condaEnv) {
        Write-Host '  Creating conda env jachin-layer2 (Python 3.11)...' -ForegroundColor Gray
        conda create -n jachin-layer2 python=3.11 -y 2>$null | Out-Null
    }
    $env:PYTHONUTF8 = 1
    conda run -n jachin-layer2 pip install -q -r $CoreReq 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $UseConda = $true
        $env:JACHIN_CONDA_ENV = "jachin-layer2"
        $jachinDir = Join-Path $env:USERPROFILE ".jachin"
        if (-not (Test-Path $jachinDir)) { New-Item -ItemType Directory -Path $jachinDir -Force | Out-Null }
        "jachin-layer2" | Out-File (Join-Path $jachinDir "conda_env") -Encoding ascii
    }
}
}
if (-not $UseConda) {
    $Python = $env:JACHIN_PYTHON; if (-not $Python) { $Python = "python" }
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Python not found' -ForegroundColor Red
        exit 1
    }
    $env:PYTHONUTF8 = 1
    & $Python -m pip install -q -r $CoreReq
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] pip install failed (Ray needs Python 3.10-3.12). Install conda and retry.' -ForegroundColor Red
        exit 1
    }
}
Write-Host '  [OK] nexus_daemon installed' -ForegroundColor Green

# 配对：已配对则跳过，未配对则自动执行
$ConfigPath = Join-Path $env:USERPROFILE ".jachin\nexus_config.json"
$AlreadyPaired = $false
if (Test-Path $ConfigPath) {
    try {
        $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($cfg.instance_id -and $cfg.access_token) { $AlreadyPaired = $true }
    } catch { }
}
if ($AlreadyPaired) {
    Write-Host ""
    Write-Host '[3/3] Pairing...' -ForegroundColor Blue
    Write-Host '  [SKIP] Already paired' -ForegroundColor DarkGray
} else {
    Write-Host ""
    Write-Host '[3/3] Pairing (first time)...' -ForegroundColor Blue
    & (Join-Path $ScriptDir "run-pair.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  [WARN] Pairing incomplete, run: .\scripts\run-pair.ps1 later' -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host '[OK] Layer2 install done' -ForegroundColor Green
Write-Host '  Start: .\scripts\start-layer2.ps1'
Write-Host ""
