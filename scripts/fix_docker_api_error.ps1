# Fix Docker API 500 Error
# 修复 Docker API 500 错误

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fix Docker API 500 Error" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Error: 500 Internal Server Error for API route" -ForegroundColor Red
Write-Host "This usually means Docker Engine is not running properly" -ForegroundColor Yellow
Write-Host ""

# Check administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[WARN] Running without admin privileges (some operations may fail)" -ForegroundColor Yellow
}

# Step 1: Check Docker Desktop status
Write-Host "[1/6] Checking Docker Desktop status..." -ForegroundColor Yellow

$dockerProcesses = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if ($dockerProcesses) {
    Write-Host "  [INFO] Docker Desktop processes running: $($dockerProcesses.Count)" -ForegroundColor Cyan
    foreach ($proc in $dockerProcesses) {
        Write-Host "    - PID $($proc.Id) (Started: $($proc.StartTime))" -ForegroundColor Gray
    }
} else {
    Write-Host "  [WARN] Docker Desktop is not running" -ForegroundColor Yellow
}

# Step 2: Check Docker API
Write-Host ""
Write-Host "[2/6] Testing Docker API..." -ForegroundColor Yellow

try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>&1
    if ($LASTEXITCODE -eq 0 -and $dockerVersion -notmatch "500") {
        Write-Host "  [OK] Docker API is working" -ForegroundColor Green
        Write-Host "    Server version: $dockerVersion" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Docker API error detected" -ForegroundColor Red
        Write-Host "    Error: $dockerVersion" -ForegroundColor Gray
    }
} catch {
    Write-Host "  [ERROR] Cannot connect to Docker API" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)" -ForegroundColor Gray
}

# Step 3: Check WSL distributions
Write-Host ""
Write-Host "[3/6] Checking WSL distributions..." -ForegroundColor Yellow

$wslList = wsl --list --all --verbose 2>&1 | Out-String
Write-Host $wslList

$hasDockerDesktop = $wslList -match "docker-desktop"
$hasDockerDesktopData = $wslList -match "docker-desktop-data"

if (-not $hasDockerDesktop) {
    Write-Host "  [ERROR] docker-desktop WSL distribution not found!" -ForegroundColor Red
} else {
    Write-Host "  [OK] docker-desktop found" -ForegroundColor Green
}

if (-not $hasDockerDesktopData) {
    Write-Host "  [WARN] docker-desktop-data WSL distribution not found!" -ForegroundColor Yellow
    Write-Host "    This may be the cause of the API error" -ForegroundColor Gray
} else {
    Write-Host "  [OK] docker-desktop-data found" -ForegroundColor Green
}

# Step 4: Restart Docker Desktop
Write-Host ""
Write-Host "[4/6] Restarting Docker Desktop..." -ForegroundColor Yellow

Write-Host "  Stopping Docker Desktop..." -ForegroundColor Cyan
$dockerProcesses = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if ($dockerProcesses) {
    foreach ($proc in $dockerProcesses) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    Write-Host "    [OK] Stopped" -ForegroundColor Green
} else {
    Write-Host "    [INFO] Already stopped" -ForegroundColor Gray
}

Write-Host "  Shutting down WSL..." -ForegroundColor Cyan
wsl --shutdown 2>&1 | Out-Null
Start-Sleep -Seconds 2
Write-Host "    [OK] WSL shutdown" -ForegroundColor Green

Write-Host "  Waiting 5 seconds before restart..." -ForegroundColor Cyan
Start-Sleep -Seconds 5

Write-Host "  Starting Docker Desktop..." -ForegroundColor Cyan
try {
    $dockerDesktopPath = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktopPath) {
        Start-Process $dockerDesktopPath
        Write-Host "    [OK] Started" -ForegroundColor Green
    } else {
        Write-Host "    [ERROR] Docker Desktop executable not found" -ForegroundColor Red
        Write-Host "      Expected: $dockerDesktopPath" -ForegroundColor Gray
        Write-Host "    [INFO] Please start Docker Desktop manually" -ForegroundColor Yellow
    }
} catch {
    Write-Host "    [ERROR] Failed to start Docker Desktop" -ForegroundColor Red
    Write-Host "      Error: $($_.Exception.Message)" -ForegroundColor Gray
    Write-Host "    [INFO] Please start Docker Desktop manually" -ForegroundColor Yellow
}

# Step 5: Wait and verify
Write-Host ""
Write-Host "[5/6] Waiting for Docker Engine to start..." -ForegroundColor Yellow
Write-Host "  This may take 30-60 seconds..." -ForegroundColor Gray

$maxWait = 60
$waited = 0
$dockerReady = $false

while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 5
    $waited += 5
    
    try {
        $testResult = docker ps 2>&1
        if ($LASTEXITCODE -eq 0 -and $testResult -notmatch "500") {
            $dockerReady = $true
            break
        }
    } catch {
        # Continue waiting
    }
    
    Write-Host "    Waiting... ($waited/$maxWait seconds)" -ForegroundColor Gray
}

if ($dockerReady) {
    Write-Host "  [OK] Docker Engine is ready!" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Docker Engine may still be starting" -ForegroundColor Yellow
    Write-Host "    Please wait a bit longer and check manually" -ForegroundColor Gray
}

# Step 6: Final verification
Write-Host ""
Write-Host "[6/6] Final verification..." -ForegroundColor Yellow

try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>&1
    if ($LASTEXITCODE -eq 0 -and $dockerVersion -notmatch "500") {
        Write-Host "  [OK] Docker API is working!" -ForegroundColor Green
        Write-Host "    Server version: $dockerVersion" -ForegroundColor Gray
        
        $containerCount = (docker ps -q 2>&1 | Measure-Object).Count
        Write-Host "    Running containers: $containerCount" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Docker API still has issues" -ForegroundColor Red
        Write-Host "    Error: $dockerVersion" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  [INFO] Try the following:" -ForegroundColor Yellow
        Write-Host "    1. Wait a few more minutes for Docker to fully start" -ForegroundColor White
        Write-Host "    2. Check Docker Desktop UI for error messages" -ForegroundColor White
        Write-Host "    3. Restart your computer if problem persists" -ForegroundColor White
    }
} catch {
    Write-Host "  [ERROR] Cannot verify Docker API" -ForegroundColor Red
    Write-Host "    Error: $($_.Exception.Message)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Fix Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

if (-not $hasDockerDesktopData) {
    Write-Host "⚠️  IMPORTANT: docker-desktop-data is missing!" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "This is likely the root cause. Solution:" -ForegroundColor White
    Write-Host "  1. Uninstall Docker Desktop completely" -ForegroundColor Cyan
    Write-Host "  2. Reinstall Docker Desktop" -ForegroundColor Cyan
    Write-Host "  3. On first start, go to Settings → Resources → Advanced" -ForegroundColor Cyan
    Write-Host "  4. Set 'Disk image location' to E:\DockerDesktopWSL" -ForegroundColor Cyan
    Write-Host "  5. Click 'Apply & restart'" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "See: docs\DOCKER_REINSTALL_SOLUTION.md for details" -ForegroundColor Gray
    Write-Host ""
}
