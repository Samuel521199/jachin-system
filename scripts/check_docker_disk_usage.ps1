# Check Docker disk usage
# 检查Docker磁盘使用情况

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Docker Disk Usage Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check C: drive Docker directory
Write-Host "[1/3] Checking C: drive Docker directory..." -ForegroundColor Yellow
$cDockerPath = "$env:USERPROFILE\AppData\Local\Docker"
if (Test-Path $cDockerPath) {
    Write-Host "  Path: $cDockerPath" -ForegroundColor Cyan
    
    $dirs = Get-ChildItem $cDockerPath -Directory -ErrorAction SilentlyContinue
    if ($dirs) {
        Write-Host "  Subdirectories:" -ForegroundColor Cyan
        foreach ($dir in $dirs) {
            $dirPath = $dir.FullName
            $size = (Get-ChildItem $dirPath -Recurse -File -ErrorAction SilentlyContinue | 
                     Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
            if ($size) {
                $sizeGB = [math]::Round($size / 1GB, 2)
                Write-Host "    $($dir.Name): $sizeGB GB" -ForegroundColor White
            } else {
                Write-Host "    $($dir.Name): < 1 MB" -ForegroundColor Gray
            }
        }
    } else {
        Write-Host "  [OK] Directory is empty" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] C: drive Docker directory not found" -ForegroundColor Green
}

# Check E: drive Docker directory
Write-Host ""
Write-Host "[2/3] Checking E: drive Docker directory..." -ForegroundColor Yellow
$eDockerPath = "E:\docker"
if (Test-Path $eDockerPath) {
    Write-Host "  Path: $eDockerPath" -ForegroundColor Cyan
    
    $dirs = Get-ChildItem $eDockerPath -Directory -ErrorAction SilentlyContinue
    if ($dirs) {
        Write-Host "  Subdirectories:" -ForegroundColor Cyan
        foreach ($dir in $dirs) {
            $dirPath = $dir.FullName
            $size = (Get-ChildItem $dirPath -Recurse -File -ErrorAction SilentlyContinue | 
                     Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
            if ($size) {
                $sizeGB = [math]::Round($size / 1GB, 2)
                Write-Host "    $($dir.Name): $sizeGB GB" -ForegroundColor White
            } else {
                Write-Host "    $($dir.Name): < 1 MB" -ForegroundColor Gray
            }
        }
    }
} else {
    Write-Host "  [WARN] E: drive Docker directory not found" -ForegroundColor Yellow
}

# Check Docker system disk usage
Write-Host ""
Write-Host "[3/3] Checking Docker system disk usage..." -ForegroundColor Yellow
try {
    $dockerInfo = docker system df 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host $dockerInfo
    } else {
        Write-Host "  [ERROR] Cannot get Docker disk usage" -ForegroundColor Red
    }
} catch {
    Write-Host "  [ERROR] Docker command failed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Summary" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "If C: drive still has large Docker data:" -ForegroundColor Yellow
Write-Host "  1. Ensure Docker is working correctly" -ForegroundColor White
Write-Host "  2. Verify E: drive migration is complete" -ForegroundColor White
Write-Host "  3. Stop Docker Desktop" -ForegroundColor White
Write-Host "  4. Backup C: drive data (optional)" -ForegroundColor White
Write-Host "  5. Delete C: drive old data" -ForegroundColor White
Write-Host ""
