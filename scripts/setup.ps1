# Setup script - 一键设置开发环境
# 根据流程图实现：检查/创建 Conda 环境 -> 安装依赖 -> 配置 Dapr

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 彩色日志函数
function Write-Step {
    param([string]$Message)
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Jachin-System v2.0 Setup Script" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Step 1: 检查/创建 Conda 环境
Write-Step "Step 1: Checking Conda Environment"

$condaEnv = conda env list 2>$null | Select-String "jachin-dev"
if (-not $condaEnv) {
    Write-Info "Conda environment 'jachin-dev' not found, creating..."
    
    # 检查 environment.yml 是否存在
    $envFile = Join-Path $ProjectRoot "environment.yml"
    if (-not (Test-Path $envFile)) {
        Write-Info "environment.yml not found, creating basic Conda environment..."
        conda create -n jachin-dev python=3.11 -y
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to create Conda environment"
            exit 1
        }
    } else {
        Write-Info "Found environment.yml, creating environment..."
        conda env create -f $envFile
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to create Conda environment from environment.yml"
            exit 1
        }
    }
    Write-Success "Conda environment 'jachin-dev' created"
} else {
    Write-Success "Conda environment 'jachin-dev' already exists"
}

# Step 2: 安装依赖
Write-Step "Step 2: Installing Dependencies"

Write-Info "Installing backend dependencies..."
$backendReq = Join-Path $ProjectRoot "backend\requirements.txt"
$rootReq = Join-Path $ProjectRoot "requirements.txt"

if (Test-Path $backendReq) {
    Write-Info "Installing from backend/requirements.txt..."
    conda run -n jachin-dev pip install -q -r $backendReq
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Backend dependencies installed"
    } else {
        Write-Warning "Some backend dependencies may have failed"
    }
}

if (Test-Path $rootReq) {
    Write-Info "Installing from requirements.txt..."
    conda run -n jachin-dev pip install -q -r $rootReq
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Root dependencies installed"
    } else {
        Write-Warning "Some root dependencies may have failed"
    }
}

# TTS 可选依赖（Split-Inference phonemize）
Write-Info "Installing TTS optional deps (misaki, huggingface_hub)..."
conda run -n jachin-dev pip install -q 'misaki[zh]' huggingface_hub 2>$null
if ($LASTEXITCODE -eq 0) { Write-Success "TTS deps installed" } else { Write-Warning "TTS deps optional, phonemize may be unavailable" }

# 验证关键依赖
Write-Info "Verifying critical dependencies..."
$criticalDeps = @("fastapi", "uvicorn", "pydantic")
$allOk = $true
foreach ($dep in $criticalDeps) {
    $check = conda run -n jachin-dev python -c "import $dep" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Success "  [OK] $dep"
    } else {
        Write-Error "  [FAIL] $dep (missing)"
        $allOk = $false
    }
}

if (-not $allOk) {
    Write-Warning "Some critical dependencies are missing"
}

# Step 3: 检查并安装 Dapr CLI
Write-Step "Step 3: Setting up Dapr"

$daprCmd = Get-Command dapr -ErrorAction SilentlyContinue
if (-not $daprCmd) {
    Write-Warning "Dapr CLI not found in PATH"
    Write-Info "Install with: powershell -Command `"iwr https://raw.githubusercontent.com/dapr/cli/master/install/install.ps1 -useb | iex`""
    Write-Warning "Continuing without Dapr CLI"
} else {
    Write-Success "Dapr CLI found: $($daprCmd.Source)"
    
    $daprdPath = "$env:USERPROFILE\.dapr\bin\daprd.exe"
    if (-not (Test-Path $daprdPath)) {
        Write-Info "Dapr runtime not found, initializing..."
        dapr uninstall 2>$null | Out-Null
        dapr init -s --runtime-version 1.16.5 2>&1 | Out-Null
        
        if (Test-Path $daprdPath) {
            Write-Success "Dapr runtime initialized (version 1.16.5)"
        } else {
            Write-Warning "Dapr init may have failed"
        }
    } else {
        Write-Success "Dapr runtime exists"
    }
}

# Step 4: 设置环境变量文件
Write-Step "Step 4: Setting up Environment Variables"

$envFile = Join-Path $ProjectRoot ".env"
$envExample = Join-Path $ProjectRoot ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Write-Info "Creating .env file from .env.example..."
        Copy-Item $envExample $envFile
        Write-Success ".env file created"
        Write-Warning "Please edit .env and set QWEN_API_KEY"
    } else {
        Write-Warning ".env.example not found"
    }
} else {
    Write-Success ".env file already exists"
}

# 完成
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "        Setup Complete! [OK]" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Info "Next steps:"
Write-Host "  1. conda activate jachin-dev" -ForegroundColor Cyan
Write-Host "  2. Edit .env and set QWEN_API_KEY" -ForegroundColor Cyan
Write-Host "  3. .\scripts\start.ps1" -ForegroundColor Cyan
Write-Host ""
