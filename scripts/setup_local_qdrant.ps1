# Setup Local Qdrant
# This script helps install and start Qdrant locally

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setting up Local Qdrant" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if Qdrant is already running
$port6333 = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($port6333) {
    Write-Host "[INFO] Qdrant is already running on port 6333" -ForegroundColor Green
    Write-Host "[INFO] Testing connection..." -ForegroundColor Cyan
    
    try {
        $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($health.StatusCode -eq 200) {
            $healthJson = $health.Content | ConvertFrom-Json
            if ($healthJson.status -eq "ok") {
                Write-Host "[SUCCESS] Qdrant is running and healthy!" -ForegroundColor Green
                exit 0
            }
        }
    } catch {
        Write-Host "[WARN] Qdrant port is open but health check failed" -ForegroundColor Yellow
    }
}

Write-Host "[INFO] Qdrant is not running" -ForegroundColor Yellow
Write-Host ""

# Check if Qdrant executable exists
$qdrantPath = $null
$possiblePaths = @(
    "C:\qdrant\qdrant.exe",
    "$env:USERPROFILE\qdrant\qdrant.exe",
    "$env:LOCALAPPDATA\qdrant\qdrant.exe",
    ".\qdrant\qdrant.exe",
    "qdrant.exe"
)

Write-Host "[1/3] Checking for Qdrant installation..." -ForegroundColor Cyan

foreach ($path in $possiblePaths) {
    if (Test-Path $path) {
        $qdrantPath = $path
        Write-Host "  [OK] Found Qdrant at: $qdrantPath" -ForegroundColor Green
        break
    }
}

if (-not $qdrantPath) {
    Write-Host "  [INFO] Qdrant not found locally" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please choose an installation method:" -ForegroundColor Cyan
    Write-Host "  1. Download and run Qdrant manually" -ForegroundColor Gray
    Write-Host "  2. Use Docker to run Qdrant (recommended for Windows)" -ForegroundColor Gray
    Write-Host "  3. Skip Qdrant setup for now" -ForegroundColor Gray
    Write-Host ""
    
    $choice = Read-Host "Enter your choice (1/2/3)"
    
    switch ($choice) {
        "1" {
            Write-Host ""
            Write-Host "To download Qdrant:" -ForegroundColor Cyan
            Write-Host "  1. Visit: https://github.com/qdrant/qdrant/releases" -ForegroundColor Gray
            Write-Host "  2. Download: qdrant-windows-x86_64.zip" -ForegroundColor Gray
            Write-Host "  3. Extract to a folder (e.g., C:\qdrant)" -ForegroundColor Gray
            Write-Host "  4. Run: C:\qdrant\qdrant.exe" -ForegroundColor Gray
            Write-Host ""
            Write-Host "After installation, run this script again to verify." -ForegroundColor Yellow
            exit 0
        }
        "2" {
            Write-Host ""
            Write-Host "[INFO] Starting Qdrant with Docker..." -ForegroundColor Cyan
            
            # Check if Docker is running
            try {
                docker ps | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "Docker is not running"
                }
            } catch {
                Write-Host "[ERROR] Docker is not running or not installed" -ForegroundColor Red
                Write-Host "[INFO] Please start Docker Desktop and try again" -ForegroundColor Yellow
                exit 1
            }
            
            # Check if Qdrant container already exists
            $existingContainer = docker ps -a --filter "name=qdrant-local" --format "{{.Names}}"
            if ($existingContainer -eq "qdrant-local") {
                Write-Host "  [INFO] Found existing Qdrant container, starting it..." -ForegroundColor Gray
                docker start qdrant-local
            } else {
                Write-Host "  [INFO] Creating new Qdrant container..." -ForegroundColor Gray
                docker run -d `
                    --name qdrant-local `
                    -p 6333:6333 `
                    -p 6334:6334 `
                    -v "${PWD}\data\qdrant:/qdrant/storage" `
                    qdrant/qdrant:latest
            }
            
            Write-Host "  [INFO] Waiting for Qdrant to start..." -ForegroundColor Gray
            Start-Sleep -Seconds 5
            
            # Test connection
            $maxRetries = 10
            $retryCount = 0
            $connected = $false
            
            while ($retryCount -lt $maxRetries) {
                try {
                    $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
                    if ($health.StatusCode -eq 200) {
                        $healthJson = $health.Content | ConvertFrom-Json
                        if ($healthJson.status -eq "ok") {
                            $connected = $true
                            break
                        }
                    }
                } catch {
                    $retryCount++
                    Write-Host "  [INFO] Waiting for Qdrant... ($retryCount/$maxRetries)" -ForegroundColor Gray
                    Start-Sleep -Seconds 2
                }
            }
            
            if ($connected) {
                Write-Host "  [SUCCESS] Qdrant is running in Docker!" -ForegroundColor Green
                Write-Host ""
                Write-Host "Connection details:" -ForegroundColor Cyan
                Write-Host "  REST API: http://localhost:6333" -ForegroundColor Gray
                Write-Host "  gRPC API: http://localhost:6334" -ForegroundColor Gray
                Write-Host ""
                Write-Host "To stop Qdrant: docker stop qdrant-local" -ForegroundColor DarkGray
                Write-Host "To start Qdrant: docker start qdrant-local" -ForegroundColor DarkGray
                exit 0
            } else {
                Write-Host "  [ERROR] Qdrant failed to start in Docker" -ForegroundColor Red
                Write-Host "  [INFO] Check logs: docker logs qdrant-local" -ForegroundColor Yellow
                exit 1
            }
        }
        "3" {
            Write-Host "[INFO] Skipping Qdrant setup" -ForegroundColor Yellow
            Write-Host "[INFO] You can set it up later using docs/LOCAL_DATABASE_SETUP.md" -ForegroundColor Gray
            exit 0
        }
        default {
            Write-Host "[ERROR] Invalid choice" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host ""
    Write-Host "[2/3] Starting Qdrant..." -ForegroundColor Cyan
    
    # Check if Qdrant is already running
    $qdrantProcess = Get-Process -Name "qdrant" -ErrorAction SilentlyContinue
    if ($qdrantProcess) {
        Write-Host "  [INFO] Qdrant is already running" -ForegroundColor Green
    } else {
        Write-Host "  [INFO] Starting Qdrant from: $qdrantPath" -ForegroundColor Gray
        
        # Create data directory if it doesn't exist
        $dataDir = Join-Path $PWD "data\qdrant"
        if (-not (Test-Path $dataDir)) {
            New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        }
        
        # Start Qdrant in background
        $qdrantJob = Start-Job -ScriptBlock {
            param($exePath, $dataDir)
            Set-Location (Split-Path $exePath)
            & $exePath --storage-path $dataDir
        } -ArgumentList $qdrantPath, $dataDir
        
        Write-Host "  [INFO] Waiting for Qdrant to start..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
        
        # Test connection
        $maxRetries = 10
        $retryCount = 0
        $connected = $false
        
        while ($retryCount -lt $maxRetries) {
            try {
                $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
                if ($health.StatusCode -eq 200) {
                    $healthJson = $health.Content | ConvertFrom-Json
                    if ($healthJson.status -eq "ok") {
                        $connected = $true
                        break
                    }
                }
            } catch {
                $retryCount++
                Write-Host "  [INFO] Waiting for Qdrant... ($retryCount/$maxRetries)" -ForegroundColor Gray
                Start-Sleep -Seconds 2
            }
        }
        
        if ($connected) {
            Write-Host "  [SUCCESS] Qdrant started successfully!" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] Qdrant failed to start" -ForegroundColor Red
            Write-Host "  [INFO] Check if port 6333 is available" -ForegroundColor Yellow
            exit 1
        }
    }
}

Write-Host ""
Write-Host "[3/3] Verifying Qdrant connection..." -ForegroundColor Cyan

try {
    $health = Invoke-WebRequest -Uri "http://localhost:6333/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    if ($health.StatusCode -eq 200) {
        $healthJson = $health.Content | ConvertFrom-Json
        if ($healthJson.status -eq "ok") {
            Write-Host "  [SUCCESS] Qdrant is running and healthy!" -ForegroundColor Green
            Write-Host ""
            Write-Host "Connection details:" -ForegroundColor Cyan
            Write-Host "  REST API: http://localhost:6333" -ForegroundColor Gray
            Write-Host "  gRPC API: http://localhost:6334" -ForegroundColor Gray
            Write-Host ""
            Write-Host "QDRANT_URL: http://localhost:6333" -ForegroundColor DarkGray
            Write-Host "QDRANT_GRPC_URL: http://localhost:6334" -ForegroundColor DarkGray
        }
    }
} catch {
    Write-Host "  [ERROR] Cannot connect to Qdrant: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Qdrant setup completed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
