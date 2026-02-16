# Start All Services - One-Click Startup
# 一键启动所有服务

param(
    [switch]$SkipBackend,
    [switch]$NoDapr
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

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
Write-Host "   Jachin-System - Start All Services" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Step 1: Check Docker
Write-Step "Step 1: Checking Docker"

try {
    docker ps | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker is not running"
    }
    Write-Success "Docker is running"
} catch {
    Write-Error "Docker is not running or not installed"
    Write-Info "Please start Docker Desktop and try again"
    exit 1
}

# Step 2: Start middleware services
Write-Step "Step 2: Starting Middleware Services (Docker)"

$composeFile = "docker-compose.dev.yml"
$projectName = "jachin-dev"

Write-Info "Starting Docker Compose services..."

# Try docker compose (v2) first, fallback to docker-compose (v1)
$dockerComposeCmd = "docker compose"
$testCmd = & docker compose version 2>&1
if ($LASTEXITCODE -ne 0) {
    $dockerComposeCmd = "docker-compose"
}

# Build command
$composeCmd = "$dockerComposeCmd -f $composeFile -p $projectName up -d"

Invoke-Expression $composeCmd

if ($LASTEXITCODE -eq 0) {
    Write-Success "Middleware services started"
    Start-Sleep -Seconds 5
    
    Write-Info "Checking service status..."
    $services = & $dockerComposeCmd -f $composeFile -p $projectName ps --format json | ConvertFrom-Json
    $runningCount = ($services | Where-Object { $_.State -eq "running" }).Count
    $totalCount = $services.Count
    
    if ($runningCount -eq $totalCount) {
        $statusColor = "Green"
    } else {
        $statusColor = "Yellow"
    }
    Write-Host "  Running: $runningCount/$totalCount services" -ForegroundColor $statusColor
    
    if ($runningCount -lt $totalCount) {
        Write-Warning "Some services may still be starting..."
    }
} else {
    Write-Error "Failed to start middleware services"
    exit 1
}

# Step 3: Check local databases
Write-Step "Step 3: Checking Local Databases"

Write-Info "Checking PostgreSQL..."
$pgPort = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($pgPort) {
    try {
        $env:PGPASSWORD = "secure_password"
        $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Success "PostgreSQL connected"
        } else {
            Write-Warning "PostgreSQL port open but connection failed"
            Write-Info "Run fix script: .\scripts\fix_postgres_quick.ps1"
        }
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } catch {
        Write-Warning "Cannot test PostgreSQL connection"
    }
} else {
    Write-Warning "PostgreSQL not running on port 5432"
    Write-Info "Please ensure PostgreSQL is installed and running"
}

Write-Info "Checking Qdrant..."
$qdrantPort = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($qdrantPort) {
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/healthz" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            Write-Success "Qdrant is running"
        }
    } catch {
        try {
            $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($health.StatusCode -eq 200) {
                Write-Success "Qdrant is running"
            }
        } catch {
            Write-Warning "Qdrant port open but cannot connect"
        }
    }
} else {
    Write-Warning "Qdrant not running on port 6333"
    Write-Info "Please ensure Qdrant is installed and running"
}

# Step 4: Start backend service
if (-not $SkipBackend) {
    Write-Step "Step 4: Starting Backend API Service"
    
    $envFile = Join-Path $ProjectRoot ".env"
    if (Test-Path $envFile) {
        Write-Info "Loading environment variables..."
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
                $key = $matches[1].Trim()
                $value = $matches[2].Trim().Trim('"').Trim("'")
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
        Write-Success "Environment variables loaded"
    }
    
    if ($env:APP_PORT) {
        $appPort = $env:APP_PORT
    } elseif ($env:SERVER_PORT) {
        $appPort = $env:SERVER_PORT
    } else {
        $appPort = "18888"
    }
    
    if ($env:SERVER_HOST) {
        $serverHost = $env:SERVER_HOST
    } else {
        $serverHost = "0.0.0.0"
    }
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "   Backend Service Configuration" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  App URL:     http://localhost:$appPort" -ForegroundColor Gray
    Write-Host "  API Docs:    http://localhost:$appPort/docs" -ForegroundColor Gray
    Write-Host "  Health:      http://localhost:$appPort/health" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host ""
    
    Write-Info "Checking Conda environment..."
    $condaEnv = conda env list 2>$null | Select-String "jachin-dev"
    if (-not $condaEnv) {
        Write-Error "Conda environment 'jachin-dev' not found"
        Write-Info "Please run: conda env create -f environment.yml"
        exit 1
    }
    
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
            break
        }
    }
    
    $env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"
    
    if ($NoDapr) {
        Write-Info "Starting backend service (without Dapr)..."
        if ($pythonExe) {
            & $pythonExe -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
        } else {
            conda run -n jachin-dev python -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
        }
    } else {
        $daprCmd = Get-Command dapr -ErrorAction SilentlyContinue
        if (-not $daprCmd) {
            Write-Warning "Dapr CLI not found, running without Dapr sidecar"
            if ($pythonExe) {
                & $pythonExe -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
            } else {
                conda run -n jachin-dev python -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
            }
        } else {
            Write-Info "Starting backend service (with Dapr sidecar)..."
            if ($env:DAPR_HTTP_PORT) {
                $daprHttpPort = $env:DAPR_HTTP_PORT
            } else {
                $daprHttpPort = "13500"
            }
            
            if ($env:DAPR_GRPC_PORT) {
                $daprGrpcPort = $env:DAPR_GRPC_PORT
            } else {
                $daprGrpcPort = "15001"
            }
            
            $componentsPath = Join-Path $ProjectRoot "dapr\components"
            $configPath = Join-Path $ProjectRoot "dapr\config\config.yaml"
            
            if ($pythonExe) {
                $pythonCmd = $pythonExe
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
                -- $pythonCmd -m uvicorn core.main:app --host $serverHost --port $appPort --reload --log-level info
        }
    }
} else {
    Write-Step "Step 4: Skipping Backend Service"
    Write-Info "Only middleware services started"
    Write-Host ""
    Write-Host "To start backend service, run:" -ForegroundColor Cyan
    Write-Host "  .\scripts\start_backend_dev.ps1" -ForegroundColor Gray
    Write-Host ""
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Startup Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  Check status: .\scripts\check_dev_services.ps1" -ForegroundColor Gray
Write-Host "  Stop all: .\scripts\stop_all.ps1" -ForegroundColor Gray
Write-Host ""
