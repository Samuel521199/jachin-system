# =============================================================================
# Prerequisites check - run before install
# Usage: .\scripts\check-prerequisites.ps1 [cloud|layer2|layer3] [-NoPrompt]
#   -NoPrompt : when missing, only show commands (for install scripts, no interactive prompt)
#   default   : when missing, prompt "Install now? (Y/N)" and run winget if Y
# =============================================================================

param([string]$Layer = "all", [switch]$NoPrompt)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

$missing = @()
$missingNode = $false
$missingPython = $false
$missingDocker = $false
$missingTauri = $false
$warnings = @()

function Test-Cmd { param($name) Get-Command $name -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Prerequisites Check [$Layer]" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Cloud: Node.js + npm
if ($Layer -in "all", "cloud") {
    Write-Host "> Checking Node.js..." -ForegroundColor Gray -NoNewline
    if (-not (Test-Cmd node)) {
        Write-Host " MISS" -ForegroundColor Red
        $missing += "Node.js (winget install OpenJS.NodeJS.LTS)"
        $script:missingNode = $true
    } elseif (-not (Test-Cmd npm)) {
        Write-Host " MISS" -ForegroundColor Red
        $missing += "npm (comes with Node.js)"
        $script:missingNode = $true
    } else {
        Write-Host " OK" -ForegroundColor Green
        Write-Host "      node $(node -v)  npm $(npm -v)" -ForegroundColor DarkGray
    }
}

# Layer2: Python + pip, optional Docker
if ($Layer -in "all", "layer2") {
    Write-Host "> Checking Python..." -ForegroundColor Gray -NoNewline
    $py = $env:JACHIN_PYTHON; if (-not $py) { $py = "python" }
    if (-not (Test-Cmd $py)) { $py = "python3" }
    if (-not (Test-Cmd $py)) {
        Write-Host " MISS" -ForegroundColor Red
        $missing += "Python 3.10+ (winget install Python.Python.3.11)"
        $script:missingPython = $true
    } else {
        Write-Host " OK" -ForegroundColor Green
        $ver = & $py -c "import sys; print(sys.version.split()[0])" 2>$null
        if ($ver) { Write-Host "      $py $ver" -ForegroundColor DarkGray }
        if ($ver -match "^3\.13") {
            Write-Host "  > Conda (recommended for Ray)..." -ForegroundColor Gray -NoNewline
            if (-not (Test-Cmd conda)) {
                Write-Host " not installed" -ForegroundColor Yellow
                $warnings += "Conda (Ray needs Python 3.10-3.12; install Miniconda: winget install Anaconda.Miniconda3)"
            } else { Write-Host " OK" -ForegroundColor Green }
        }
    }
    if ($Layer -in "all", "layer2") {
        Write-Host "  > Docker (optional for Qdrant)..." -ForegroundColor Gray -NoNewline
        if (-not (Test-Cmd docker)) {
            Write-Host " not installed" -ForegroundColor Yellow
            $warnings += "Docker (optional: winget install Docker.DockerDesktop)"
        } else {
            Write-Host " OK" -ForegroundColor Green
        }
    }
}

# Layer3: Node.js (skip if already checked for cloud), optional Rust
if ($Layer -in "all", "layer3") {
    if ($Layer -eq "layer3") {
        Write-Host "> Checking Node.js (Layer3)..." -ForegroundColor Gray -NoNewline
        if (-not (Test-Cmd node)) {
            Write-Host " MISS" -ForegroundColor Red
            $missing += "Node.js"
            $script:missingNode = $true
        } else {
            Write-Host " OK" -ForegroundColor Green
        }
    }
    if ($Layer -in "all", "layer3") {
        Write-Host "  > Tauri/Rust (optional for desktop)..." -ForegroundColor Gray -NoNewline
        if (-not (Test-Cmd tauri)) {
            Write-Host " not installed" -ForegroundColor Yellow
            $warnings += "Rust + Tauri CLI (optional: winget install Rustlang.Rustup)"
            $script:missingTauri = $true
        } else {
            Write-Host " OK" -ForegroundColor Green
        }
    }
}

# Summary
Write-Host "> Summary..." -ForegroundColor Gray

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "[FAIL] Missing required:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host ""
    $doInstall = $false
    if (-not $NoPrompt) {
        Write-Host "> Install missing via winget now? (Y/N): " -ForegroundColor Yellow -NoNewline
        $r = Read-Host
        $doInstall = ($r -eq "Y" -or $r -eq "y" -or $r -eq "yes")
    }
    if ($doInstall) {
        $wingetExe = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $wingetExe) {
            Write-Host "[ERROR] winget not found. Install via Microsoft Store or Windows Update." -ForegroundColor Red
            exit 1
        }
        $wOpts = @("--accept-package-agreements", "--accept-source-agreements", "--silent")
        if ($missingNode) {
            Write-Host "> Installing Node.js (winget OpenJS.NodeJS.LTS)..." -ForegroundColor Cyan
            & $wingetExe.Source install OpenJS.NodeJS.LTS @wOpts
            if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] Node.js install may have failed. Try: winget install OpenJS.NodeJS.LTS" -ForegroundColor Yellow }
        }
        if ($missingPython) {
            Write-Host "> Installing Python (winget Python.Python.3.11)..." -ForegroundColor Cyan
            & $wingetExe.Source install Python.Python.3.11 @wOpts
            if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] Python install may have failed. Try: winget install Python.Python.3.11" -ForegroundColor Yellow }
        }
        Write-Host ""
        Write-Host "[OK] Install done. Restart terminal for PATH, then run this script again." -ForegroundColor Green
        Write-Host ""
        exit 0
    }
    Write-Host "Install manually (PowerShell as Admin):" -ForegroundColor Yellow
    if ($missingNode) { Write-Host "  winget install OpenJS.NodeJS.LTS" -ForegroundColor Gray }
    if ($missingPython) { Write-Host "  winget install Python.Python.3.11" -ForegroundColor Gray }
    Write-Host ""
    Write-Host "Or run again and choose Y when prompted" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "[WARN] Optional (some features limited):" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    $doOptInstall = $false
    if (-not $NoPrompt) {
        Write-Host ""
        Write-Host "> Install optional via winget now? (Y/N): " -ForegroundColor Yellow -NoNewline
        $r = Read-Host
        $doOptInstall = ($r -eq "Y" -or $r -eq "y" -or $r -eq "yes")
    }
    if ($doOptInstall) {
        $wingetExe = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $wingetExe) {
            Write-Host "[ERROR] winget not found." -ForegroundColor Red
        } else {
            $wOpts = @("--accept-package-agreements", "--accept-source-agreements", "--silent")
            if ($missingDocker) {
                Write-Host "> Installing Docker (winget Docker.DockerDesktop)..." -ForegroundColor Cyan
                & $wingetExe.Source install Docker.DockerDesktop @wOpts
            }
            if ($missingTauri) {
                Write-Host "> Installing Rust (winget Rustlang.Rustup)..." -ForegroundColor Cyan
                & $wingetExe.Source install Rustlang.Rustup @wOpts
                Write-Host "  After restart, run: npm i -g @tauri-apps/cli" -ForegroundColor Gray
            }
            Write-Host ""
            Write-Host "[OK] Install done. Restart terminal for PATH." -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "[OK] Prerequisites satisfied" -ForegroundColor Green
Write-Host ""
exit 0
