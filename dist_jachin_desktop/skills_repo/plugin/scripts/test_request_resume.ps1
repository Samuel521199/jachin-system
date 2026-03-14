# =============================================================================
# Test Request Resume - Boss zhipin
# =============================================================================

param(
    [string]$JobName = "Java_杭州 10-15K",
    [int]$MaxCount = 10,
    [ValidateSet("batch", "harvester")]
    [string]$Mode = "batch",
    [switch]$NoLaunchChrome
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$PluginRoot = Split-Path -Parent $ScriptDir
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PluginRoot)
Set-Location $ProjectRoot

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[ERR] python not found" -ForegroundColor Red
    exit 1
}

function Test-ChromeDebugPort {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -Method GET -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $true
    }
    catch { return $false }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Request Resume Test" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Mode: $Mode | Job: $JobName | Max: $MaxCount" -ForegroundColor White
Write-Host ""

if (-not (Test-ChromeDebugPort)) {
    if ($NoLaunchChrome) {
        Write-Host "[ERR] Chrome not running on 9222. Run launch_chrome_debug.ps1 first" -ForegroundColor Red
        exit 1
    }
    Write-Host "[0/2] Starting Chrome..." -ForegroundColor Yellow
    $chromeScript = Join-Path $PluginRoot "scripts\launch_chrome_debug.ps1"
    if (-not (Test-Path $chromeScript)) {
        Write-Host "[ERR] launch_chrome_debug.ps1 not found" -ForegroundColor Red
        exit 1
    }
    & $chromeScript
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-ChromeDebugPort) { break }
    }
    if (-not (Test-ChromeDebugPort)) {
        Write-Host "[ERR] Chrome startup timeout" -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host "[0/2] Chrome ready (9222)" -ForegroundColor Green
}

Write-Host ""
Write-Host "[1/2] Confirm Boss zhipin chat page is open, job: $JobName" -ForegroundColor Yellow
Write-Host "      Press any key to continue..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
Write-Host ""

Write-Host "[2/2] Running..." -ForegroundColor Cyan
Write-Host ""

$exitCode = 0
try {
    if ($Mode -eq "batch") {
        python "$ProjectRoot\skills_repo\plugin\scripts\test_request_resume_batch.py" --job $JobName --max $MaxCount
    }
    else {
        python "$ProjectRoot\scripts\test_boss_harvester_l3_local.py" --job $JobName --max $MaxCount
    }
    $exitCode = $LASTEXITCODE
}
catch {
    Write-Host "[ERR] Failed: $_" -ForegroundColor Red
    $exitCode = 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Done" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

exit $exitCode
