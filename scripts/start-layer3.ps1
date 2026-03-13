# =============================================================================
# Layer3 (Desktop) - One-click start (Windows)
# clients/desktop - Jachin Terminal
# =============================================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

try {
$DesktopDir = Join-Path $ProjectRoot "clients\desktop"
if (-not (Test-Path $DesktopDir)) {
    Write-Host "[ERROR] clients\desktop not found. Run: .\scripts\install-layer3.ps1" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
    Write-Host "[INFO] Installing dependencies..." -ForegroundColor Yellow
    Push-Location $DesktopDir
    npm install
    Pop-Location
}

# L3 Sidecar 检查：bin 目录需有 l3_node 可执行文件
$BinDir = Join-Path $DesktopDir "src-tauri\bin"
$L3Exe = Get-ChildItem -Path $BinDir -Filter "l3_node-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $L3Exe) {
    Write-Host ""
    Write-Host "[Layer3] L3 Sidecar 未找到，正在构建 (需 PyInstaller)..." -ForegroundColor Yellow
    & python (Join-Path $ScriptDir "build_l3_sidecar.py") 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[Layer3] 构建失败，尝试创建占位符..." -ForegroundColor Yellow
        & python (Join-Path $ScriptDir "create_l3_stub.py") 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] 请先运行: pip install pyinstaller && python scripts\build_l3_sidecar.py" -ForegroundColor Red
            exit 1
        }
        Write-Host "[WARN] 占位符仅用于通过构建，L3 引擎不会真正运行。完整功能需: python scripts\build_l3_sidecar.py" -ForegroundColor Yellow
    } else {
        Write-Host "[Layer3] L3 Sidecar 构建完成" -ForegroundColor Green
    }
    Write-Host ""
}

$UtcNow = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
Write-Host ""
Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
Write-Host "[$UtcNow]   Layer3 (Desktop)" -ForegroundColor Cyan
Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
Write-Host "[$UtcNow]  Prereq: Start L2 first! Run in terminal 1: .\scripts\run-gateway.ps1" -ForegroundColor Yellow
Write-Host "[$UtcNow]  Skills empty? Run diagnose: .\scripts\diagnose-skill-sync.ps1" -ForegroundColor Gray
Write-Host "[$UtcNow]  Tauri requires Rust. Falls back to Vite dev if not found."
Write-Host "[$UtcNow]  Press Ctrl+C to stop"
Write-Host ""

Push-Location $DesktopDir
if (Get-Command tauri -ErrorAction SilentlyContinue) {
    npm run tauri:dev
} else {
    Write-Host "[INFO] Tauri not found, using Vite dev mode" -ForegroundColor Yellow
    npm run dev
}
Pop-Location
} finally {
    Write-Host ""
    Read-Host "Press Enter to exit"
}
