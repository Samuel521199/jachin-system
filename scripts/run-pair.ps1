# =============================================================================
# 边缘智能体配对 - 6 位码连接指挥部 (极客终端版)
# 用法: .\scripts\run-pair.ps1 [-BaseUrl http://localhost:3000]
# =============================================================================

param([string]$BaseUrl = $env:NEXUS_BASE_URL)
if (-not $BaseUrl) { $BaseUrl = "http://localhost:3000" }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

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
    & $Python -m pip install -q click rich 2>$null | Out-Null
}

if ($CondaEnv) {
    conda run -n $CondaEnv python -m core.cli pair --base-url $BaseUrl
} else {
    & $Python -m core.cli pair --base-url $BaseUrl
}
