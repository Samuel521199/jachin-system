# Check Local Database Configuration Script
# Verify PostgreSQL and Qdrant are properly installed and running

# Set console output encoding to UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Checking Local Database Configuration" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$allOk = $true

# Check PostgreSQL
Write-Host "[1/2] Checking PostgreSQL..." -ForegroundColor Cyan

# Check if PostgreSQL process is running
$pgProcess = Get-Process -Name "postgres" -ErrorAction SilentlyContinue
if ($pgProcess) {
    Write-Host "  [OK] PostgreSQL process is running" -ForegroundColor Green
} else {
    Write-Host "  [WARN] PostgreSQL process not detected" -ForegroundColor Yellow
    Write-Host "  [INFO] Please ensure PostgreSQL service is started" -ForegroundColor Gray
}

# Check if port 5432 is in use
$port5432 = Get-NetTCPConnection -LocalPort 5432 -ErrorAction SilentlyContinue
if ($port5432) {
    Write-Host "  [OK] Port 5432 is in use (PostgreSQL may be running)" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Port 5432 is not in use" -ForegroundColor Red
    Write-Host "  [INFO] PostgreSQL may not be started" -ForegroundColor Yellow
    $allOk = $false
}

# Try to connect to PostgreSQL
try {
    $env:PGPASSWORD = "secure_password"
    $pgTest = & psql -U jachin -d jachin_brain -c "SELECT 1;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] PostgreSQL connection successful" -ForegroundColor Green
        Write-Host "  [INFO] Database: jachin_brain" -ForegroundColor Gray
        Write-Host "  [INFO] User: jachin" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] PostgreSQL connection failed" -ForegroundColor Red
        Write-Host "  [DETAIL] $pgTest" -ForegroundColor Gray
        $allOk = $false
    }
} catch {
    Write-Host "  [ERROR] Cannot connect to PostgreSQL: $_" -ForegroundColor Red
    Write-Host "  [INFO] Please check if PostgreSQL is installed and running" -ForegroundColor Yellow
    Write-Host "  [INFO] See docs/LOCAL_DATABASE_SETUP.md for installation instructions" -ForegroundColor Gray
    $allOk = $false
}

Write-Host ""

# Check Qdrant
Write-Host "[2/2] Checking Qdrant..." -ForegroundColor Cyan

# Check if port 6333 is in use
$port6333 = Get-NetTCPConnection -LocalPort 6333 -ErrorAction SilentlyContinue
if ($port6333) {
    Write-Host "  [OK] Port 6333 is in use (Qdrant may be running)" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Port 6333 is not in use" -ForegroundColor Red
    Write-Host "  [INFO] Qdrant may not be started" -ForegroundColor Yellow
    $allOk = $false
}

# Check if port 6334 is in use
$port6334 = Get-NetTCPConnection -LocalPort 6334 -ErrorAction SilentlyContinue
if ($port6334) {
    Write-Host "  [OK] Port 6334 is in use (Qdrant gRPC may be running)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Port 6334 is not in use (gRPC port)" -ForegroundColor Yellow
}

# Try to connect to Qdrant
try {
    # Try different health check endpoints
    $healthEndpoints = @(
        "http://localhost:6333/healthz",
        "http://localhost:6333/health",
        "http://localhost:6333/"
    )
    
    $connected = $false
    foreach ($endpoint in $healthEndpoints) {
        try {
            $qdrantHealth = Invoke-WebRequest -Uri $endpoint -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($qdrantHealth.StatusCode -eq 200) {
                # Try to parse as JSON if possible
                try {
                    $healthJson = $qdrantHealth.Content | ConvertFrom-Json
                    if ($healthJson.status -eq "ok" -or $healthJson.result.status -eq "ok") {
                        Write-Host "  [OK] Qdrant health check passed" -ForegroundColor Green
                        Write-Host "  [INFO] REST API: http://localhost:6333" -ForegroundColor Gray
                        Write-Host "  [INFO] gRPC API: http://localhost:6334" -ForegroundColor Gray
                        $connected = $true
                        break
                    }
                } catch {
                    # If not JSON, just check if we got a 200 response
                    if ($qdrantHealth.StatusCode -eq 200) {
                        Write-Host "  [OK] Qdrant is responding (endpoint: $endpoint)" -ForegroundColor Green
                        Write-Host "  [INFO] REST API: http://localhost:6333" -ForegroundColor Gray
                        Write-Host "  [INFO] gRPC API: http://localhost:6334" -ForegroundColor Gray
                        $connected = $true
                        break
                    }
                }
            }
        } catch {
            # Try next endpoint
            continue
        }
    }
    
    if (-not $connected) {
        # If ports are open but health check fails, it might still be working
        if ($port6333) {
            Write-Host "  [WARN] Qdrant ports are open but health check failed" -ForegroundColor Yellow
            Write-Host "  [INFO] Qdrant may still be working, try accessing http://localhost:6333/dashboard" -ForegroundColor Gray
            # Don't fail the check if ports are open
        } else {
            Write-Host "  [ERROR] Cannot connect to Qdrant" -ForegroundColor Red
            Write-Host "  [INFO] Please check if Qdrant is installed and running" -ForegroundColor Yellow
            Write-Host "  [INFO] See docs/LOCAL_DATABASE_SETUP.md for installation instructions" -ForegroundColor Gray
            $allOk = $false
        }
    }
} catch {
    if ($port6333) {
        Write-Host "  [WARN] Qdrant ports are open but connection test failed: $_" -ForegroundColor Yellow
        Write-Host "  [INFO] Qdrant may still be working" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Cannot connect to Qdrant: $_" -ForegroundColor Red
        Write-Host "  [INFO] Please check if Qdrant is installed and running" -ForegroundColor Yellow
        Write-Host "  [INFO] See docs/LOCAL_DATABASE_SETUP.md for installation instructions" -ForegroundColor Gray
        $allOk = $false
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

if ($allOk) {
    Write-Host "  [SUCCESS] All local database checks passed" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Start other Docker services:" -ForegroundColor Gray
    Write-Host "     docker-compose -f docker-compose.dev.yml up -d" -ForegroundColor DarkGray
    Write-Host "  2. Initialize database:" -ForegroundColor Gray
    Write-Host "     .\installer\init_database.ps1" -ForegroundColor DarkGray
    Write-Host "  3. Start backend service:" -ForegroundColor Gray
    Write-Host "     .\scripts\start.ps1" -ForegroundColor DarkGray
} else {
    Write-Host "  [FAILED] Some checks failed, please fix and retry" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Please refer to the following documentation:" -ForegroundColor Yellow
    Write-Host "  docs/LOCAL_DATABASE_SETUP.md" -ForegroundColor Gray
    exit 1
}
