# Check and Clean Docker Desktop Installation
# 检查并清理 Docker Desktop 安装

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Check and Clean Docker Desktop" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script requires administrator privileges!" -ForegroundColor Red
    Write-Host "Please run PowerShell as Administrator" -ForegroundColor Yellow
    exit 1
}

# Paths
$dockerProgramFiles = "${env:ProgramFiles}\Docker"
$dockerProgramFilesX86 = "${env:ProgramFiles(x86)}\Docker"
$dockerAppData = "$env:USERPROFILE\AppData\Local\Docker"
$dockerRoaming = "$env:USERPROFILE\AppData\Roaming\Docker"
$dockerProgramData = "${env:ProgramData}\Docker"

# Step 1: Check if Docker Desktop is installed
Write-Host "[1/6] Checking Docker Desktop installation..." -ForegroundColor Yellow

$dockerInstalled = $false
$dockerExePath = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"

if (Test-Path $dockerExePath) {
    Write-Host "  [FOUND] Docker Desktop is installed" -ForegroundColor Green
    Write-Host "    Location: $dockerExePath" -ForegroundColor Gray
    
    # Get version info
    if (Test-Path $dockerExePath) {
        $versionInfo = (Get-Item $dockerExePath).VersionInfo
        Write-Host "    Version: $($versionInfo.FileVersion)" -ForegroundColor Gray
    }
    
    $dockerInstalled = $true
} else {
    Write-Host "  [INFO] Docker Desktop executable not found" -ForegroundColor Yellow
    Write-Host "    Expected: $dockerExePath" -ForegroundColor Gray
}

# Check Program Files (x86)
if (Test-Path "${env:ProgramFiles(x86)}\Docker") {
    Write-Host "  [FOUND] Docker files in Program Files (x86)" -ForegroundColor Yellow
    Write-Host "    Location: ${env:ProgramFiles(x86)}\Docker" -ForegroundColor Gray
    $dockerInstalled = $true
}

# Step 2: Check if Docker Desktop is running
Write-Host ""
Write-Host "[2/6] Checking if Docker Desktop is running..." -ForegroundColor Yellow

$dockerProcesses = Get-Process "Docker Desktop" -ErrorAction SilentlyContinue
if ($dockerProcesses) {
    Write-Host "  [WARN] Docker Desktop is running!" -ForegroundColor Yellow
    Write-Host "    Found $($dockerProcesses.Count) process(es)" -ForegroundColor Gray
    
    $stopChoice = Read-Host "    Do you want to stop Docker Desktop? (Y/N)"
    if ($stopChoice -eq "Y" -or $stopChoice -eq "y") {
        Write-Host "    Stopping Docker Desktop..." -ForegroundColor Cyan
        Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
        Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        wsl --shutdown 2>&1 | Out-Null
        Write-Host "      [OK] Stopped" -ForegroundColor Green
    } else {
        Write-Host "    [SKIP] Not stopping Docker Desktop" -ForegroundColor Yellow
        Write-Host "    [WARN] Cannot uninstall while Docker Desktop is running" -ForegroundColor Red
    }
} else {
    Write-Host "  [OK] Docker Desktop is not running" -ForegroundColor Green
}

# Step 3: Check WSL distributions
Write-Host ""
Write-Host "[3/6] Checking WSL distributions..." -ForegroundColor Yellow

$wslList = wsl --list --all --verbose 2>&1 | Out-String
Write-Host $wslList

$hasDockerDesktop = $wslList -match "docker-desktop"
$hasDockerDesktopData = $wslList -match "docker-desktop-data"

if ($hasDockerDesktop -or $hasDockerDesktopData) {
    Write-Host "  [FOUND] Docker WSL distributions exist" -ForegroundColor Yellow
    if ($hasDockerDesktop) { Write-Host "    - docker-desktop" -ForegroundColor Gray }
    if ($hasDockerDesktopData) { Write-Host "    - docker-desktop-data" -ForegroundColor Gray }
} else {
    Write-Host "  [OK] No Docker WSL distributions found" -ForegroundColor Green
}

# Step 4: Uninstall Docker Desktop (if installed)
Write-Host ""
Write-Host "[4/6] Uninstalling Docker Desktop..." -ForegroundColor Yellow

if ($dockerInstalled) {
    Write-Host "  Docker Desktop is installed, attempting to uninstall..." -ForegroundColor Cyan
    
    # Try to find uninstaller
    $uninstallerPaths = @(
        "${env:ProgramFiles}\Docker\Docker\uninstall.exe",
        "${env:ProgramFiles}\Docker\Docker\Docker Desktop Installer.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\uninstall.exe"
    )
    
    $uninstallerFound = $false
    foreach ($uninstaller in $uninstallerPaths) {
        if (Test-Path $uninstaller) {
            Write-Host "    Found uninstaller: $uninstaller" -ForegroundColor Gray
            $uninstallerFound = $true
            
            $uninstallChoice = Read-Host "    Do you want to run uninstaller? (Y/N)"
            if ($uninstallChoice -eq "Y" -or $uninstallChoice -eq "y") {
                Write-Host "    Running uninstaller..." -ForegroundColor Cyan
                Start-Process $uninstaller -Wait
                Write-Host "      [OK] Uninstaller completed" -ForegroundColor Green
            } else {
                Write-Host "      [SKIP] Uninstaller not run" -ForegroundColor Yellow
            }
            break
        }
    }
    
    if (-not $uninstallerFound) {
        Write-Host "    [WARN] Uninstaller not found" -ForegroundColor Yellow
        Write-Host "    [INFO] You may need to uninstall manually:" -ForegroundColor Cyan
        Write-Host "      Settings → Apps → Docker Desktop → Uninstall" -ForegroundColor Gray
    }
} else {
    Write-Host "  [INFO] Docker Desktop is not installed" -ForegroundColor Gray
}

# Step 5: Clean up WSL distributions
Write-Host ""
Write-Host "[5/6] Cleaning up WSL distributions..." -ForegroundColor Yellow

if ($hasDockerDesktop -or $hasDockerDesktopData) {
    $cleanWslChoice = Read-Host "  Do you want to unregister Docker WSL distributions? (Y/N)"
    if ($cleanWslChoice -eq "Y" -or $cleanWslChoice -eq "y") {
        wsl --shutdown 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        
        if ($hasDockerDesktop) {
            Write-Host "    Unregistering docker-desktop..." -ForegroundColor Cyan
            wsl --unregister docker-desktop 2>&1 | Out-Null
            Write-Host "      [OK] Unregistered" -ForegroundColor Green
        }
        
        if ($hasDockerDesktopData) {
            Write-Host "    Unregistering docker-desktop-data..." -ForegroundColor Cyan
            wsl --unregister docker-desktop-data 2>&1 | Out-Null
            Write-Host "      [OK] Unregistered" -ForegroundColor Green
        }
    } else {
        Write-Host "    [SKIP] WSL distributions not cleaned" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] No WSL distributions to clean" -ForegroundColor Green
}

# Step 6: Clean up directories and files
Write-Host ""
Write-Host "[6/6] Cleaning up directories and files..." -ForegroundColor Yellow

$directoriesToClean = @(
    $dockerProgramFiles,
    $dockerProgramFilesX86,
    $dockerAppData,
    $dockerRoaming,
    $dockerProgramData
)

$cleanDirsChoice = Read-Host "  Do you want to remove Docker directories? (Y/N)"
if ($cleanDirsChoice -eq "Y" -or $cleanDirsChoice -eq "y") {
    foreach ($dir in $directoriesToClean) {
        if (Test-Path $dir) {
            Write-Host "    Removing: $dir" -ForegroundColor Cyan
            try {
                Remove-Item $dir -Recurse -Force -ErrorAction Stop
                Write-Host "      [OK] Removed" -ForegroundColor Green
            } catch {
                Write-Host "      [WARN] Failed to remove: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }
} else {
    Write-Host "    [SKIP] Directories not removed" -ForegroundColor Yellow
    Write-Host "    [INFO] Directories that would be removed:" -ForegroundColor Cyan
    foreach ($dir in $directoriesToClean) {
        if (Test-Path $dir) {
            Write-Host "      - $dir" -ForegroundColor Gray
        }
    }
}

# Clean up registry (optional)
Write-Host ""
Write-Host "Cleaning up registry entries..." -ForegroundColor Yellow

$cleanRegChoice = Read-Host "  Do you want to clean Docker registry entries? (Y/N)"
if ($cleanRegChoice -eq "Y" -or $cleanRegChoice -eq "y") {
    $regPaths = @(
        "HKLM:\SOFTWARE\Docker Inc.",
        "HKLM:\SOFTWARE\WOW6432Node\Docker Inc.",
        "HKCU:\SOFTWARE\Docker Inc."
    )
    
    foreach ($regPath in $regPaths) {
        if (Test-Path $regPath) {
            Write-Host "    Removing: $regPath" -ForegroundColor Cyan
            try {
                Remove-Item $regPath -Recurse -Force -ErrorAction Stop
                Write-Host "      [OK] Removed" -ForegroundColor Green
            } catch {
                Write-Host "      [WARN] Failed to remove: $($_.Exception.Message)" -ForegroundColor Yellow
            }
        }
    }
} else {
    Write-Host "    [SKIP] Registry entries not cleaned" -ForegroundColor Yellow
}

# Final summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Cleanup Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Verify cleanup
Write-Host "Verification:" -ForegroundColor Cyan

if (Test-Path $dockerExePath) {
    Write-Host "  [WARN] Docker Desktop still exists at: $dockerExePath" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] Docker Desktop executable removed" -ForegroundColor Green
}

$wslListAfter = wsl --list --all --verbose 2>&1 | Out-String
$hasDockerAfter = $wslListAfter -match "docker-desktop"

if ($hasDockerAfter) {
    Write-Host "  [WARN] Docker WSL distributions still exist" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] Docker WSL distributions removed" -ForegroundColor Green
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Restart your computer (recommended)" -ForegroundColor White
Write-Host "  2. Reinstall Docker Desktop if needed" -ForegroundColor White
Write-Host "  3. Run migration scripts after installation" -ForegroundColor White
Write-Host ""
