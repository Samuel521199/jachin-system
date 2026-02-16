# Check Docker Desktop Storage Configuration
# 检查 Docker Desktop 存储配置

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Check Docker Desktop Storage" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Paths
$cDataPath = "$env:USERPROFILE\AppData\Local\Docker\wsl\data"
$cDiskDir = "C:\Users\$env:USERNAME\AppData\Local\Docker\wsl\disk"
$eVhdxFile = "E:\docker\wsl\disk\docker_data.vhdx"

# Step 1: Check WSL distributions
Write-Host "[1/4] Checking WSL distributions..." -ForegroundColor Yellow
$wslList = wsl --list --all --verbose 2>&1 | Out-String
Write-Host $wslList

$hasDockerDesktop = $wslList -match "docker-desktop"
$hasDockerDesktopData = $wslList -match "docker-desktop-data"

if ($hasDockerDesktop) {
    Write-Host "  [OK] docker-desktop exists" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] docker-desktop not found!" -ForegroundColor Red
}

if ($hasDockerDesktopData) {
    Write-Host "  [INFO] docker-desktop-data exists (legacy mode)" -ForegroundColor Cyan
} else {
    Write-Host "  [INFO] docker-desktop-data not found (using VHDX mode)" -ForegroundColor Cyan
    Write-Host "    This is normal for Docker Desktop 4.30+" -ForegroundColor Gray
}

# Step 2: Check data directory
Write-Host ""
Write-Host "[2/4] Checking data directory..." -ForegroundColor Yellow

if (Test-Path $cDataPath) {
    $dataItems = Get-ChildItem $cDataPath -ErrorAction SilentlyContinue
    Write-Host "  [INFO] data directory exists: $($dataItems.Count) items" -ForegroundColor Cyan
    
    if ($dataItems.Count -gt 0) {
        Write-Host "    Contents:" -ForegroundColor Gray
        foreach ($item in $dataItems | Select-Object -First 5) {
            Write-Host "      - $($item.Name)" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "  [INFO] data directory does not exist" -ForegroundColor Yellow
    Write-Host "    This is normal if using VHDX mode" -ForegroundColor Gray
}

# Step 3: Check disk directory and VHDX files
Write-Host ""
Write-Host "[3/4] Checking disk directory and VHDX files..." -ForegroundColor Yellow

if (Test-Path $cDiskDir) {
    $diskItem = Get-Item $cDiskDir -Force -ErrorAction SilentlyContinue
    
    if ($diskItem.LinkType -eq "Junction") {
        Write-Host "  [INFO] C: disk directory is a Junction" -ForegroundColor Cyan
        Write-Host "    Target: $($diskItem.Target)" -ForegroundColor Gray
    } else {
        Write-Host "  [INFO] C: disk directory exists (not a Junction)" -ForegroundColor Cyan
        
        $vhdxFiles = Get-ChildItem "$cDiskDir\*.vhdx" -ErrorAction SilentlyContinue
        if ($vhdxFiles) {
            Write-Host "    VHDX files:" -ForegroundColor Gray
            foreach ($vhdx in $vhdxFiles) {
                $size = [math]::Round($vhdx.Length / 1GB, 2)
                Write-Host "      - $($vhdx.Name): $size GB" -ForegroundColor Gray
            }
        } else {
            Write-Host "    No VHDX files found" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "  [WARN] C: disk directory does not exist" -ForegroundColor Yellow
}

# Check E: drive VHDX
if (Test-Path $eVhdxFile) {
    $eSize = [math]::Round((Get-Item $eVhdxFile).Length / 1GB, 2)
    Write-Host "  [OK] E: drive VHDX exists: $eSize GB" -ForegroundColor Green
} else {
    Write-Host "  [INFO] E: drive VHDX not found" -ForegroundColor Yellow
}

# Step 4: Summary and recommendations
Write-Host ""
Write-Host "[4/4] Summary and recommendations..." -ForegroundColor Yellow
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Storage Mode" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

if ($hasDockerDesktopData) {
    Write-Host "Mode: Legacy (using docker-desktop-data WSL distribution)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Recommendation:" -ForegroundColor Yellow
    Write-Host "  Run migration script to move to E: drive:" -ForegroundColor White
    Write-Host "    .\scripts\setup_docker_e_drive_after_install.ps1" -ForegroundColor Cyan
} else {
    Write-Host "Mode: Modern (using VHDX file)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Recommendation:" -ForegroundColor Yellow
    Write-Host "  Configure Junction to use E: drive VHDX:" -ForegroundColor White
    Write-Host "    .\scripts\configure_docker_vhdx_to_e_drive.ps1" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  This will:" -ForegroundColor Gray
    Write-Host "    - Create Junction: C:\...\disk -> E:\docker\wsl\disk" -ForegroundColor Gray
    Write-Host "    - Docker Desktop will use E: drive VHDX (45.59 GB)" -ForegroundColor Gray
    Write-Host "    - C: drive space will be freed" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Current status:" -ForegroundColor Cyan
Write-Host "  docker-desktop: $($hasDockerDesktop)" -ForegroundColor Gray
Write-Host "  docker-desktop-data: $($hasDockerDesktopData)" -ForegroundColor Gray
Write-Host "  Storage mode: $(if ($hasDockerDesktopData) { 'Legacy (WSL)' } else { 'Modern (VHDX)' })" -ForegroundColor Gray
Write-Host ""
