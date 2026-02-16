# Jachin-System v3.2 File Structure Restructuring Script

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Jachin-System v3.2 Restructuring" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Create new directory structure
Write-Host "[1/8] Creating new directory structure..." -ForegroundColor Yellow

# Tier 1: Cloud
New-Item -ItemType Directory -Path "cloud\marketplace" -Force | Out-Null
New-Item -ItemType Directory -Path "cloud\auth" -Force | Out-Null
Write-Host "  [OK] cloud/ directories created" -ForegroundColor Green

# Tier 2: Core (rename backend)
Write-Host "[2/8] Renaming backend/ to core/..." -ForegroundColor Yellow
if (Test-Path "backend") {
    if (Test-Path "core") {
        Write-Host "  [SKIP] core/ already exists" -ForegroundColor Yellow
    } else {
        Rename-Item -Path "backend" -NewName "core" -Force
        Write-Host "  [OK] backend/ renamed to core/" -ForegroundColor Green
    }
} else {
    Write-Host "  [WARN] backend/ not found" -ForegroundColor Yellow
}

# Step 3: Adjust core/ internal structure
Write-Host "[3/8] Adjusting core/ internal structure..." -ForegroundColor Yellow

# Create new directories
New-Item -ItemType Directory -Path "core\app" -Force | Out-Null
New-Item -ItemType Directory -Path "core\brain\ray_cluster" -Force | Out-Null
New-Item -ItemType Directory -Path "core\brain\planner" -Force | Out-Null
New-Item -ItemType Directory -Path "core\runtime" -Force | Out-Null
New-Item -ItemType Directory -Path "core\runtime\sandbox" -Force | Out-Null
New-Item -ItemType Directory -Path "core\runtime\schemas" -Force | Out-Null
New-Item -ItemType Directory -Path "core\registry" -Force | Out-Null
New-Item -ItemType Directory -Path "core\memory\schema" -Force | Out-Null
New-Item -ItemType Directory -Path "core\memory\schema\migrations" -Force | Out-Null
New-Item -ItemType Directory -Path "core\memory\schema\migrations\versions" -Force | Out-Null
New-Item -ItemType Directory -Path "core\web_ui" -Force | Out-Null
Write-Host "  [OK] core/ internal directories created" -ForegroundColor Green

# Step 4: Move existing files to new locations
Write-Host "[4/8] Moving existing files..." -ForegroundColor Yellow

# Move core/core/registry.py to core/registry/
if (Test-Path "core\core\registry.py") {
    if (-not (Test-Path "core\registry\registry.py")) {
        Move-Item -Path "core\core\registry.py" -Destination "core\registry\registry.py" -Force
        Write-Host "  [OK] registry.py moved" -ForegroundColor Green
    }
}

# Move core/core/protocol.py to core/registry/
if (Test-Path "core\core\protocol.py") {
    if (-not (Test-Path "core\registry\protocol.py")) {
        Move-Item -Path "core\core\protocol.py" -Destination "core\registry\protocol.py" -Force
        Write-Host "  [OK] protocol.py moved" -ForegroundColor Green
    }
}

# Move core/core/llm/ to core/brain/llm/
if (Test-Path "core\core\llm") {
    if (-not (Test-Path "core\brain\llm")) {
        Move-Item -Path "core\core\llm" -Destination "core\brain\llm" -Force
        Write-Host "  [OK] llm/ moved to brain/llm/" -ForegroundColor Green
    }
}

# Move core/core/memory/ content (keep structure, add schema subdirectory)
if (Test-Path "core\core\memory") {
    Write-Host "  [OK] memory/ structure confirmed" -ForegroundColor Green
}

# Step 5: Create new directories and files
Write-Host "[5/8] Creating new directories and files..." -ForegroundColor Yellow

# skills_repo
New-Item -ItemType Directory -Path "skills_repo" -Force | Out-Null
Set-Content -Path "skills_repo\.gitkeep" -Value ""
$skillsRepoReadme = @"
# Skills Repository

This directory stores all installed skills.

Each skill has its own subdirectory:

\`\`\`
skills_repo/
  {skill_id}/
    manifest.yaml      # Skill manifest
    main.py            # Entry point
    requirements.txt   # Python dependencies
\`\`\`
"@
Set-Content -Path "skills_repo\README.md" -Value $skillsRepoReadme
Write-Host "  [OK] skills_repo/ created" -ForegroundColor Green

# installer
New-Item -ItemType Directory -Path "installer" -Force | Out-Null
$installerReadme = @"
# Installer Scripts

Installation and deployment scripts:

- install.sh / install.ps1 - Installation script
- cluster_setup.sh / cluster_setup.ps1 - Cluster setup script
- init_database.sh / init_database.ps1 - Database initialization
- validate_setup.sh / validate_setup.ps1 - Environment validation
"@
Set-Content -Path "installer\README.md" -Value $installerReadme
Write-Host "  [OK] installer/ created" -ForegroundColor Green

# config
New-Item -ItemType Directory -Path "config" -Force | Out-Null
Write-Host "  [OK] config/ created" -ForegroundColor Green

# Step 6: Create __init__.py files
Write-Host "[6/8] Creating __init__.py files..." -ForegroundColor Yellow

$initFiles = @(
    "core\app\__init__.py",
    "core\brain\__init__.py",
    "core\brain\ray_cluster\__init__.py",
    "core\brain\planner\__init__.py",
    "core\runtime\__init__.py",
    "core\runtime\sandbox\__init__.py",
    "core\runtime\schemas\__init__.py",
    "core\registry\__init__.py",
    "core\memory\schema\__init__.py",
    "cloud\marketplace\__init__.py",
    "cloud\auth\__init__.py"
)

foreach ($file in $initFiles) {
    if (-not (Test-Path $file)) {
        Set-Content -Path $file -Value '"""Module initialization"""'
    }
}
Write-Host "  [OK] __init__.py files created" -ForegroundColor Green

# Step 7: Verify requirements.txt
Write-Host "[7/8] Verifying requirements.txt..." -ForegroundColor Yellow
if (Test-Path "core\requirements.txt") {
    Write-Host "  [OK] requirements.txt found" -ForegroundColor Green
} else {
    Write-Host "  [WARN] requirements.txt not found" -ForegroundColor Yellow
}

# Step 8: Verify main.py
Write-Host "[8/8] Verifying main.py..." -ForegroundColor Yellow
if (Test-Path "core\main.py") {
    Write-Host "  [OK] main.py found" -ForegroundColor Green
} else {
    Write-Host "  [WARN] main.py not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Restructuring Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Update import statements (backend -> core)" -ForegroundColor Yellow
Write-Host "2. Update script paths" -ForegroundColor Yellow
Write-Host "3. Update docker-compose.yml paths" -ForegroundColor Yellow
Write-Host "4. Update documentation paths" -ForegroundColor Yellow
Write-Host ""
