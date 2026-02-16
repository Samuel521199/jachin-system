# Start Backend in Development Mode (Console)
# 开发模式启动后端（控制台，方便调试）

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Starting Backend (Development Mode)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Mode: Console-based (easy debugging)" -ForegroundColor Green
Write-Host "      All errors and logs will be shown here" -ForegroundColor Green
Write-Host ""

# 初始化 Conda（如果未初始化）
Write-Host "[1/5] Initializing Conda..." -ForegroundColor Cyan
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    # 尝试初始化 conda
    $condaInitScript = "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
    if (-not (Test-Path $condaInitScript)) {
        $condaInitScript = "$env:USERPROFILE\anaconda3\Scripts\conda.exe"
    }
    if (-not (Test-Path $condaInitScript)) {
        $condaInitScript = "$env:LOCALAPPDATA\Programs\Anaconda3\Scripts\conda.exe"
    }
    
    if (Test-Path $condaInitScript) {
        Write-Host "  [INFO] Found conda at: $condaInitScript" -ForegroundColor Gray
        # Conda 需要手动初始化，提示用户
        Write-Host "  [WARN] Conda not initialized in PowerShell" -ForegroundColor Yellow
        Write-Host "  [INFO] Please run: conda init powershell" -ForegroundColor Gray
        Write-Host "  [INFO] Then restart PowerShell and try again" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Conda not found" -ForegroundColor Red
        Write-Host "  [INFO] Please install Anaconda or Miniconda" -ForegroundColor Yellow
        exit 1
    }
}

# 检查 Conda 环境
Write-Host "[2/5] Checking Conda environment..." -ForegroundColor Cyan
$condaEnv = conda env list 2>$null | Select-String "jachin-dev"
if (-not $condaEnv) {
    Write-Host "  [ERROR] Conda environment 'jachin-dev' not found" -ForegroundColor Red
    Write-Host "  [INFO] Please run: conda env create -f environment.yml" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] Conda environment 'jachin-dev' found" -ForegroundColor Green

# 激活 Conda 环境（PowerShell 方式）
Write-Host "[3/5] Activating Conda environment..." -ForegroundColor Cyan

# 方法 1: 尝试使用 conda activate（如果已初始化）
try {
    # 尝试激活环境
    conda activate jachin-dev 2>&1 | Out-Null
    if ($env:CONDA_DEFAULT_ENV -eq "jachin-dev") {
        Write-Host "  [OK] Conda environment activated" -ForegroundColor Green
        $pythonCmd = "python"
    } else {
        throw "Activation failed"
    }
} catch {
    # 方法 2: 直接使用 conda 环境的 Python
    Write-Host "  [INFO] Using conda run instead..." -ForegroundColor Gray
    
    # 查找 Python 可执行文件
    $pythonExe = $null
    $possiblePaths = @(
        "$env:USERPROFILE\.conda\envs\jachin-dev\python.exe",
        "$env:LOCALAPPDATA\conda\conda\envs\jachin-dev\python.exe",
        "$env:USERPROFILE\miniconda3\envs\jachin-dev\python.exe",
        "$env:USERPROFILE\anaconda3\envs\jachin-dev\python.exe",
        "$env:CONDA_PREFIX\python.exe"
    )
    
    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $pythonExe = $path
            Write-Host "  [OK] Found Python: $path" -ForegroundColor Green
            break
        }
    }
    
    if ($pythonExe) {
        $pythonCmd = $pythonExe
    } else {
        Write-Host "  [WARN] Cannot find Python executable, will use conda run" -ForegroundColor Yellow
        $pythonCmd = $null
    }
}

# 设置环境变量
Write-Host "[4/5] Setting up environment..." -ForegroundColor Cyan
$env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"
Write-Host "  [OK] PYTHONPATH set" -ForegroundColor Green

# 加载 .env 文件
$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Write-Host "  [INFO] Loading .env file..." -ForegroundColor Gray
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
    Write-Host "  [OK] Environment variables loaded" -ForegroundColor Green
} else {
    Write-Host "  [WARN] .env file not found, using defaults" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[5/5] Starting backend service..." -ForegroundColor Cyan
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Backend Configuration" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
# 从环境变量读取端口配置
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }

Write-Host "  App URL:     http://localhost:$appPort" -ForegroundColor Gray
Write-Host "  API Docs:    http://localhost:$appPort/docs" -ForegroundColor Gray
Write-Host "  Health:      http://localhost:$appPort/health" -ForegroundColor Gray
Write-Host "  Auto Reload: Enabled" -ForegroundColor Gray
Write-Host "  Log Level:   INFO" -ForegroundColor Gray
Write-Host ""
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   Backend Logs (All output will appear here)" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# 检查 main.py 是否存在
$mainPy = Join-Path $ProjectRoot "core\main.py"
if (-not (Test-Path $mainPy)) {
    Write-Host "[ERROR] Backend main file not found: $mainPy" -ForegroundColor Red
    Write-Host "[INFO] Checking for alternative locations..." -ForegroundColor Yellow
    
    # 检查其他可能的位置
    $altPaths = @(
        Join-Path $ProjectRoot "backend\main.py",
        Join-Path $ProjectRoot "main.py"
    )
    
    $found = $false
    foreach ($altPath in $altPaths) {
        if (Test-Path $altPath) {
            Write-Host "[INFO] Found: $altPath" -ForegroundColor Green
            $mainPy = $altPath
            $found = $true
            break
        }
    }
    
    if (-not $found) {
        Write-Host "[ERROR] Cannot find main.py file" -ForegroundColor Red
        exit 1
    }
}

# 确定模块路径
if ($mainPy -match "core\\main\.py") {
    $modulePath = "core.main:app"
} elseif ($mainPy -match "backend\\main\.py") {
    $modulePath = "backend.main:app"
} else {
    $modulePath = "main:app"
}

Write-Host "[INFO] Using module: $modulePath" -ForegroundColor Gray
Write-Host "[INFO] Main file: $mainPy" -ForegroundColor Gray
Write-Host ""

# 检查 conda 环境
Write-Host "[INFO] Checking conda environment..." -ForegroundColor Blue
$condaCheck = conda run -n jachin-dev python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Cannot run Python in conda environment 'jachin-dev'" -ForegroundColor Red
    Write-Host "[INFO] Error: $condaCheck" -ForegroundColor Yellow
    Write-Host "[INFO] Try activating manually: conda activate jachin-dev" -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] Conda environment OK: $condaCheck" -ForegroundColor Green
Write-Host ""

# 尝试找到 conda Python 可执行文件
$pythonExe = $null
$condaBase = $env:CONDA_PREFIX

if ($condaBase -and (Test-Path "$condaBase\python.exe")) {
    $pythonExe = "$condaBase\python.exe"
    Write-Host "[INFO] Using conda Python: $pythonExe" -ForegroundColor Green
} else {
    # 尝试常见路径
    $possiblePaths = @(
        "$env:USERPROFILE\.conda\envs\jachin-dev\python.exe",
        "$env:LOCALAPPDATA\conda\conda\envs\jachin-dev\python.exe",
        "$env:USERPROFILE\miniconda3\envs\jachin-dev\python.exe",
        "$env:USERPROFILE\anaconda3\envs\jachin-dev\python.exe"
    )
    
    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $pythonExe = $path
            Write-Host "[INFO] Found Python: $pythonExe" -ForegroundColor Green
            break
        }
    }
}

# 启动服务
Write-Host ""
# 从环境变量读取端口配置
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$serverHost = if ($env:SERVER_HOST) { $env:SERVER_HOST } else { "0.0.0.0" }

Write-Host "[INFO] Starting uvicorn server..." -ForegroundColor Blue
Write-Host "[INFO] Module: $modulePath" -ForegroundColor Gray
Write-Host "[INFO] Host: $serverHost`:$appPort" -ForegroundColor Gray
Write-Host "[INFO] Reload: Enabled" -ForegroundColor Gray
Write-Host ""

if ($pythonExe) {
    # 直接使用 Python 可执行文件
    Write-Host "[INFO] Using direct Python execution" -ForegroundColor Gray
    & $pythonExe -m uvicorn $modulePath `
        --host $serverHost `
        --port $appPort `
        --reload `
        --log-level info `
        --use-colors
} else {
    # 回退到 conda run
    Write-Host "[INFO] Using conda run (output may be buffered)" -ForegroundColor Yellow
    Write-Host "[INFO] If you don't see output, try: conda activate jachin-dev" -ForegroundColor Yellow
    Write-Host ""
    
    # 使用 Start-Process 以确保输出正常
    conda run -n jachin-dev --no-capture-output python -m uvicorn $modulePath `
        --host $serverHost `
        --port $appPort `
        --reload `
        --log-level info `
        --use-colors
}

# 如果命令退出，显示退出码
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
    Write-Host ""
    Write-Host "[ERROR] Server exited with code: $LASTEXITCODE" -ForegroundColor Red
    Write-Host "[INFO] Check the error messages above" -ForegroundColor Yellow
    Write-Host "[INFO] Try running manually:" -ForegroundColor Yellow
    Write-Host "  conda activate jachin-dev" -ForegroundColor Gray
    Write-Host "  python -m uvicorn $modulePath --host $serverHost --port $appPort --reload --log-level info" -ForegroundColor Gray
}
