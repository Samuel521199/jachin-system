# =============================================================================
# Layer2 (用户) - 一键安装 (Windows)
# nexus_daemon（V2：LanceDB 本地向量记忆）
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

# V2: 向量由 LanceDB 管理，跳过独立向量服务

# nexus_daemon (Conda env for Python 3.11)
Write-Host "[1/4] nexus_daemon (Python)..." -ForegroundColor Blue
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
if (-not $UseConda) {
    $Python = $env:JACHIN_PYTHON; if (-not $Python) { $Python = "python" }
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Python not found' -ForegroundColor Red
        exit 1
    }
    $env:PYTHONUTF8 = 1
    & $Python -m pip install -q -r $CoreReq
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] pip install failed. Install conda and retry.' -ForegroundColor Red
        exit 1
    }
}
Write-Host '  [OK] nexus_daemon installed' -ForegroundColor Green

# L1 信任：已有 nexus_config 则跳过；否则自动跑 CLI 辅助配对（有 Gateway 时可改用 /gateway Nexus 登录）
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
    Write-Host '[2/4] Pairing...' -ForegroundColor Blue
    Write-Host '  [SKIP] Already paired' -ForegroundColor DarkGray
} else {
    Write-Host ""
    Write-Host '[2/4] Pairing (first time)...' -ForegroundColor Blue
    & (Join-Path $ScriptDir "run-pair.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  [WARN] Pairing incomplete, run: .\scripts\run-pair.ps1 later' -ForegroundColor Yellow
    }
}

# Edge Embedding 模型预下载 (sentence-transformers all-MiniLM-L6-v2, ~90MB)
# L2 记忆向量检索在 edge 模式下首次使用时会自动下载，此处预下载避免首次启动卡顿
Write-Host ""
Write-Host '[3/4] Edge Embedding (all-MiniLM-L6-v2)...' -ForegroundColor Blue
$env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"
if ($UseConda) {
    $null = conda run -n jachin-layer2 python (Join-Path $ScriptDir "download_embedding_model.py") 2>&1
} else {
    & $Python (Join-Path $ScriptDir "download_embedding_model.py") 2>&1
}
if ($LASTEXITCODE -eq 0) {
    Write-Host '  [OK] Embedding model ready' -ForegroundColor Green
} else {
    Write-Host '  [WARN] Skip, will auto-download on first edge mode use' -ForegroundColor Yellow
}

# TTS 模型预下载 (MOSS ONNX) - L2 为 L3 桌面端提供模型就绪检查服务
Write-Host ""
Write-Host '[4/4] TTS model (MOSS ONNX)...' -ForegroundColor Blue
$ttsModelFile = Join-Path $ProjectRoot "data\tts\kokoro-v0_19.onnx"
if (Test-Path $ttsModelFile) {
    Write-Host '  [SKIP] TTS model exists' -ForegroundColor DarkGray
} else {
    if ($UseConda) {
        conda run -n jachin-layer2 pip install -q huggingface_hub 2>$null | Out-Null
        conda run -n jachin-layer2 python (Join-Path $ScriptDir "download_tts_models.py") 2>&1
    } else {
        & $Python -m pip install -q huggingface_hub 2>$null | Out-Null
        & $Python (Join-Path $ScriptDir "download_tts_models.py") 2>&1
    }
    if (Test-Path $ttsModelFile) {
        Write-Host '  [OK] TTS model ready' -ForegroundColor Green
    } else {
        Write-Host '  [WARN] TTS download incomplete, run later: python scripts\download_tts_models.py' -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[OK] Layer2 install done" -ForegroundColor Green
Write-Host "  Start: .\scripts\start-layer2.ps1"
Write-Host "  Light: start.bat daemon or .\scripts\run-daemon.ps1"
Write-Host ""
