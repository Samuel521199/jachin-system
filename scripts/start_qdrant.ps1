# 启动本地 Qdrant 服务脚本
# 用于开发环境

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Starting Local Qdrant Service" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Docker 是否运行
$dockerRunning = docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}

# 检查 Qdrant 容器是否存在
$containerExists = docker ps -a --filter "name=qdrant-local" --format "{{.Names}}" | Select-String "qdrant-local"

if ($containerExists) {
    # 检查容器是否运行
    $containerRunning = docker ps --filter "name=qdrant-local" --format "{{.Names}}" | Select-String "qdrant-local"
    
    if ($containerRunning) {
        Write-Host "[INFO] Qdrant container is already running" -ForegroundColor Green
    } else {
        Write-Host "[INFO] Starting existing Qdrant container..." -ForegroundColor Yellow
        docker start qdrant-local
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[SUCCESS] Qdrant container started" -ForegroundColor Green
        } else {
            Write-Host "[ERROR] Failed to start Qdrant container" -ForegroundColor Red
            exit 1
        }
    }
} else {
    # 创建新容器
    Write-Host "[INFO] Creating new Qdrant container..." -ForegroundColor Yellow
    
    # 确保存储目录存在
    $storagePath = Join-Path $PSScriptRoot "..\qdrant_storage"
    if (-not (Test-Path $storagePath)) {
        New-Item -ItemType Directory -Path $storagePath -Force | Out-Null
        Write-Host "[INFO] Created storage directory: $storagePath" -ForegroundColor Cyan
    }
    
    docker run -d `
      --name qdrant-local `
      -p 6333:6333 `
      -p 6334:6334 `
      -v "${storagePath}:/qdrant/storage" `
      qdrant/qdrant:latest
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[SUCCESS] Qdrant container created and started" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Failed to create Qdrant container" -ForegroundColor Red
        exit 1
    }
}

# 等待服务就绪
Write-Host "[INFO] Waiting for Qdrant to be ready..." -ForegroundColor Yellow
$maxRetries = 30
$retryCount = 0
$isReady = $false

while ($retryCount -lt $maxRetries -and -not $isReady) {
    Start-Sleep -Seconds 1
    $retryCount++
    
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:6333/health" -TimeoutSec 2 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            $isReady = $true
        }
    } catch {
        # 继续重试
    }
}

if ($isReady) {
    Write-Host "[SUCCESS] Qdrant is ready!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  REST API: http://localhost:6333" -ForegroundColor Cyan
    Write-Host "  gRPC API: http://localhost:6334" -ForegroundColor Cyan
    Write-Host "  Web UI:   http://localhost:6333/dashboard" -ForegroundColor Cyan
    Write-Host ""
    exit 0
} else {
    Write-Host "[WARNING] Qdrant container started but health check failed" -ForegroundColor Yellow
    Write-Host "[INFO] You can check logs with: docker logs qdrant-local" -ForegroundColor Cyan
    exit 1
}
