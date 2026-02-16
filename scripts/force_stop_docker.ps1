# Force stop Docker Desktop quickly
# 快速强制停止 Docker Desktop

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Force Stop Docker Desktop" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[WARN] Running without admin privileges (some operations may fail)" -ForegroundColor Yellow
}

# Step 1: Stop Docker Desktop processes
Write-Host "[1/4] Stopping Docker Desktop processes..." -ForegroundColor Yellow

$dockerProcesses = @(
    "Docker Desktop",
    "com.docker.backend",
    "com.docker.proxy",
    "vpnkit",
    "com.docker.cli"
)

foreach ($processName in $dockerProcesses) {
    $processes = Get-Process -Name $processName -ErrorAction SilentlyContinue
    if ($processes) {
        Write-Host "  Stopping $processName..." -ForegroundColor Cyan
        foreach ($proc in $processes) {
            try {
                Stop-Process -Id $proc.Id -Force -ErrorAction Stop
                Write-Host "    [OK] Stopped PID $($proc.Id)" -ForegroundColor Green
            } catch {
                Write-Host "    [WARN] Failed to stop PID $($proc.Id): $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }
}

Start-Sleep -Seconds 2
Write-Host "  [OK] Docker Desktop processes stopped" -ForegroundColor Green

# Step 2: Shutdown WSL
Write-Host ""
Write-Host "[2/4] Shutting down WSL..." -ForegroundColor Yellow
wsl --shutdown 2>&1 | Out-Null
Start-Sleep -Seconds 2
Write-Host "  [OK] WSL shutdown" -ForegroundColor Green

# Step 3: Kill any remaining Docker processes
Write-Host ""
Write-Host "[3/4] Checking for remaining Docker processes..." -ForegroundColor Yellow

$remainingProcesses = Get-Process | Where-Object {
    $_.ProcessName -like "*docker*" -or
    $_.ProcessName -like "*com.docker*" -or
    $_.MainWindowTitle -like "*Docker*"
} -ErrorAction SilentlyContinue

if ($remainingProcesses) {
    Write-Host "  Found remaining processes:" -ForegroundColor Cyan
    foreach ($proc in $remainingProcesses) {
        Write-Host "    - $($proc.ProcessName) (PID: $($proc.Id))" -ForegroundColor Gray
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            Write-Host "      [OK] Stopped" -ForegroundColor Green
        } catch {
            Write-Host "      [WARN] Failed to stop: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "  [OK] No remaining Docker processes" -ForegroundColor Green
}

# Step 4: Verify
Write-Host ""
Write-Host "[4/4] Verifying shutdown..." -ForegroundColor Yellow

$stillRunning = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
if ($stillRunning) {
    Write-Host "  [WARN] Docker Desktop processes still running:" -ForegroundColor Yellow
    foreach ($proc in $stillRunning) {
        Write-Host "    - PID $($proc.Id)" -ForegroundColor Gray
    }
    Write-Host "  [INFO] You may need to restart your computer" -ForegroundColor Cyan
} else {
    Write-Host "  [OK] Docker Desktop is fully stopped" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Shutdown Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
