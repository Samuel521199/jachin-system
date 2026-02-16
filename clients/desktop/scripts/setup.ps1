# Desktop Client Setup Script - Windows PowerShell

Write-Host "==========================================" -ForegroundColor Green
Write-Host "Setting up Jachin Desktop Client" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""

# Check Node.js
Write-Host "Checking Node.js..." -ForegroundColor Cyan
$nodeVersion = node --version 2>$null
if ($nodeVersion) {
    Write-Host "[OK] Node.js: $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Node.js not found. Please install Node.js v18+" -ForegroundColor Red
    exit 1
}

# Check Rust
Write-Host "Checking Rust..." -ForegroundColor Cyan
$rustVersion = rustc --version 2>$null
if ($rustVersion) {
    Write-Host "[OK] Rust: $rustVersion" -ForegroundColor Green
} else {
    Write-Host "[WARN] Rust not found. Installing Rust..." -ForegroundColor Yellow
    Write-Host "  Please visit: https://rustup.rs/" -ForegroundColor Yellow
    Write-Host "  Or run: winget install Rustlang.Rustup" -ForegroundColor Yellow
    exit 1
}

# Check Tauri CLI
Write-Host "Checking Tauri CLI..." -ForegroundColor Cyan
$tauriVersion = tauri --version 2>$null
if ($tauriVersion) {
    Write-Host "[OK] Tauri CLI: $tauriVersion" -ForegroundColor Green
} else {
    Write-Host "[INFO] Installing Tauri CLI..." -ForegroundColor Cyan
    npm install -g @tauri-apps/cli@next
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Tauri CLI installed" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Failed to install Tauri CLI" -ForegroundColor Red
        exit 1
    }
}

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Cyan
npm install
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install dependencies" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Setup completed!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Make sure backend is running: .\start.bat" -ForegroundColor Yellow
Write-Host "  2. Start desktop client: npm run tauri:dev" -ForegroundColor Yellow
