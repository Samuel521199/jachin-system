# Jachin-System One-Click Deploy
# Steps: env check -> deps install -> TTS models download -> desktop build

param(
    [switch]$SkipTts,
    [switch]$SkipDesktop,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Write-Step { param([string]$Message)
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}
function Write-Info { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Blue }
function Write-Success { param([string]$Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Jachin-System Deploy" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Step 1: Base env
Write-Step "Step 1: Check base environment"

if (-not $SkipBackend) {
    $condaEnv = conda env list 2>$null | Select-String "jachin-dev"
    if (-not $condaEnv) {
        Write-Info "Conda env not found, running setup.ps1..."
        & "$ScriptDir\setup.ps1"
        if ($LASTEXITCODE -ne 0) { Write-Err "setup failed"; exit 1 }
    } else {
        Write-Success "Conda env jachin-dev exists"
    }
} else {
    Write-Info "Skipping backend env check"
}

# Step 2: Backend deps
if (-not $SkipBackend) {
    Write-Step "Step 2: Install backend deps"

    $coreReq = Join-Path $ProjectRoot "core\requirements.txt"
    $rootReq = Join-Path $ProjectRoot "requirements.txt"
    $backendReq = Join-Path $ProjectRoot "backend\requirements.txt"
    foreach ($req in @($coreReq, $rootReq, $backendReq)) {
        if (Test-Path $req) {
            Write-Info "Installing: $req"
            conda run -n jachin-dev pip install -q -r $req
        }
    }

    Write-Info "Installing TTS deps (misaki, huggingface_hub, ~1 min)..."
    conda run -n jachin-dev pip install -q 'misaki[zh]' huggingface_hub 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Success "TTS deps installed" } else { Write-Warn "misaki install failed" }
}

# Step 3: TTS models
if (-not $SkipTts -and -not $SkipBackend) {
    Write-Step "Step 3: Download TTS models"

    $ttsDir = Join-Path $ProjectRoot "data\tts"
    $modelFile = Join-Path $ttsDir "kokoro-v0_19.onnx"
    if (Test-Path $modelFile) {
        Write-Success "TTS models exist, skip"
    } else {
        Write-Info "Downloading Kokoro (~326MB, may take 2-5 min)..."
        $env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"
        conda run -n jachin-dev python "$ScriptDir\download_tts_models.py"
        if ($LASTEXITCODE -eq 0) { Write-Success "TTS models done" } else { Write-Warn "TTS download incomplete, run scripts\download_tts_models.py later" }
    }
}

# Step 4: Desktop
if (-not $SkipDesktop) {
    Write-Step "Step 4: Build desktop"

    $desktopDir = Join-Path $ProjectRoot "clients\desktop"
    if (-not (Test-Path (Join-Path $desktopDir "package.json"))) {
        Write-Err "clients/desktop not found"
        exit 1
    }

    Set-Location $desktopDir

    Write-Info "Step 4a: npm install (may take 1-2 min)..."
    npm install --silent 2>$null
    if ($LASTEXITCODE -ne 0) { npm install }
    Write-Success "npm deps ready"

    Write-Info "Step 4b: tauri build - Vite bundle first (~10s), then Rust compile (~5-10 min)..."
    Write-Info "        (You will see tsc/vite output, then cargo compile messages)"
    npm run tauri:build:tts
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "tts-local build failed, trying default..."
        npm run tauri:build
    }
    Set-Location $ProjectRoot
    Write-Success "Desktop build done"
}

# Done
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Deploy Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Info "Next:"
Write-Host "  1. Start backend: .\scripts\start.ps1" -ForegroundColor Cyan
Write-Host "  2. Run desktop: clients\desktop\src-tauri\target\release\jachin-desktop.exe" -ForegroundColor Cyan
Write-Host "  Or dev: cd clients\desktop; npm run tauri:dev:tts" -ForegroundColor Cyan
Write-Host ""
