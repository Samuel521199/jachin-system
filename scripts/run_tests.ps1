# Run Tests Script
# Jachin-System Test Runner

param(
    [Parameter(Mandatory=$false)]
    [string]$TestType = "all",  # all, unit, integration, e2e, performance
    
    [Parameter(Mandatory=$false)]
    [switch]$Coverage = $false,  # Generate coverage report
    
    [Parameter(Mandatory=$false)]
    [switch]$Detailed = $false   # Detailed output (use -Verbose for PowerShell verbose)
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Jachin-System Test Runner" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check conda environment
Write-Host "[1/5] Checking Conda environment..." -ForegroundColor Yellow
$condaEnv = "jachin-dev"
if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Host "Error: conda command not found" -ForegroundColor Red
    Write-Host "Please install Anaconda or Miniconda first" -ForegroundColor Red
    exit 1
}

# Activate conda environment
Write-Host "Activating Conda environment: $condaEnv" -ForegroundColor Green
& conda activate $condaEnv
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to activate Conda environment '$condaEnv'" -ForegroundColor Red
    Write-Host "Please create environment first: conda create -n $condaEnv python=3.10" -ForegroundColor Red
    exit 1
}

# Check pytest
Write-Host "[2/5] Checking pytest..." -ForegroundColor Yellow
python -m pytest --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pytest..." -ForegroundColor Yellow
    pip install pytest pytest-asyncio pytest-cov
}

# Check Ray
Write-Host "[3/5] Checking Ray..." -ForegroundColor Yellow
python -c "import ray" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: Ray not installed, some tests may not run" -ForegroundColor Yellow
}

# Build test command
Write-Host "[4/5] Building test command..." -ForegroundColor Yellow
$testPath = "tests"
$testArgs = @()

switch ($TestType.ToLower()) {
    "unit" {
        $testPath = "tests/unit"
        Write-Host "Running unit tests..." -ForegroundColor Green
    }
    "integration" {
        $testPath = "tests/integration"
        Write-Host "Running integration tests..." -ForegroundColor Green
    }
    "e2e" {
        $testPath = "tests/e2e"
        Write-Host "Running end-to-end tests..." -ForegroundColor Green
    }
    "performance" {
        $testPath = "tests/performance"
        Write-Host "Running performance tests..." -ForegroundColor Green
    }
    default {
        Write-Host "Running all tests..." -ForegroundColor Green
    }
}

# Always use verbose output for pytest
$testArgs += "-v"

# Add more verbose output if Detailed is specified
if ($Detailed) {
    $testArgs += "-vv"
}

if ($Coverage) {
    $testArgs += "--cov=core"
    $testArgs += "--cov-report=html"
    $testArgs += "--cov-report=term"
    Write-Host "Will generate coverage report..." -ForegroundColor Green
}

# Run tests
Write-Host "[5/5] Running tests..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Command: pytest $testPath $($testArgs -join ' ')" -ForegroundColor Cyan
Write-Host ""

$testCommand = "python -m pytest $testPath $($testArgs -join ' ')"

try {
    Invoke-Expression $testCommand
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "All tests passed!" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        
        if ($Coverage) {
            Write-Host ""
            Write-Host "Coverage report generated: htmlcov/index.html" -ForegroundColor Cyan
            Write-Host "Open in browser to view detailed report" -ForegroundColor Cyan
        }
    } else {
        Write-Host ""
        Write-Host "========================================" -ForegroundColor Red
        Write-Host "Some tests failed" -ForegroundColor Red
        Write-Host "========================================" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} catch {
    Write-Host ""
    Write-Host "Error: Test execution failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
