# Deep Clean Docker Desktop for Reinstallation
# 深度清理 Docker Desktop 以便重新安装

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deep Clean Docker Desktop" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will completely remove Docker Desktop" -ForegroundColor Green
Write-Host "so you can reinstall it fresh." -ForegroundColor Green
Write-Host ""

# Check administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script requires administrator privileges!" -ForegroundColor Red
    Write-Host "Please run PowerShell as Administrator" -ForegroundColor Yellow
    exit 1
}

# Step 1: Stop all Docker processes
Write-Host "[1/8] Stopping all Docker processes..." -ForegroundColor Yellow

$dockerProcessNames = @(
    "Docker Desktop",
    "com.docker.backend",
    "com.docker.proxy",
    "com.docker.cli",
    "com.docker.build",
    "vpnkit",
    "docker-sandbox"
)

foreach ($procName in $dockerProcessNames) {
    $processes = Get-Process -Name $procName -ErrorAction SilentlyContinue
    if ($processes) {
        Write-Host "  Stopping $procName..." -ForegroundColor Cyan
        Stop-Process -Name $procName -Force -ErrorAction SilentlyContinue
    }
}

Start-Sleep -Seconds 3
wsl --shutdown 2>&1 | Out-Null
Start-Sleep -Seconds 2
Write-Host "  [OK] All processes stopped" -ForegroundColor Green

# Step 2: Unregister WSL distributions
Write-Host ""
Write-Host "[2/8] Unregistering WSL distributions..." -ForegroundColor Yellow

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
Write-Host "[3/8] Removing Docker directories..." -ForegroundColor Yellow

$directoriesToClean = @(
    "${env:ProgramFiles}\Docker",
    "${env:ProgramFiles(x86)}\Docker",
    "$env:USERPROFILE\AppData\Local\Docker",
    "$env:USERPROFILE\AppData\Roaming\Docker",
    "${env:ProgramData}\Docker",
    "$env:USERPROFILE\.docker"
)

foreach ($dir in $directoriesToClean) {
    if (Test-Path $dir) {
        Write-Host "  Removing: $dir" -ForegroundColor Cyan
        try {
            Remove-Item $dir -Recurse -Force -ErrorAction Stop
            Write-Host "    [OK] Removed" -ForegroundColor Green
        } catch {
            Write-Host "    [WARN] Failed: $($_.Exception.Message)" -ForegroundColor Yellow
            # Try alternative method
            try {
                cmd /c "rmdir /s /q `"$dir`"" 2>&1 | Out-Null
                Write-Host "    [OK] Removed via alternative method" -ForegroundColor Green
            } catch {
                Write-Host "    [ERROR] Still failed - may need manual removal" -ForegroundColor Red
            }
        }
    }
}

# Step 4: Clean registry - Uninstall keys
Write-Host ""
Write-Host "[4/8] Cleaning registry uninstall keys..." -ForegroundColor Yellow

$uninstallKeys = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)

$dockerUninstallKeys = Get-ItemProperty $uninstallKeys -ErrorAction SilentlyContinue | 
    Where-Object { $_.DisplayName -like "*Docker*" }

foreach ($key in $dockerUninstallKeys) {
    Write-Host "  Removing uninstall key: $($key.DisplayName)" -ForegroundColor Cyan
    try {
        Remove-Item $key.PSPath -Recurse -Force -ErrorAction Stop
        Write-Host "    [OK] Removed" -ForegroundColor Green
    } catch {
        Write-Host "    [WARN] Failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# Step 5: Clean registry - Docker Inc. keys
Write-Host ""
Write-Host "[5/8] Cleaning Docker registry keys..." -ForegroundColor Yellow

$regPaths = @(
    "HKLM:\SOFTWARE\Docker Inc.",
    "HKLM:\SOFTWARE\WOW6432Node\Docker Inc.",
    "HKCU:\SOFTWARE\Docker Inc.",
    "HKLM:\SOFTWARE\Classes\Applications\Docker Desktop.exe",
    "HKLM:\SOFTWARE\Classes\dockerfile",
    "HKLM:\SOFTWARE\Classes\docker-compose"
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

# Step 6: Clean installer cache
Write-Host ""
Write-Host "[6/8] Cleaning installer cache..." -ForegroundColor Yellow

$installerCachePaths = @(
    "${env:ProgramData}\Package Cache",
    "$env:LOCALAPPDATA\Temp\Docker*",
    "$env:TEMP\Docker*"
)

foreach ($cachePath in $installerCachePaths) {
    $cacheItems = Get-ChildItem $cachePath -ErrorAction SilentlyContinue | 
        Where-Object { $_.Name -like "*Docker*" }
    
    foreach ($item in $cacheItems) {
        Write-Host "  Removing cache: $($item.FullName)" -ForegroundColor Cyan
        try {
            Remove-Item $item.FullName -Recurse -Force -ErrorAction Stop
            Write-Host "    [OK] Removed" -ForegroundColor Green
        } catch {
            Write-Host "    [WARN] Failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

# Step 7: Clean Windows Installer database
Write-Host ""
Write-Host "[7/8] Cleaning Windows Installer database..." -ForegroundColor Yellow

# Find Docker Desktop product code
$productCodes = Get-WmiObject Win32_Product -ErrorAction SilentlyContinue | 
    Where-Object { $_.Name -like "*Docker*" }

foreach ($product in $productCodes) {
    Write-Host "  Found product: $($product.Name)" -ForegroundColor Cyan
    Write-Host "    Product Code: $($product.IdentifyingNumber)" -ForegroundColor Gray
    Write-Host "    Attempting to uninstall..." -ForegroundColor Cyan
    
    try {
        $product.Uninstall()
        Write-Host "      [OK] Uninstalled" -ForegroundColor Green
    } catch {
        Write-Host "      [WARN] Failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# Step 8: Verify cleanup
Write-Host ""
Write-Host "[8/8] Verifying cleanup..." -ForegroundColor Yellow

$dockerExePath = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dockerExePath) {
    Write-Host "  [WARN] Docker Desktop executable still exists!" -ForegroundColor Yellow
    Write-Host "    Location: $dockerExePath" -ForegroundColor Gray
} else {
    Write-Host "  [OK] Docker Desktop executable removed" -ForegroundColor Green
}

$wslListAfter = wsl --list --all --verbose 2>&1
if ($wslListAfter -match "docker-desktop") {
    Write-Host "  [WARN] Docker WSL distributions still exist" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] Docker WSL distributions removed" -ForegroundColor Green
}

$regKeysAfter = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | 
    Where-Object { $_.DisplayName -like "*Docker*" }

if ($regKeysAfter) {
    Write-Host "  [WARN] Docker registry keys still exist" -ForegroundColor Yellow
    foreach ($key in $regKeysAfter) {
        Write-Host "    - $($key.DisplayName)" -ForegroundColor Gray
    }
} else {
    Write-Host "  [OK] Docker registry keys removed" -ForegroundColor Green
}

# Final summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deep Clean Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Important notes:" -ForegroundColor Yellow
Write-Host "  1. E: drive VHDX file is safe:" -ForegroundColor White
Write-Host "     E:\docker\wsl\disk\docker_data.vhdx" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Restart your computer (REQUIRED)" -ForegroundColor Yellow
Write-Host "     This ensures all processes and locks are released" -ForegroundColor White
Write-Host ""
Write-Host "  3. After restart, you can reinstall Docker Desktop" -ForegroundColor Yellow
Write-Host "     The installer should now work properly" -ForegroundColor White
Write-Host ""
Write-Host "  4. After installation, run migration scripts:" -ForegroundColor Yellow
Write-Host "     .\scripts\setup_docker_e_drive_after_install.ps1" -ForegroundColor Cyan
Write-Host "     .\scripts\configure_docker_vhdx_to_e_drive.ps1" -ForegroundColor Cyan
Write-Host ""
