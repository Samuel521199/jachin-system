# Development Mode Startup Script
# 开发模式启动脚本
# - 中间件服务（Redis、MQTT、Dapr）通过 Docker 运行
# - 后端应用在控制台运行，方便查看日志和调试

param(
    [switch]$SkipInfrastructure,
    [switch]$NoDapr
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 彩色输出函数
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
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Jachin-System Development Mode" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Mode: Console-based backend (easy debugging)" -ForegroundColor Cyan
Write-Host "      Docker-based middleware (Redis, MQTT, Dapr)" -ForegroundColor Cyan
Write-Host ""

# Step 1: 检查并激活环境
Write-Step "Step 1: Checking and Activating Environment"

# 检查 conda 是否可用
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Error "Conda not found. Please install Anaconda or Miniconda"
    Write-Info "Download: https://www.anaconda.com/products/distribution"
    exit 1
}

# 检查环境是否存在
$condaEnv = conda env list 2>$null | Select-String "jachin-dev"
if (-not $condaEnv) {
    Write-Error "Conda environment 'jachin-dev' not found"
    Write-Info "Please run: conda env create -f environment.yml"
    exit 1
}
Write-Success "Conda environment 'jachin-dev' found"

# 尝试激活 conda 环境
Write-Info "Activating conda environment 'jachin-dev'..."

# 方法 1: 如果 conda 已初始化，尝试激活
try {
    # 查找 Python 可执行文件（作为激活的替代方案）
    $pythonExe = $null
    $possiblePaths = @(
        "$env:USERPROFILE\.conda\envs\jachin-dev\python.exe",
        "$env:LOCALAPPDATA\conda\conda\envs\jachin-dev\python.exe",
        "$env:USERPROFILE\miniconda3\envs\jachin-dev\python.exe",
        "$env:USERPROFILE\anaconda3\envs\jachin-dev\python.exe"
    )
    
    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $pythonExe = $path
            $env:CONDA_PREFIX = Split-Path (Split-Path $path)
            $env:CONDA_DEFAULT_ENV = "jachin-dev"
            Write-Success "Conda environment activated (using Python: $path)"
            $script:PythonExe = $pythonExe
            break
        }
    }
    
    if (-not $pythonExe) {
        Write-Warning "Cannot find Python executable automatically"
        Write-Info "Will use conda run (output may be buffered)"
        Write-Info "Tip: Run 'conda init powershell' and restart PowerShell for better output"
        $script:PythonExe = $null
    }
} catch {
    Write-Warning "Cannot activate conda environment automatically: $_"
    Write-Info "Will use conda run instead"
    $script:PythonExe = $null
}

# 检查 Python 依赖
Write-Info "Checking Python dependencies..."
if ($script:PythonExe) {
    $depCheck = & $script:PythonExe -c "import fastapi, uvicorn" 2>&1
} else {
    $depCheck = conda run -n jachin-dev python -c "import fastapi, uvicorn" 2>&1
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "Dependencies not installed"
    Write-Info "Please run: conda activate jachin-dev"
    Write-Info "Then: pip install -r core/requirements.txt"
    exit 1
}
Write-Success "Python dependencies verified"

# Step 2: 启动中间件服务（Docker）
if (-not $SkipInfrastructure) {
    Write-Step "Step 2: Starting Middleware Services (Docker)"
    
    # 检查 Docker
    try {
        docker ps 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker not running"
        }
        Write-Success "Docker is running"
    } catch {
        Write-Error "Docker is not running"
        Write-Info "Please start Docker Desktop"
        exit 1
    }
    
    # 启动 Docker 服务
    $composeFile = "docker-compose.dev.yml"
    $projectName = "jachin-dev"
    
    Write-Info "Starting middleware services..."
    Write-Info "  - Redis (port 6379)"
    Write-Info "  - MQTT (ports 1883, 9001)"
    Write-Info "  - Dapr Placement (port 6050)"
    Write-Info "  - Dapr Scheduler (port 6060)"
    Write-Host ""
    
    docker-compose -f $composeFile -p $projectName up -d
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Middleware services started"
        Write-Info "Waiting for services to initialize..."
        Start-Sleep -Seconds 3
        
        # 检查服务状态
        $services = @("redis", "mqtt", "dapr-placement", "dapr-scheduler")
        foreach ($service in $services) {
            $status = docker-compose -f $composeFile -p $projectName ps $service 2>&1 | Select-String "Up"
            if ($status) {
                Write-Success "  [OK] $service"
            } else {
                Write-Warning "  [CHECK] $service"
            }
        }
    } else {
        Write-Error "Failed to start middleware services"
        Write-Info "Check logs: docker-compose -f $composeFile -p $projectName logs"
        exit 1
    }
} else {
    Write-Step "Step 2: Skipping Infrastructure (using existing services)"
}

# Step 3: 检查本地数据库
Write-Step "Step 3: Checking Local Databases"

# 检查 PostgreSQL
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    try {
        $env:PGPASSWORD = "secure_password"
        $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "PostgreSQL - Connected"
        } else {
            Write-Warning "PostgreSQL - Port open but connection failed"
            Write-Info "Run: .\scripts\fix_postgres_quick.ps1"
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "PostgreSQL - Cannot test connection"
    }
} else {
    Write-Warning "PostgreSQL - Not running on port 5432"
    Write-Info "Please start PostgreSQL locally"
}

# 检查 Qdrant
$qdrantPort = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($qdrantPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Success "Qdrant - Running"
        }
    } catch {
        Write-Warning "Qdrant - Port open but health check failed"
    }
} else {
    Write-Warning "Qdrant - Not running on port 6333"
    Write-Info "Please start Qdrant locally or use Docker"
}

# Step 4: 配置环境变量
Write-Step "Step 4: Configuring Environment"

# 设置 PYTHONPATH
$env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"

# 加载 .env 文件
$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Write-Info "Loading environment variables from .env..."
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
            Write-Host "  [SET] $key" -ForegroundColor DarkGray
        }
    }
    Write-Success "Environment variables loaded"
} else {
    Write-Warning ".env file not found, using defaults"
}

# 检查 API Key
$qwenApiKey = $env:QWEN_API_KEY
if (-not $qwenApiKey) {
    $qwenApiKey = $env:QWEN_AI_API_KEY
}
if (-not $qwenApiKey) {
    $qwenApiKey = [System.Environment]::GetEnvironmentVariable("QWEN_AI_API_KEY", "User")
}

if ($qwenApiKey) {
    Write-Success "API Key found (length: $($qwenApiKey.Length))"
} else {
    Write-Warning "QWEN_API_KEY not set. Some features may not work."
    Write-Info "Set it in .env file or environment variable"
}

# Step 5: 启动后端服务（控制台模式）
Write-Step "Step 5: Starting Backend Service (Console Mode)"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Backend Configuration" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Mode:        Console (for debugging)" -ForegroundColor Gray
Write-Host "  App ID:      jachin-brain" -ForegroundColor Gray
# 从环境变量读取端口配置
$appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { "18888" }
$daprHttpPort = if ($env:DAPR_HTTP_PORT) { $env:DAPR_HTTP_PORT } else { "13500" }
$daprGrpcPort = if ($env:DAPR_GRPC_PORT) { $env:DAPR_GRPC_PORT } else { "15001" }

Write-Host "  App Port:    $appPort" -ForegroundColor Gray
if (-not $NoDapr) {
    Write-Host "  Dapr HTTP:   $daprHttpPort" -ForegroundColor Gray
    Write-Host "  Dapr gRPC:   $daprGrpcPort" -ForegroundColor Gray
}
Write-Host "  Log Level:   INFO (detailed)" -ForegroundColor Gray
Write-Host "  Auto Reload: Enabled" -ForegroundColor Gray
Write-Host ""

# 检查 Dapr（如果需要）
if (-not $NoDapr) {
    $daprCmd = Get-Command dapr -ErrorAction SilentlyContinue
    if (-not $daprCmd) {
        Write-Warning "Dapr CLI not found, running without Dapr sidecar"
        Write-Info "Install Dapr: https://docs.dapr.io/getting-started/install-dapr-cli/"
        $NoDapr = $true
    }
}

# 设置工作目录
Set-Location $ProjectRoot

# 构建启动命令
$componentsPath = Join-Path $ProjectRoot "dapr\components"
$configPath = Join-Path $ProjectRoot "dapr\config\config.yaml"
$mainPy = Join-Path $ProjectRoot "core\main.py"

# 检查 main.py 是否存在
if (-not (Test-Path $mainPy)) {
    Write-Error "Backend main file not found: $mainPy"
    exit 1
}

Write-Info "Starting backend service..."
Write-Warning "Press Ctrl+C to stop"
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "   Backend Logs (Errors will be shown)" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# 从环境变量读取主机配置
$serverHost = if ($env:SERVER_HOST) { $env:SERVER_HOST } else { "0.0.0.0" }

if ($NoDapr) {
    # 直接运行，不使用 Dapr
    Write-Info "Running without Dapr sidecar..."
    if ($script:PythonExe) {
        & $script:PythonExe -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
    } else {
        conda run -n jachin-dev python -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
    }
} else {
    # 使用 Dapr 运行
    Write-Info "Running with Dapr sidecar..."
    
    # 过滤 Dapr 的噪音日志，但保留错误
    $script:schedulerErrorLastShown = 0
    $script:heartbeatLogLastShown = 0
    $heartbeatLogInterval = 60
    
    # 构建 Python 命令
    if ($script:PythonExe) {
        $pythonCmd = $script:PythonExe
    } else {
        $pythonCmd = "conda run -n jachin-dev python"
    }
    
    & dapr run `
        --app-id "jachin-brain" `
        --app-port $appPort `
        --dapr-http-port $daprHttpPort `
        --dapr-grpc-port $daprGrpcPort `
        --resources-path "$componentsPath" `
        --config "$configPath" `
        --log-level warn `
        -- $pythonCmd -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info 2>&1 | 
        ForEach-Object {
            $line = $_
            $now = [DateTimeOffset]::Now.ToUnixTimeSeconds()
            
            # 过滤 scheduler 连接错误（已知问题）
            if ($line -match "Failed to connect to scheduler host" -or $line -match "scheduler.watchhosts") {
                if (($now - $script:schedulerErrorLastShown) -ge 60) {
                    Write-Host "[DAPR] Scheduler connection retrying (harmless)" -ForegroundColor DarkYellow
                    $script:schedulerErrorLastShown = $now
                }
                return
            }
            
            # 过滤心跳日志
            if ($line -match "system/heartbeat" -or $line -match "POST.*heartbeat") {
                if (($now - $script:heartbeatLogLastShown) -ge $heartbeatLogInterval) {
                    Write-Host "[DAPR] Heartbeat (showing every ${heartbeatLogInterval}s)" -ForegroundColor DarkGray
                    $script:heartbeatLogLastShown = $now
                }
                return
            }
            
            # 高亮错误和警告
            if ($line -match "ERROR|Error|error|Exception|Traceback|Failed|failed") {
                Write-Host $line -ForegroundColor Red
            } elseif ($line -match "WARN|Warning|warning") {
                Write-Host $line -ForegroundColor Yellow
            } elseif ($line -match "INFO|Info") {
                Write-Host $line -ForegroundColor Cyan
            } else {
                Write-Host $line
            }
        }
}
