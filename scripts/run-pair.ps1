# =============================================================================
# 边缘智能体配对 - 6 位码连接指挥部 (极客终端版)
# 用法: .\scripts\run-pair.ps1 [-BaseUrl http://localhost:3000]
# 恢复: .\scripts\run-pair.ps1 -Recover -Code "ABC123"  (云端已配对但本地未保存时)
# =============================================================================

param(
    [string]$BaseUrl = $env:NEXUS_BASE_URL,
    [switch]$Recover,
    [string]$Code = ""
)
if (-not $BaseUrl) { $BaseUrl = "http://localhost:3000" }

$ErrorActionPreference = "Continue"
$env:PYTHONUNBUFFERED = "1"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "[Pair] Starting pairing script (Nexus: $BaseUrl)..." -ForegroundColor Cyan

$CondaEnv = $env:JACHIN_CONDA_ENV
if (-not $CondaEnv) {
    $condaEnvFile = Join-Path $env:USERPROFILE ".jachin\conda_env"
    if (Test-Path $condaEnvFile) { $CondaEnv = (Get-Content $condaEnvFile -Raw).Trim() }
}
if ($CondaEnv -and (Get-Command conda -ErrorAction SilentlyContinue)) {
    $Python = "conda run -n $CondaEnv python"
} else {
    $Python = $env:JACHIN_PYTHON; if (-not $Python) { $Python = "python" }
}
if (-not $CondaEnv -and -not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] Python not found. Run install-layer2.ps1 first.' -ForegroundColor Red
    exit 1
}

# CLI deps (when not using conda)
if (-not $CondaEnv) {
    $env:PYTHONUTF8 = 1
    Write-Host "[Pair] Installing CLI deps (httpx, click, rich)..." -ForegroundColor Gray
    & $Python -m pip install -q httpx click rich 2>&1
}

$cliArgs = @("pair", "--base-url", $BaseUrl)
if ($Recover -and $Code) {
    Write-Host "[Pair] Recovery mode: fetching credentials with code..." -ForegroundColor Cyan
    $cliArgs += "--recover", "--code", $Code.Trim()
} else {
    Write-Host "[Pair] Running CLI pair (output below)..." -ForegroundColor Gray
}
Write-Host ""
if ($CondaEnv) {
    conda run -n $CondaEnv --no-capture-output python -m core.cli @cliArgs
} else {
    & $Python -m core.cli @cliArgs
}
$pairExit = $LASTEXITCODE
Write-Host ""
if ($pairExit -eq 0) {
    Write-Host "[Pair] [OK] Pairing completed." -ForegroundColor Green
} else {
    Write-Host "[Pair] [FAILED] exit code $pairExit" -ForegroundColor Red
}
exit $pairExit
