# Finalize directory structure to match v3.2 design

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Finalizing v3.2 Structure" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Move models/ to memory/schema/ if needed
Write-Host "[1/5] Checking models/ directory..." -ForegroundColor Yellow
if (Test-Path "core\models") {
    $modelFiles = Get-ChildItem -Path "core\models" -File
    if ($modelFiles.Count -gt 1) {
        Write-Host "  [INFO] models/ contains files, keeping for now" -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] models/ is empty or only __init__.py" -ForegroundColor Green
    }
}

# Step 2: Check services/ directory
Write-Host "[2/5] Checking services/ directory..." -ForegroundColor Yellow
if (Test-Path "core\services") {
    $serviceFiles = Get-ChildItem -Path "core\services" -Recurse -File
    if ($serviceFiles.Count -gt 0) {
        Write-Host "  [INFO] services/ contains $($serviceFiles.Count) files" -ForegroundColor Yellow
        Write-Host "  [NOTE] services/ not in v3.2 design, consider moving to api/ or brain/" -ForegroundColor Yellow
    } else {
        Write-Host "  [OK] services/ is empty" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] services/ does not exist" -ForegroundColor Green
}

# Step 3: Verify required directories exist
Write-Host "[3/5] Verifying required directories..." -ForegroundColor Yellow

$requiredDirs = @(
    "core\app",
    "core\api",
    "core\brain\llm",
    "core\brain\ray_cluster",
    "core\brain\planner",
    "core\runtime",
    "core\runtime\sandbox",
    "core\runtime\schemas",
    "core\registry",
    "core\memory",
    "core\memory\schema",
    "core\web_ui",
    "cloud\marketplace",
    "cloud\auth",
    "skills_repo",
    "installer",
    "config"
)

$missingDirs = @()
foreach ($dir in $requiredDirs) {
    if (-not (Test-Path $dir)) {
        $missingDirs += $dir
        Write-Host "  [MISSING] $dir" -ForegroundColor Red
    }
}

if ($missingDirs.Count -eq 0) {
    Write-Host "  [OK] All required directories exist" -ForegroundColor Green
} else {
    Write-Host "  [WARN] $($missingDirs.Count) directories missing" -ForegroundColor Yellow
}

# Step 4: Verify main.py location
Write-Host "[4/5] Verifying main.py location..." -ForegroundColor Yellow
if (Test-Path "core\main.py") {
    Write-Host "  [OK] main.py is in core/" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] main.py not found in core/" -ForegroundColor Red
}

# Step 5: Verify requirements.txt location
Write-Host "[5/5] Verifying requirements.txt location..." -ForegroundColor Yellow
if (Test-Path "core\requirements.txt") {
    Write-Host "  [OK] requirements.txt is in core/" -ForegroundColor Green
} else {
    Write-Host "  [WARN] requirements.txt not found in core/" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Structure Verification Complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Summary
Write-Host "Current structure:" -ForegroundColor Cyan
Write-Host "  Tier 1 (cloud/):" -ForegroundColor Cyan
Write-Host "    - marketplace/: $(if (Test-Path 'cloud\marketplace') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'cloud\marketplace') { 'Green' } else { 'Red' })
Write-Host "    - auth/: $(if (Test-Path 'cloud\auth') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'cloud\auth') { 'Green' } else { 'Red' })

Write-Host "  Tier 2 (core/):" -ForegroundColor Cyan
Write-Host "    - app/: $(if (Test-Path 'core\app') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\app') { 'Green' } else { 'Red' })
Write-Host "    - api/: $(if (Test-Path 'core\api') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\api') { 'Green' } else { 'Red' })
Write-Host "    - brain/llm/: $(if (Test-Path 'core\brain\llm') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\brain\llm') { 'Green' } else { 'Red' })
Write-Host "    - brain/ray_cluster/: $(if (Test-Path 'core\brain\ray_cluster') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\brain\ray_cluster') { 'Green' } else { 'Red' })
Write-Host "    - brain/planner/: $(if (Test-Path 'core\brain\planner') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\brain\planner') { 'Green' } else { 'Red' })
Write-Host "    - runtime/: $(if (Test-Path 'core\runtime') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\runtime') { 'Green' } else { 'Red' })
Write-Host "    - registry/: $(if (Test-Path 'core\registry') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\registry') { 'Green' } else { 'Red' })
Write-Host "    - memory/: $(if (Test-Path 'core\memory') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\memory') { 'Green' } else { 'Red' })
Write-Host "    - web_ui/: $(if (Test-Path 'core\web_ui') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'core\web_ui') { 'Green' } else { 'Red' })

Write-Host "  Other:" -ForegroundColor Cyan
Write-Host "    - skills_repo/: $(if (Test-Path 'skills_repo') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'skills_repo') { 'Green' } else { 'Red' })
Write-Host "    - installer/: $(if (Test-Path 'installer') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'installer') { 'Green' } else { 'Red' })
Write-Host "    - config/: $(if (Test-Path 'config') { 'OK' } else { 'MISSING' })" -ForegroundColor $(if (Test-Path 'config') { 'Green' } else { 'Red' })

Write-Host ""
