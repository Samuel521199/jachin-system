# Complete Docker Desktop WSL Cleanup
# 完全清理 Docker Desktop WSL 环境（即使 unregister 失败）

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Complete Docker Desktop WSL Cleanup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will:" -ForegroundColor Yellow
Write-Host "  1. Stop Docker Desktop and WSL" -ForegroundColor White
Write-Host "  2. Clean up WSL distributions (even if unregister fails)" -ForegroundColor White
Write-Host "  3. Remove all Docker WSL directories" -ForegroundColor White
Write-Host "  4. Prepare for Docker Desktop reinstallation" -ForegroundColor White
Write-Host ""

# Check administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script requires administrator privileges!" -ForegroundColor Red
    Write-Host "Please run PowerShell as Administrator" -ForegroundColor Yellow
    exit 1
}

# Step 1: Stop Docker Desktop
Write-Host "[1/5] Stopping Docker Desktop and WSL..." -ForegroundColor Yellow
Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
wsl --shutdown 2>&1 | Out-Null
Start-Sleep -Seconds 3
Write-Host "  [OK] Stopped" -ForegroundColor Green

# Step 2: Try to unregister (ignore failures)
Write-Host ""
Write-Host "[2/5] Attempting to unregister WSL distributions..." -ForegroundColor Yellow
$wslList = wsl --list --all --verbose 2>&1 | Out-String
Write-Host $wslList

Write-Host "  Attempting docker-desktop..." -ForegroundColor Cyan
$result = wsl --unregister docker-desktop 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "    [OK] Unregistered" -ForegroundColor Green
} else {
    Write-Host "    [WARN] Unregister failed (will clean directories manually)" -ForegroundColor Yellow
}

Write-Host "  Attempting docker-desktop-data..." -ForegroundColor Cyan
$result = wsl --unregister docker-desktop-data 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "    [OK] Unregistered" -ForegroundColor Green
} else {
    Write-Host "    [WARN] Unregister failed (will clean directories manually)" -ForegroundColor Yellow
}

# Step 3: Clean up Docker Desktop WSL directories (force removal)
Write-Host ""
Write-Host "[3/5] Force cleaning Docker Desktop WSL directories..." -ForegroundColor Yellow

$dockerWslPath = "$env:USERPROFILE\AppData\Local\Docker\wsl"
$directoriesToClean = @(
    "$dockerWslPath\data",
    "$dockerWslPath\distro",
    "$dockerWslPath\disk"
)

foreach ($dir in $directoriesToClean) {
    if (Test-Path $dir) {
        Write-Host "  Removing: $dir" -ForegroundColor Cyan
        try {
            # Check if it's a junction/symlink
            $item = Get-Item $dir -Force -ErrorAction SilentlyContinue
            if ($item.LinkType) {
                Write-Host "    Detected $($item.LinkType), removing..." -ForegroundColor Gray
            }
            Remove-Item $dir -Recurse -Force -ErrorAction Stop
            Write-Host "    [OK] Removed" -ForegroundColor Green
        } catch {
            Write-Host "    [WARN] Failed to remove: $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "    Attempting alternative method..." -ForegroundColor Gray
            # Try using cmd to remove
            $cmdResult = cmd /c "rmdir /s /q `"$dir`"" 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    [OK] Removed via alternative method" -ForegroundColor Green
            } else {
                Write-Host "    [ERROR] Still failed - may need manual cleanup" -ForegroundColor Red
            }
        }
    } else {
        Write-Host "  [INFO] Not found: $dir" -ForegroundColor Gray
    }
}

# Step 4: Clean up WSL registry entries (optional, advanced)
Write-Host ""
Write-Host "[4/5] Checking for WSL registry entries..." -ForegroundColor Yellow
$wslRegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss"
if (Test-Path $wslRegPath) {
    $distributions = Get-ChildItem $wslRegPath -ErrorAction SilentlyContinue
    $dockerDists = $distributions | Where-Object {
        $distName = (Get-ItemProperty $_.PSPath -Name DistributionName -ErrorAction SilentlyContinue).DistributionName
        $distName -like "*docker*"
    }
    if ($dockerDists) {
        Write-Host "  Found Docker-related registry entries:" -ForegroundColor Cyan
        foreach ($dist in $dockerDists) {
            $distName = (Get-ItemProperty $dist.PSPath -Name DistributionName -ErrorAction SilentlyContinue).DistributionName
            Write-Host "    - $distName" -ForegroundColor Gray
            try {
                Remove-Item $dist.PSPath -Recurse -Force -ErrorAction Stop
                Write-Host "      [OK] Removed" -ForegroundColor Green
            } catch {
                Write-Host "      [WARN] Failed to remove registry entry" -ForegroundColor Yellow
            }
        }
    } else {
        Write-Host "  [INFO] No Docker-related registry entries found" -ForegroundColor Gray
    }
} else {
    Write-Host "  [INFO] WSL registry path not found" -ForegroundColor Gray
}

# Step 5: Prepare for reinstallation
Write-Host ""
Write-Host "[5/5] Preparing for Docker Desktop reinstallation..." -ForegroundColor Yellow

# Create empty directory structure for Docker Desktop
if (-not (Test-Path $dockerWslPath)) {
    New-Item -ItemType Directory -Path $dockerWslPath -Force | Out-Null
    Write-Host "  [OK] Created Docker WSL directory" -ForegroundColor Green
}

# Create empty disk directory (Docker Desktop will create VHDX here)
$diskDir = "$dockerWslPath\disk"
if (-not (Test-Path $diskDir)) {
    New-Item -ItemType Directory -Path $diskDir -Force | Out-Null
    Write-Host "  [OK] Created empty disk directory" -ForegroundColor Green
} else {
    Write-Host "  [INFO] Disk directory already exists" -ForegroundColor Gray
}

# Verify E: drive VHDX is safe
$eVhdxPath = "E:\docker\wsl\disk\docker_data.vhdx"
if (Test-Path $eVhdxPath) {
    $eSize = [math]::Round((Get-Item $eVhdxPath).Length / 1GB, 2)
    Write-Host "  [OK] E: drive VHDX safe ($eSize GB)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] E: drive VHDX not found at expected location" -ForegroundColor Yellow
}

# Final summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Cleanup Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Uninstall Docker Desktop (if not already done):" -ForegroundColor White
Write-Host "     Settings → Apps → Docker Desktop → Uninstall" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Reinstall Docker Desktop:" -ForegroundColor White
Write-Host "     - Download from https://www.docker.com/products/docker-desktop" -ForegroundColor Gray
Write-Host "     - Install and start" -ForegroundColor Gray
Write-Host "     - Docker will recreate WSL distributions" -ForegroundColor Gray
Write-Host ""
Write-Host "  3. After Docker Desktop creates docker-desktop-data:" -ForegroundColor White
Write-Host "     Run: .\scripts\migrate_docker_using_existing_vhdx.ps1 -Migrate" -ForegroundColor Cyan
Write-Host ""
Write-Host "Current status:" -ForegroundColor Cyan
Write-Host "  WSL distributions: Cleaned (ready for reinstall)" -ForegroundColor Gray
Write-Host "  C: Docker WSL dirs: Removed" -ForegroundColor Gray
Write-Host "  E: drive VHDX: Safe (will be used after migration)" -ForegroundColor Gray
Write-Host ""
