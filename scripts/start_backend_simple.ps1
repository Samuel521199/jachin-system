# Simple Backend Startup (Direct Python Execution)
# 简单后端启动（直接使用 Python，确保输出正常）
# 自动激活 conda 环境

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Starting Backend (Simple Mode)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 尝试激活 conda 环境
Write-Host "[1/3] Activating Conda environment..." -ForegroundColor Cyan

# 方法 1: 如果 conda 已初始化，尝试激活
try {
    # 检查 conda 是否可用
    $condaAvailable = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaAvailable) {
        # 尝试激活环境
        # 注意：在 PowerShell 中，conda activate 需要先初始化
        # 我们使用 conda run 或直接找到 Python 可执行文件
        
        # 查找 conda 环境的 Python
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
            Write-Host "  [OK] Using conda environment Python" -ForegroundColor Green
        } else {
            # 如果找不到，检查环境是否存在
            $envExists = conda env list 2>$null | Select-String "jachin-dev"
            if ($envExists) {
                Write-Host "  [WARN] Cannot find Python executable, using conda run" -ForegroundColor Yellow
                $pythonCmd = $null
            } else {
                Write-Host "  [ERROR] Conda environment 'jachin-dev' not found" -ForegroundColor Red
                Write-Host "  [INFO] Please create it: conda env create -f environment.yml" -ForegroundColor Yellow
                exit 1
            }
        }
    } else {
        throw "Conda not found"
    }
} catch {
    Write-Host "  [ERROR] Cannot activate conda environment: $_" -ForegroundColor Red
    Write-Host "  [INFO] Please ensure conda is installed and initialized" -ForegroundColor Yellow
    Write-Host "  [INFO] Run: conda init powershell (then restart PowerShell)" -ForegroundColor Yellow
    exit 1
}

# 检查是否已经在正确的环境中
if ($env:CONDA_DEFAULT_ENV -eq "jachin-dev") {
    Write-Host "  [OK] Already in jachin-dev environment" -ForegroundColor Green
    $pythonCmd = "python"
}

# 设置环境变量
Write-Host "[2/3] Setting up environment..." -ForegroundColor Cyan
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
}

# 检查 main.py
Write-Host "[3/3] Checking files..." -ForegroundColor Cyan
$mainPy = Join-Path $ProjectRoot "core\main.py"
if (-not (Test-Path $mainPy)) {
    Write-Host "  [ERROR] core\main.py not found" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Main file found: $mainPy" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Backend Configuration" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
# 从环境变量读取端口配置
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$serverHost = if ($env:SERVER_HOST) { $env:SERVER_HOST } else { "0.0.0.0" }

Write-Host "  App URL:     http://localhost:$appPort" -ForegroundColor Gray
Write-Host "  API Docs:    http://localhost:$appPort/docs" -ForegroundColor Gray
Write-Host "  Health:      http://localhost:$appPort/health" -ForegroundColor Gray
Write-Host "  Auto Reload: Enabled" -ForegroundColor Gray
Write-Host ""
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   Backend Logs" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# 启动服务
if ($pythonCmd) {
    if ($pythonCmd -eq "python") {
        # 使用当前环境的 Python
        python -m uvicorn core.main:app `
            --host $serverHost `
            --port $appPort `
            --reload `
            --log-level info `
            --use-colors
    } else {
        # 使用找到的 Python 可执行文件
        & $pythonCmd -m uvicorn core.main:app `
            --host $serverHost `
            --port $appPort `
            --reload `
            --log-level info `
            --use-colors
    }
} else {
    # 使用 conda run
    conda run -n jachin-dev python -m uvicorn core.main:app `
        --host $serverHost `
        --port $appPort `
        --reload `
        --log-level info `
        --use-colors
}

if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
    Write-Host ""
    Write-Host "[ERROR] Server exited with code: $LASTEXITCODE" -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:" -ForegroundColor Cyan
    Write-Host "  1. Ensure conda is initialized: conda init powershell" -ForegroundColor Gray
    Write-Host "  2. Restart PowerShell after conda init" -ForegroundColor Gray
    Write-Host "  3. Or activate manually: conda activate jachin-dev" -ForegroundColor Gray
    Write-Host "  4. Then run: python -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info" -ForegroundColor Gray
}
