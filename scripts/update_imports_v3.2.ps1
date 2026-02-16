# Update imports and paths after restructuring backend/ to core/

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Updating imports and paths" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Update Python imports in core/
Write-Host "[1/5] Updating Python imports..." -ForegroundColor Yellow

$pythonFiles = Get-ChildItem -Path "core" -Recurse -Filter "*.py"
$updatedCount = 0

foreach ($file in $pythonFiles) {
    $content = Get-Content $file.FullName -Raw -Encoding UTF8
    $originalContent = $content
    
    # Replace backend imports
    $content = $content -replace 'from backend\.', 'from core.'
    $content = $content -replace 'import backend\.', 'import core.'
    $content = $content -replace 'from backend ', 'from core '
    $content = $content -replace 'import backend ', 'import core '
    
    # Update relative imports for moved modules
    $content = $content -replace 'from core\.llm', 'from core.brain.llm'
    $content = $content -replace 'from core\.registry', 'from core.registry'
    $content = $content -replace 'from core\.protocol', 'from core.registry.protocol'
    
    if ($content -ne $originalContent) {
        Set-Content -Path $file.FullName -Value $content -Encoding UTF8 -NoNewline
        $updatedCount++
        Write-Host "  Updated: $($file.FullName)" -ForegroundColor Gray
    }
}

Write-Host "  [OK] Updated $updatedCount Python files" -ForegroundColor Green

# Step 2: Update script paths
Write-Host "[2/5] Updating script paths..." -ForegroundColor Yellow

$scriptFiles = @(
    "scripts\run_backend.bat",
    "scripts\run_backend.ps1",
    "scripts\start.ps1",
    "scripts\restart.ps1",
    "启动后端.bat"
)

foreach ($file in $scriptFiles) {
    if (Test-Path $file) {
        $content = Get-Content $file.FullName -Raw -Encoding UTF8
        $originalContent = $content
        
        $content = $content -replace 'backend\\', 'core\'
        $content = $content -replace 'backend/', 'core/'
        $content = $content -replace 'backend', 'core'
        
        if ($content -ne $originalContent) {
            Set-Content -Path $file -Value $content -Encoding UTF8 -NoNewline
            Write-Host "  Updated: $file" -ForegroundColor Gray
        }
    }
}

Write-Host "  [OK] Script paths updated" -ForegroundColor Green

# Step 3: Update docker-compose.yml
Write-Host "[3/5] Updating docker-compose.yml..." -ForegroundColor Yellow

if (Test-Path "docker-compose.yml") {
    $content = Get-Content "docker-compose.yml" -Raw -Encoding UTF8
    $originalContent = $content
    
    $content = $content -replace 'context: \./backend', 'context: ./core'
    $content = $content -replace 'backend:', 'core:'
    
    if ($content -ne $originalContent) {
        Set-Content -Path "docker-compose.yml" -Value $content -Encoding UTF8 -NoNewline
        Write-Host "  [OK] docker-compose.yml updated" -ForegroundColor Green
    } else {
        Write-Host "  [SKIP] No changes needed" -ForegroundColor Yellow
    }
}

if (Test-Path "docker-compose.dev.yml") {
    $content = Get-Content "docker-compose.dev.yml" -Raw -Encoding UTF8
    $originalContent = $content
    
    $content = $content -replace 'context: \./backend', 'context: ./core'
    $content = $content -replace 'backend:', 'core:'
    
    if ($content -ne $originalContent) {
        Set-Content -Path "docker-compose.dev.yml" -Value $content -Encoding UTF8 -NoNewline
        Write-Host "  [OK] docker-compose.dev.yml updated" -ForegroundColor Green
    }
}

# Step 4: Update documentation
Write-Host "[4/5] Updating documentation..." -ForegroundColor Yellow

$docFiles = Get-ChildItem -Path "docs" -Recurse -Filter "*.md"
$docUpdatedCount = 0

foreach ($file in $docFiles) {
    $content = Get-Content $file.FullName -Raw -Encoding UTF8
    $originalContent = $content
    
    $content = $content -replace 'backend/', 'core/'
    $content = $content -replace 'backend\\', 'core\'
    $content = $content -replace '`backend`', '`core`'
    
    if ($content -ne $originalContent) {
        Set-Content -Path $file.FullName -Value $content -Encoding UTF8 -NoNewline
        $docUpdatedCount++
    }
}

Write-Host "  [OK] Updated $docUpdatedCount documentation files" -ForegroundColor Green

# Step 5: Update core/README.md
Write-Host "[5/5] Updating core/README.md..." -ForegroundColor Yellow

if (Test-Path "core\README.md") {
    $content = Get-Content "core\README.md" -Raw -Encoding UTF8
    $content = $content -replace 'backend/', 'core/'
    $content = $content -replace 'Backend \(Brain Layer\)', 'Core (Tier 2 - Jachin Hive)'
    Set-Content -Path "core\README.md" -Value $content -Encoding UTF8 -NoNewline
    Write-Host "  [OK] core/README.md updated" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Update Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Note: Please manually review and fix any remaining import issues" -ForegroundColor Yellow
Write-Host ""
