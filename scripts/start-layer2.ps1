# =============================================================================
# Layer2 (用户) - 一键启动 (Windows)
# nexus_daemon @ http://127.0.0.1:9000
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$DaemonDir = Join-Path $ProjectRoot "core\nexus_daemon"
if (-not (Test-Path $DaemonDir)) {
    Write-Host "[ERROR] 未找到 core\nexus_daemon，请先执行: .\scripts\install-layer2.ps1" -ForegroundColor Red
    exit 1
}

$CondaEnv = $env:JACHIN_CONDA_ENV
if (-not $CondaEnv) {
    $condaEnvFile = Join-Path $env:USERPROFILE ".jachin\conda_env"
    if (Test-Path $condaEnvFile) { $CondaEnv = (Get-Content $condaEnvFile -Raw).Trim() }
}
if ($CondaEnv -and (Get-Command conda -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host '  Layer2 (nexus_daemon) starting' -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Ingress: http://127.0.0.1:9000"
    Write-Host "  Press Ctrl+C to stop"
    Write-Host ""
    conda run -n $CondaEnv python -m core.nexus_daemon
} else {
    $Python = $env:JACHIN_PYTHON; if (-not $Python) { $Python = "python" }
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Python not found. Run install-layer2.ps1 first.' -ForegroundColor Red
        exit 1
    }
    $CoreReq = Join-Path $ProjectRoot "core\requirements.txt"
    if ((Test-Path $CoreReq)) {
        $null = & $Python -c "import fastapi" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[INFO] Installing deps...' -ForegroundColor Yellow
            $env:PYTHONUTF8 = 1
            & $Python -m pip install -q -r $CoreReq
        }
    }
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host '  Layer2 (nexus_daemon) starting' -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Ingress: http://127.0.0.1:9000"
    Write-Host "  Press Ctrl+C to stop"
    Write-Host ""
    & $Python -m core.nexus_daemon
}
