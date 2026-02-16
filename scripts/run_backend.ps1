# Wrapper script for running backend with conda environment
# This script is called by dapr run

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Set PYTHONPATH
$env:PYTHONPATH = "$ProjectRoot;$ProjectRoot\core"

# Get conda environment Python executable
# Try to find conda environment path
$condaEnvPath = $null
if (Test-Path "$env:CONDA_PREFIX\python.exe") {
    $condaEnvPath = $env:CONDA_PREFIX
} else {
    # Try common conda locations
    $possiblePaths = @(
        "$env:USERPROFILE\.conda\envs\jachin-dev",
        "$env:LOCALAPPDATA\conda\conda\envs\jachin-dev",
        "C:\Users\$env:USERNAME\.conda\envs\jachin-dev"
    )
    foreach ($path in $possiblePaths) {
        if (Test-Path "$path\python.exe") {
            $condaEnvPath = $path
            break
        }
    }
}

if ($condaEnvPath) {
    $pythonExe = Join-Path $condaEnvPath "python.exe"
    Write-Host "[INFO] Using Python: $pythonExe" -ForegroundColor Gray
    
    # Run uvicorn (use port from environment variable or default to 18888)
    $appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { 18888 }
    & $pythonExe -m uvicorn core.main:app --host 0.0.0.0 --port $appPort
} else {
    # Fallback to conda run
    Write-Host "[WARN] Could not find conda Python, trying conda run..." -ForegroundColor Yellow
    $appPort = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { 18888 }
    conda run -n jachin-dev python -m uvicorn core.main:app --host 0.0.0.0 --port $appPort
}
