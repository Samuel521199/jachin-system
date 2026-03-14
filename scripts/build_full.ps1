# Full Build Script - L3_RECRUITMENT_BUILD_SPEC
# Usage: .\scripts\build_full.ps1
# Options: -SkipTauri (L3 only), -NoClean (skip clean, incremental), -Force (force L3+Tauri rebuild)

param(
    [switch]$SkipTauri,
    [switch]$NoClean,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "l3_node"))) {
    $root = $PSScriptRoot
    while ($root -and -not (Test-Path (Join-Path $root "l3_node"))) {
        $root = Split-Path -Parent $root
    }
}
if (-not $root) { Write-Error "Project root not found" }

Set-Location $root

# 1. Clean (unless -NoClean)
if (-not $NoClean) {
    Write-Host "`n[1/4] Cleaning build artifacts..." -ForegroundColor Cyan
    . "$PSScriptRoot\build_clean.ps1" -Root $root
} else {
    Write-Host "`n[1/4] Skip clean (-NoClean)" -ForegroundColor Gray
}

# 2. Build L3 Sidecar (skips if binary newer than source, unless -Force)
Write-Host "`n[2/4] Building L3 Sidecar (PyInstaller)..." -ForegroundColor Cyan
$l3Args = @()
if ($Force) { $l3Args += "--force" }
python scripts\build_l3_sidecar.py @l3Args
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERR] L3 Sidecar build failed" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Build Tauri Desktop (optional)
if (-not $SkipTauri) {
    Write-Host "`n[3/4] Building Tauri Desktop..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "clients\desktop")
    npm run tauri build
    $tauriExit = $LASTEXITCODE
    Pop-Location
    if ($tauriExit -ne 0) {
        Write-Host "[ERR] Tauri build failed" -ForegroundColor Red
        exit $tauriExit
    }
} else {
    Write-Host "`n[3/4] Skip Tauri (-SkipTauri)" -ForegroundColor Gray
}

# 4. Assemble portable output (L3_RECRUITMENT_BUILD_SPEC)
Write-Host "`n[4/4] Assembling portable output..." -ForegroundColor Cyan
$tauriTarget = Join-Path $root "clients\desktop\src-tauri\target\release"
$outDir = Join-Path $root "dist_jachin_desktop"
if (-not (Test-Path $tauriTarget)) {
    Write-Host "[WARN] Tauri target not found, copying L3 and resources only" -ForegroundColor Yellow
}

$null = New-Item -ItemType Directory -Force -Path $outDir
$outScripts = Join-Path $outDir "scripts"
$outBin = Join-Path $outDir "bin"
$outSkills = Join-Path $outDir "skills_repo\plugin"
$outConfig = Join-Path $outDir "config"

# Copy main exe (if Tauri built)
$mainExe = Get-ChildItem $tauriTarget -Filter "*.exe" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "l3_node" } | Select-Object -First 1
if ($mainExe) {
    Copy-Item $mainExe.FullName -Destination $outDir -Force
    Write-Host "  Copied main: $($mainExe.Name)" -ForegroundColor Gray
}

# Copy bin/l3_node
$binDir = Join-Path $root "clients\desktop\src-tauri\bin"
if (Test-Path $binDir) {
    $null = New-Item -ItemType Directory -Force -Path $outBin
    Get-ChildItem $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName -Destination $outBin -Force
    }
    Write-Host "  Copied bin/l3_node*.exe" -ForegroundColor Gray
}

# Copy scripts
$null = New-Item -ItemType Directory -Force -Path $outScripts
$chromeScript = Join-Path $root "scripts\launch_chrome_debug.ps1"
if (-not (Test-Path $chromeScript)) { $chromeScript = Join-Path $root "skills_repo\plugin\scripts\launch_chrome_debug.ps1" }
if (Test-Path $chromeScript) {
    Copy-Item $chromeScript -Destination $outScripts -Force
}
Copy-Item (Join-Path $root "scripts\run_l3.ps1") -Destination $outScripts -Force
Write-Host "  Copied scripts: launch_chrome_debug.ps1, run_l3.ps1" -ForegroundColor Gray

# Copy skills_repo/plugin
$pluginSrc = Join-Path $root "skills_repo\plugin"
if (Test-Path $pluginSrc) {
    $null = New-Item -ItemType Directory -Force -Path $outSkills
    if (Test-Path (Join-Path $pluginSrc "2-track-a-atomic-mcp")) {
        Copy-Item (Join-Path $pluginSrc "2-track-a-atomic-mcp") -Destination $outSkills -Recurse -Force
    }
    if (Test-Path (Join-Path $pluginSrc "data")) {
        Copy-Item (Join-Path $pluginSrc "data") -Destination $outSkills -Recurse -Force
    }
    if (Test-Path (Join-Path $pluginSrc "scripts")) {
        Copy-Item (Join-Path $pluginSrc "scripts") -Destination $outSkills -Recurse -Force
    }
    if (Test-Path (Join-Path $pluginSrc "src")) {
        Copy-Item (Join-Path $pluginSrc "src") -Destination $outSkills -Recurse -Force
    }
    if (Test-Path (Join-Path $pluginSrc ".env.example")) {
        Copy-Item (Join-Path $pluginSrc ".env.example") -Destination $outSkills -Force
    }
    Write-Host "  Copied skills_repo/plugin/" -ForegroundColor Gray
}

# Copy config
$null = New-Item -ItemType Directory -Force -Path $outConfig
if (Test-Path (Join-Path $root "config\skills_config.yaml")) {
    Copy-Item (Join-Path $root "config\skills_config.yaml") -Destination $outConfig -Force
}
if (Test-Path (Join-Path $root "config\l3_recruitment.yaml.example")) {
    Copy-Item (Join-Path $root "config\l3_recruitment.yaml.example") -Destination (Join-Path $outConfig "l3_recruitment.yaml.example") -Force
}
Write-Host "  Copied config/" -ForegroundColor Gray

# Copy .env.example
if (Test-Path (Join-Path $root ".env.example")) {
    Copy-Item (Join-Path $root ".env.example") -Destination $outDir -Force
}

# Copy README_DEPLOY.md (optional)
if (Test-Path (Join-Path $root "docs\README_DEPLOY.md")) {
    Copy-Item (Join-Path $root "docs\README_DEPLOY.md") -Destination $outDir -Force
} elseif (Test-Path (Join-Path $root "README_DEPLOY.md")) {
    Copy-Item (Join-Path $root "README_DEPLOY.md") -Destination $outDir -Force
}

Write-Host "`n[Done] Portable output: $outDir" -ForegroundColor Green
Write-Host "  Run: $outDir\*.exe" -ForegroundColor Gray
Write-Host "  Debug L3: set JACHIN_SKIP_L3_SPAWN=1 then run $outScripts\run_l3.ps1 --ws-only" -ForegroundColor Gray
