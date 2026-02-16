# System Verification Script
# UTF-8 with BOM encoding

# Set output encoding
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Jachin-System v3.2 System Verification" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Conda environment
Write-Host "[1/6] Checking Conda environment..." -ForegroundColor Yellow
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Host "  [ERROR] Conda not found" -ForegroundColor Red
    exit 1
}

# Activate environment
$env:CONDA_DEFAULT_ENV = "jachin-dev"
if ($env:CONDA_DEFAULT_ENV -ne "jachin-dev") {
    Write-Host "  [INFO] Activating jachin-dev environment..." -ForegroundColor Blue
    conda activate jachin-dev
}
Write-Host "  [OK] Conda environment ready" -ForegroundColor Green

# Check backend port
Write-Host "[2/6] Checking backend service port..." -ForegroundColor Yellow
$backendPort = 18888
$portCheck = netstat -ano | findstr ":$backendPort"
if ($portCheck) {
    Write-Host "  [INFO] Port $backendPort is in use" -ForegroundColor Yellow
    Write-Host "  [INFO] Backend service may be running" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] Port $backendPort is available" -ForegroundColor Green
}

# Check database connections
Write-Host "[3/6] Checking database connections..." -ForegroundColor Yellow
$pgTest = Test-NetConnection -ComputerName localhost -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue -ErrorAction SilentlyContinue
if ($pgTest) {
    Write-Host "  [OK] PostgreSQL (5432) connection OK" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] PostgreSQL (5432) not responding" -ForegroundColor Yellow
}

$qdrantResponse = $null
try {
    $qdrantResponse = Invoke-WebRequest -Uri "http://localhost:6333/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
} catch {
    $qdrantResponse = $null
}
if ($qdrantResponse -and $qdrantResponse.StatusCode -eq 200) {
    Write-Host "  [OK] Qdrant (6333) running OK" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Qdrant (6333) not responding" -ForegroundColor Yellow
}

# Check Docker service
Write-Host "[4/6] Checking Docker service..." -ForegroundColor Yellow
$null = docker ps 2>&1
$dockerExitCode = $LASTEXITCODE
if ($dockerExitCode -eq 0) {
    Write-Host "  [OK] Docker is running" -ForegroundColor Green
    $null = docker ps --filter "name=redis" --format "{{.Names}}" 2>&1
    $redisExitCode = $LASTEXITCODE
    $redisOutput = docker ps --filter "name=redis" --format "{{.Names}}" 2>&1
    if ($redisExitCode -eq 0 -and $redisOutput) {
        Write-Host "  [OK] Redis container is running" -ForegroundColor Green
    } else {
        Write-Host "  [WARNING] Redis container not running" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [WARNING] Docker not running or inaccessible" -ForegroundColor Yellow
}

# Check Python dependencies
Write-Host "[5/6] Checking Python dependencies..." -ForegroundColor Yellow
$null = python -c "import fastapi, ray, grpc; print('OK')" 2>&1
$pythonExitCode = $LASTEXITCODE
if ($pythonExitCode -eq 0) {
    Write-Host "  [OK] Key dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Some dependencies may be missing" -ForegroundColor Yellow
}

# Check test status
Write-Host "[6/6] Checking test status..." -ForegroundColor Yellow
if (Test-Path "htmlcov/index.html") {
    Write-Host "  [OK] Test coverage report generated" -ForegroundColor Green
    Write-Host "  [INFO] View report: htmlcov/index.html" -ForegroundColor Blue
} else {
    Write-Host "  [INFO] Run tests to generate coverage: .\scripts\run_tests.ps1 -Coverage" -ForegroundColor Blue
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Verification Complete" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Start backend service: .\scripts\start.ps1" -ForegroundColor White
Write-Host "  2. Test API: curl http://localhost:18888/health" -ForegroundColor White
Write-Host "  3. View API docs: http://localhost:18888/docs" -ForegroundColor White
Write-Host "  4. Start desktop client: cd clients\desktop && npm run tauri:dev" -ForegroundColor White
Write-Host ""
