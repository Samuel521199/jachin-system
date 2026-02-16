# Start script - 启动所有服务
# 根据流程图实现：检查 Docker -> 启动基础设施 -> 启动后端

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
Write-Host "   Starting Jachin-System v3.2" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# 检查并激活 Conda 环境
Write-Step "Checking and Activating Environment"

# 初始化 Conda（如果未初始化）
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Info "Conda command not found, trying to initialize..."
    $condaInitScript = "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
    if (-not (Test-Path $condaInitScript)) {
        $condaInitScript = "$env:USERPROFILE\anaconda3\Scripts\conda.exe"
    }
    if (-not (Test-Path $condaInitScript)) {
        $condaInitScript = "$env:LOCALAPPDATA\Programs\Anaconda3\Scripts\conda.exe"
    }
    
    if (Test-Path $condaInitScript) {
        Write-Info "Found conda at: $condaInitScript"
        Write-Warning "Conda not initialized in PowerShell"
        Write-Info "Please run: conda init powershell"
        Write-Info "Then restart PowerShell and try again"
    } else {
        Write-Error "Conda not found"
        Write-Info "Please install Anaconda or Miniconda"
        exit 1
    }
}

# 检查 Conda 环境是否存在
$condaEnv = conda env list 2>$null | Select-String "jachin-dev"
if (-not $condaEnv) {
    Write-Error "Conda environment 'jachin-dev' not found"
    Write-Info "Please run: .\scripts\setup.ps1"
    exit 1
}
Write-Success "Conda environment 'jachin-dev' found"

# 激活 Conda 环境
Write-Info "Activating conda environment 'jachin-dev'..."

# 方法 1: 尝试使用 conda activate（如果已初始化）
$condaActivated = $false
try {
    # 尝试激活环境
    conda activate jachin-dev 2>&1 | Out-Null
    if ($env:CONDA_DEFAULT_ENV -eq "jachin-dev") {
        Write-Success "Conda environment activated"
        $condaActivated = $true
    }
} catch {
    # 方法 2: 使用 conda run 或直接找到 Python 可执行文件
    Write-Info "Using alternative activation method..."
    
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
            $env:CONDA_PREFIX = Split-Path (Split-Path $path)
            $env:CONDA_DEFAULT_ENV = "jachin-dev"
            Write-Success "Conda environment activated (using Python: $path)"
            $condaActivated = $true
            break
        }
    }
    
    if (-not $condaActivated) {
        Write-Warning "Cannot activate conda environment automatically"
        Write-Info "Will use 'conda run -n jachin-dev' for commands"
    }
}

# 检查依赖
Write-Info "Verifying dependencies..."
if ($condaActivated) {
    $depCheck = python -c "import fastapi" 2>&1
} else {
    $depCheck = conda run -n jachin-dev python -c "import fastapi" 2>&1
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "Dependencies not installed"
    Write-Info "Please run: .\scripts\setup.ps1"
    exit 1
}
Write-Success "Dependencies verified"

# Step 1: 检查 Docker
Write-Step "Step 1: Checking Docker"

try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Docker found: $dockerVersion"
    } else {
        throw "Docker command failed"
    }
} catch {
    Write-Error "Docker not found or not running"
    Write-Info "Please install Docker Desktop and ensure it's running"
    Write-Info "Download: https://www.docker.com/products/docker-desktop"
    exit 1
}

# 检查 Docker 是否运行
Write-Info "Checking if Docker daemon is running..."
try {
    docker ps 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Docker daemon is running"
    } else {
        throw "Docker daemon not responding"
    }
} catch {
    Write-Error "Docker daemon is not running"
    Write-Info "Please start Docker Desktop"
    Write-Info "Waiting 5 seconds for Docker to start..."
    Start-Sleep -Seconds 5
    
    # 再次检查
    docker ps 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker daemon still not running. Please start Docker Desktop manually."
        exit 1
    }
    Write-Success "Docker daemon is now running"
}

# Step 1.5: 检查本地 Qdrant 服务（不启动容器）
Write-Step "Step 1.5: Checking Local Qdrant Service"

$qdrantRunning = $false
$qdrantCheckRetries = 3
$qdrantCheckDelay = 2
# Qdrant 不同版本使用不同健康检查路径：新版本 /readyz、/livez，旧版本 /healthz、/health
$qdrantHealthEndpoints = @("http://localhost:6333/readyz", "http://localhost:6333/livez", "http://localhost:6333/healthz", "http://localhost:6333/health", "http://localhost:6333/")

for ($retry = 1; $retry -le $qdrantCheckRetries; $retry++) {
    foreach ($url in $qdrantHealthEndpoints) {
        try {
            $response = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                $qdrantRunning = $true
                Write-Success "Local Qdrant service is running on port 6333"
                break
            }
        } catch {
            # 继续尝试下一个端点
            continue
        }
    }
    if ($qdrantRunning) { break }
    if ($retry -lt $qdrantCheckRetries) {
        Write-Info "Qdrant health check failed (attempt $retry/$qdrantCheckRetries), retrying in $qdrantCheckDelay seconds..."
        Start-Sleep -Seconds $qdrantCheckDelay
    } else {
        Write-Warning "Local Qdrant service is not responding on port 6333 after $qdrantCheckRetries attempts"
        Write-Info "Please ensure Qdrant is installed locally and running"
        Write-Info "Backend will start but vector storage features will be unavailable"
    }
}

# Step 2: 启动基础设施
Write-Step "Step 2: Starting Infrastructure"

$composeFile = Join-Path $ProjectRoot "docker-compose.dev.yml"
if (-not (Test-Path $composeFile)) {
    Write-Error "docker-compose.dev.yml not found at: $composeFile"
    exit 1
}

# 检查并清理冲突的容器
Write-Info "Checking for conflicting containers..."
$conflictContainers = @(
    "jachin-dapr-scheduler-dev",
    "jachin-dapr-placement-dev",
    "jachin-redis-dev",
    "jachin-mqtt-dev"
)

$hasConflicts = $false
foreach ($containerName in $conflictContainers) {
    $container = docker ps -a --filter "name=$containerName" --format "{{.ID}}" 2>&1
    if ($container -and $container -ne "") {
        Write-Warning "Found conflicting container: $containerName"
        $hasConflicts = $true
        
        # 停止并删除容器
        Write-Info "  Stopping container..."
        docker stop $containerName 2>&1 | Out-Null
        Write-Info "  Removing container..."
        docker rm $containerName 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "  Removed: $containerName"
        }
    }
}

if ($hasConflicts) {
    Write-Info "Cleaned up conflicting containers"
    Write-Info "Waiting 2 seconds before starting services..."
    Start-Sleep -Seconds 2
}

# 先停止现有服务（如果存在）
Write-Info "Stopping existing services (if any)..."
docker-compose -f $composeFile down 2>&1 | Out-Null

Write-Info "Starting Docker Compose services..."
Write-Info "Compose file: docker-compose.dev.yml"
docker-compose -f $composeFile up -d

if ($LASTEXITCODE -eq 0) {
    Write-Success "Infrastructure services started"
    Write-Info "Waiting for services to be ready..."
    Start-Sleep -Seconds 5
    
    # 检查关键服务
    Write-Info "Checking service status..."
    $services = docker-compose -f $composeFile ps --services
    if ($services) {
        foreach ($service in $services) {
            $status = docker-compose -f $composeFile ps $service | Select-String "Up"
            if ($status) {
                Write-Success "  [OK] $service"
            }
            else {
                Write-Warning "  [CHECK] $service (checking...)"
            }
        }
    }
} else {
    Write-Error "Failed to start infrastructure services"
    Write-Info "Check logs with: docker-compose -f docker-compose.dev.yml logs"
    exit 1
}

# Step 3: 启动后端
Write-Step "Step 3: Starting Backend"

# 检查端口 18888 是否被占用（多次检查确保准确性）
$backendPort = 18888
Write-Info "Checking if port $backendPort is available..."
$maxRetries = 3
$portFree = $false

for ($retry = 1; $retry -le $maxRetries; $retry++) {
    $portProcesses = netstat -ano | Select-String ":$backendPort\s" | Select-String "LISTENING"
    
    if (-not $portProcesses) {
        $portFree = $true
        Write-Success "Port $backendPort is available (check $retry/$maxRetries)"
        break
    }
    
    if ($retry -eq 1) {
        Write-Warning "Port $backendPort is in use, attempting to free it..."
    }
    
    $processIds = $portProcesses | ForEach-Object {
        $_.ToString().Split()[-1]
    } | Select-Object -Unique
    
    foreach ($processId in $processIds) {
        try {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($process) {
                Write-Warning "Port $backendPort is in use by process: $($process.ProcessName) (PID: $processId)"
                Write-Info "Stopping process $processId..."
                
                # 尝试正常关闭
                try {
                    Stop-Process -Id $processId -Force -ErrorAction Stop
                    Write-Success "Stopped process $processId"
                    Start-Sleep -Seconds 2
                } catch {
                    # 如果失败，尝试使用 taskkill
                    Write-Info "Normal stop failed, trying taskkill..."
                    $result = Start-Process -FilePath "taskkill" -ArgumentList "/F", "/PID", $processId -Wait -NoNewWindow -PassThru
                    if ($result.ExitCode -eq 0) {
                        Write-Success "Stopped process $processId using taskkill"
                        Start-Sleep -Seconds 2
                    } else {
                        Write-Warning "Failed to stop process $processId. Access denied."
                        if ($retry -eq $maxRetries) {
                            Write-Info ""
                            Write-Info "========================================" -ForegroundColor Yellow
                            Write-Info "  Port $backendPort Conflict Resolution" -ForegroundColor Yellow
                            Write-Info "========================================" -ForegroundColor Yellow
                            Write-Info ""
                            Write-Info "Option 1: Run as Administrator" -ForegroundColor Cyan
                            Write-Info "  1. Right-click PowerShell → Run as Administrator" -ForegroundColor White
                            Write-Info "  2. Run: taskkill /F /PID $processId" -ForegroundColor White
                            Write-Info ""
                            Write-Info "Option 2: Manual kill" -ForegroundColor Cyan
                            Write-Info "  taskkill /F /PID $processId" -ForegroundColor White
                            Write-Info ""
                            Write-Info "Option 3: Task Manager" -ForegroundColor Cyan
                            Write-Info "  Find PID $processId and end task" -ForegroundColor White
                            Write-Info ""
                            Write-Error "Cannot start backend - port $backendPort is still in use"
                            Write-Info "Please close the process and try again"
                            exit 1
                        }
                    }
                }
            } else {
                # 进程不存在，但端口仍显示被占用（可能是 TIME_WAIT 状态）
                Write-Info "Process $processId not found, but port still shows as LISTENING"
                Write-Info "This may be a stale connection. Waiting for port to be released..."
                Start-Sleep -Seconds 3
            }
        } catch {
            Write-Warning "Failed to check process $processId : $_"
        }
    }
    
    if ($retry -lt $maxRetries) {
        Write-Info "Waiting 2 seconds before retry..."
        Start-Sleep -Seconds 2
    }
}

if (-not $portFree) {
    Write-Error "Port $backendPort is still in use after $maxRetries attempts"
    Write-Info "Please manually close the process and try again"
    exit 1
}

# 确保 conda 环境已激活
if (-not $condaActivated) {
    Write-Info "Using conda run -n jachin-dev for backend commands"
}

# 检查 Dapr
$daprCmd = Get-Command dapr -ErrorAction SilentlyContinue
if (-not $daprCmd) {
    Write-Error "Dapr CLI not found"
    Write-Info "Please install Dapr CLI or run: .\scripts\setup.ps1"
    exit 1
}

# 设置路径
$componentsPath = Join-Path $ProjectRoot "dapr\components"
$configPath = Join-Path $ProjectRoot "dapr\config\config.yaml"

# 验证路径
if (-not (Test-Path $componentsPath)) {
    Write-Error "Dapr components path not found: $componentsPath"
    exit 1
}
if (-not (Test-Path $configPath)) {
    Write-Warning "Dapr config file not found: $configPath (using defaults)"
}

# 设置环境变量
$env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"

# 检查 API Key
$qwenApiKey = [System.Environment]::GetEnvironmentVariable("QWEN_AI_API_KEY", "User")
if (-not $qwenApiKey) {
    $qwenApiKey = [System.Environment]::GetEnvironmentVariable("QWEN_AI_API_KEY", "Machine")
}
if (-not $qwenApiKey) {
    # 尝试从 .env 文件读取
    $envFile = Join-Path $ProjectRoot ".env"
    if (Test-Path $envFile) {
        $envContent = Get-Content $envFile
        foreach ($line in $envContent) {
            if ($line -match "^QWEN_AI_API_KEY=(.+)$" -or $line -match "^QWEN_API_KEY=(.+)$") {
                $qwenApiKey = $matches[1].Trim('"').Trim("'")
                break
            }
        }
    }
}

if ($qwenApiKey) {
    $env:QWEN_AI_API_KEY = $qwenApiKey
    $env:QWEN_API_KEY = $qwenApiKey
    Write-Success "API Key found (length: $($qwenApiKey.Length))"
} else {
    Write-Warning "QWEN_AI_API_KEY not set. Some features may not work."
    Write-Info "Set it in .env file or environment variable"
}

# 显示启动信息
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Backend Configuration" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  App ID:      jachin-brain" -ForegroundColor Gray
Write-Host "  App Port:    $backendPort" -ForegroundColor Gray
Write-Host "  Dapr HTTP:   3500" -ForegroundColor Gray
$daprGrpcPortDisplay = if ($env:DAPR_GRPC_PORT) { $env:DAPR_GRPC_PORT } else { "15001" }
Write-Host "  Dapr gRPC:   $daprGrpcPortDisplay" -ForegroundColor Gray
Write-Host ""

Write-Info "Starting backend with Dapr..."
Write-Warning "Press Ctrl+C to stop"
Write-Host ""

# 使用 wrapper script 或直接运行
$wrapperScript = Join-Path $ScriptDir "run_backend.bat"
if (Test-Path $wrapperScript) {
    Write-Info "Using wrapper script: run_backend.bat"
    $backendCmd = $wrapperScript
} else {
    if ($condaActivated) {
        Write-Info "Using activated conda environment"
        $backendCmd = "python -m uvicorn core.main:app --host 0.0.0.0 --port $backendPort"
    } else {
        Write-Info "Using conda run (environment not activated)"
        $backendCmd = "conda run -n jachin-dev python -m uvicorn core.main:app --host 0.0.0.0 --port $backendPort"
    }
}

# 启动后端前最后检查端口（检查进程是否真实存在）
Write-Info "Final check: Verifying port $backendPort is free..."
Start-Sleep -Seconds 2
$finalCheck = netstat -ano | Select-String ":$backendPort\s" | Select-String "LISTENING"
if ($finalCheck) {
    $finalPids = $finalCheck | ForEach-Object { $_.ToString().Split()[-1] } | Select-Object -Unique
    $realProcesses = @()
    foreach ($processId in $finalPids) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) {
            $realProcesses += $processId
        }
    }
    
    if ($realProcesses.Count -gt 0) {
        Write-Error "Port $backendPort is still in use by process(es): $($realProcesses -join ', ')"
        Write-Info "Please close these processes before starting the backend:"
        foreach ($processId in $realProcesses) {
            Write-Info "  taskkill /F /PID $processId"
        }
        exit 1
    } else {
        Write-Warning "Port $backendPort shows as LISTENING but processes don't exist (stale connection)"
        Write-Info "Waiting 5 seconds for port to be released..."
        Start-Sleep -Seconds 5
        
        # 再次检查
        $finalCheck2 = netstat -ano | Select-String ":$backendPort\s" | Select-String "LISTENING"
        if ($finalCheck2) {
            Write-Warning "Port may still be in TIME_WAIT state. Trying to start anyway..."
            Write-Info "If startup fails, wait a few seconds and try again"
        } else {
            Write-Success "Port $backendPort is now free"
        }
    }
} else {
    Write-Success "Port $backendPort confirmed free"
}

# 启动 Dapr（过滤 scheduler 错误和心跳日志）
$script:schedulerErrorLastShown = 0
$script:heartbeatLogLastShown = 0
$heartbeatLogInterval = 60  # 每60秒显示一次心跳日志摘要

# Get Dapr ports from environment or use defaults
$daprHttpPort = if ($env:DAPR_HTTP_PORT) { $env:DAPR_HTTP_PORT } else { "3500" }
$daprGrpcPort = if ($env:DAPR_GRPC_PORT) { $env:DAPR_GRPC_PORT } else { "15001" }

# Check if Dapr ports are available
$httpPortInUse = netstat -ano | Select-String ":$daprHttpPort\s" | Select-String "LISTENING"
$grpcPortInUse = netstat -ano | Select-String ":$daprGrpcPort\s" | Select-String "LISTENING"

if ($httpPortInUse) {
    Write-Warning "Dapr HTTP port $daprHttpPort is in use. Trying alternative port..."
    $daprHttpPort = "13500"
}
if ($grpcPortInUse) {
    Write-Warning "Dapr gRPC port $daprGrpcPort is in use. Trying alternative port..."
    $daprGrpcPort = "15001"
}

Write-Info "Using Dapr ports: HTTP=$daprHttpPort, gRPC=$daprGrpcPort"

# Dapr 控制平面地址（placement/scheduler）— 适配多种部署场景
# 说明：https://docs.dapr.io/reference/cli/dapr-run/
# - 本地开发：Docker Compose 将 placement/scheduler 映射到 localhost，宿主机 daprd 需显式指定
#   否则 mDNS 返回容器内网 IP，宿主机无法访问
# - 云/多级部署：通过环境变量或 .env 覆盖，如 DAPR_PLACEMENT_HOST_ADDRESS=placement.example.com:6050
# - 跳过：设置 DAPR_PLACEMENT_HOST_ADDRESS=skip 或 DAPR_SCHEDULER_HOST_ADDRESS=skip 使用 mDNS 发现
$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -match "^DAPR_PLACEMENT_HOST_ADDRESS=(.+)$" -and -not $env:DAPR_PLACEMENT_HOST_ADDRESS) {
            $env:DAPR_PLACEMENT_HOST_ADDRESS = $matches[1].Trim('"').Trim("'")
        }
        if ($line -match "^DAPR_SCHEDULER_HOST_ADDRESS=(.+)$" -and -not $env:DAPR_SCHEDULER_HOST_ADDRESS) {
            $env:DAPR_SCHEDULER_HOST_ADDRESS = $matches[1].Trim('"').Trim("'")
        }
    }
}
$placementHost = if ($env:DAPR_PLACEMENT_HOST_ADDRESS) { $env:DAPR_PLACEMENT_HOST_ADDRESS.Trim() } else { "localhost:6050" }
$schedulerHost = if ($env:DAPR_SCHEDULER_HOST_ADDRESS) { $env:DAPR_SCHEDULER_HOST_ADDRESS.Trim() } else { "localhost:6060" }
$skipPlacement = $placementHost -in @("skip", "mdns", "")
$skipScheduler = $schedulerHost -in @("skip", "mdns", "")
if (-not $skipPlacement) { Write-Host "  Placement:  $placementHost" -ForegroundColor DarkGray }
if (-not $skipScheduler) { Write-Host "  Scheduler:  $schedulerHost" -ForegroundColor DarkGray }

# 构建 dapr run 参数
$daprRunArgs = @(
    "--app-id", "jachin-brain",
    "--app-port", $backendPort,
    "--dapr-http-port", $daprHttpPort,
    "--dapr-grpc-port", $daprGrpcPort
)
if (-not $skipPlacement) {
    $daprRunArgs += "--placement-host-address", $placementHost
}
if (-not $skipScheduler) {
    $daprRunArgs += "--scheduler-host-address", $schedulerHost
}
$daprRunArgs += "--resources-path", $componentsPath, "--config", $configPath, "--log-level", "error", "--", $backendCmd

& dapr run @daprRunArgs 2>&1 | 
    ForEach-Object {
        $line = $_
        $now = [DateTimeOffset]::Now.ToUnixTimeSeconds()
        
        # 过滤 scheduler 连接错误（已知的 Dapr 1.16.5 限制）
        if ($line -match "Failed to connect to scheduler host" -or $line -match "scheduler.watchhosts") {
            if (($now - $script:schedulerErrorLastShown) -ge 60) {
                Write-Host "[WARN] Scheduler connection retrying (harmless, known Dapr limitation)" -ForegroundColor DarkYellow
                $script:schedulerErrorLastShown = $now
            }
            return
        }
        
        # 过滤心跳日志（降低显示频率）
        if ($line -match "system/heartbeat" -or $line -match "POST.*heartbeat") {
            if (($now - $script:heartbeatLogLastShown) -ge $heartbeatLogInterval) {
                Write-Host "[INFO] Heartbeat received (showing every $heartbeatLogInterval seconds)" -ForegroundColor DarkGray
                $script:heartbeatLogLastShown = $now
            }
            return
        }
        
        # 显示其他输出
        Write-Host $line
    }
