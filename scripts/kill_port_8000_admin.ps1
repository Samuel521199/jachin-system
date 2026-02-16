# Kill process using port 8000 (requires Administrator privileges)
# Run this script as Administrator if normal kill fails

param(
    [switch]$Force
)

# Check if running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "This script requires Administrator privileges." -ForegroundColor Yellow
    Write-Host "Please right-click PowerShell and select 'Run as Administrator', then run:" -ForegroundColor Yellow
    Write-Host "  .\scripts\kill_port_8000_admin.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Or manually kill the process:" -ForegroundColor Yellow
    Write-Host "  taskkill /F /PID <PID>" -ForegroundColor Cyan
    exit 1
}

Write-Host "Finding processes using port 8000..." -ForegroundColor Yellow

$connections = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"

if ($connections) {
    $processIds = $connections | ForEach-Object {
        $_.ToString().Split()[-1]
    } | Select-Object -Unique

    foreach ($processId in $processIds) {
        try {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($process) {
                Write-Host "Found process: $($process.ProcessName) (PID: $processId)" -ForegroundColor Cyan
                Write-Host "Command line: $($process.CommandLine)" -ForegroundColor Gray -ErrorAction SilentlyContinue
                Write-Host "Killing process $processId..." -ForegroundColor Yellow
                
                Stop-Process -Id $processId -Force -ErrorAction Stop
                Write-Host "Process $processId killed successfully" -ForegroundColor Green
                Start-Sleep -Seconds 1
            }
        } catch {
            Write-Host "Failed to kill process $processId : $_" -ForegroundColor Red
            Write-Host "Trying taskkill..." -ForegroundColor Yellow
            $result = & taskkill /F /PID $processId 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Process $processId killed successfully using taskkill" -ForegroundColor Green
            } else {
                Write-Host "Failed: $result" -ForegroundColor Red
            }
        }
    }
    
    # Verify port is free
    Start-Sleep -Seconds 2
    $stillInUse = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"
    if ($stillInUse) {
        Write-Host "Warning: Port 8000 may still be in use" -ForegroundColor Yellow
    } else {
        Write-Host "Port 8000 is now free" -ForegroundColor Green
    }
} else {
    Write-Host "No process found using port 8000" -ForegroundColor Yellow
}

Write-Host "Done" -ForegroundColor Green
