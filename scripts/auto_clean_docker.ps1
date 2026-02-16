# Auto Clean Docker Desktop (No prompts)
# 自动清理 Docker Desktop（无需确认）

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Auto Clean Docker Desktop" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will automatically clean Docker Desktop" -ForegroundColor Green
Write-Host ""

# Check administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script requires administrator privileges!" -ForegroundColor Red
    Write-Host "Please run PowerShell as Administrator" -ForegroundColor Yellow
    exit 1
}

# Step 1: Stop Docker Desktop
Write-Host "[1/5] Stopping Docker Desktop..." -ForegroundColor Yellow

$dockerProcesses = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if ($dockerProcesses) {
    Write-Host "  Stopping Docker Desktop processes..." -ForegroundColor Cyan
    Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
    Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue
    Stop-Process -Name "com.docker.proxy" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    Write-Host "    [OK] Stopped" -ForegroundColor Green
} else {
    Write-Host "  [OK] Docker Desktop is not running" -ForegroundColor Green
}

wsl --shutdown 2>&1 | Out-Null
Start-Sleep -Seconds 2

# Step 2: Unregister WSL distributions
Write-Host ""
Write-Host "[2/5] Unregistering WSL distributions..." -ForegroundColor Yellow

$wslList = wsl --list --all --verbose 2>&1 | Out-String

if ($wslList -match "docker-desktop") {
    Write-Host "  Unregistering docker-desktop..." -ForegroundColor Cyan
    wsl --unregister docker-desktop 2>&1 | Out-Null
    Write-Host "    [OK] Unregistered" -ForegroundColor Green
}

if ($wslList -match "docker-desktop-data") {
    Write-Host "  Unregistering docker-desktop-data..." -ForegroundColor Cyan
    wsl --unregister docker-desktop-data 2>&1 | Out-Null
    Write-Host "    [OK] Unregistered" -ForegroundColor Green
}

# Step 3: Remove directories
Write-Host ""
Write-Host "[3/5] Removing Docker directories..." -ForegroundColor Yellow

$directoriesToClean = @(
    "${env:ProgramFiles}\Docker",
    "${env:ProgramFiles(x86)}\Docker",
    "$env:USERPROFILE\AppData\Local\Docker",
    "$env:USERPROFILE\AppData\Roaming\Docker",
    "${env:ProgramData}\Docker"
)

foreach ($dir in $directoriesToClean) {
    if (Test-Path $dir) {
        Write-Host "  Removing: $dir" -ForegroundColor Cyan
        try {
            Remove-Item $dir -Recurse -Force -ErrorAction Stop
            Write-Host "    [OK] Removed" -ForegroundColor Green
        } catch {
            Write-Host "    [WARN] Failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

# Step 4: Clean registry
Write-Host ""
Write-Host "[4/5] Cleaning registry entries..." -ForegroundColor Yellow

$regPaths = @(
    "HKLM:\SOFTWARE\Docker Inc.",
    "HKLM:\SOFTWARE\WOW6432Node\Docker Inc.",
    "HKCU:\SOFTWARE\Docker Inc."
)

foreach ($regPath in $regPaths) {
    if (Test-Path $regPath) {
        Write-Host "  Removing: $regPath" -ForegroundColor Cyan
        try {
            Remove-Item $regPath -Recurse -Force -ErrorAction Stop
            Write-Host "    [OK] Removed" -ForegroundColor Green
        } catch {
            Write-Host "    [WARN] Failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

# Step 5: Verify
Write-Host ""
Write-Host "[5/5] Verifying cleanup..." -ForegroundColor Yellow

$dockerExePath = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerExePath) {
    Write-Host "  [WARN] Docker Desktop still exists" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] Docker Desktop removed" -ForegroundColor Green
}

$wslListAfter = wsl --list --all --verbose 2>&1
if ($wslListAfter -match "docker-desktop") {
    Write-Host "  [WARN] Docker WSL distributions still exist" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] Docker WSL distributions removed" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Cleanup Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Note: E: drive VHDX file is safe:" -ForegroundColor Cyan
Write-Host "  E:\docker\wsl\disk\docker_data.vhdx" -ForegroundColor Gray
Write-Host ""
