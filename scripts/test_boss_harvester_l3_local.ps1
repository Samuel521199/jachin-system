# =============================================================================
# 测试 L3 本地 boss_harvester（直接调用逻辑，数据卷 ~/.jachin/client_volumes/）
# atom_inbox_harvester 已从 L2 剥离，现为 L3 本地伴生服务
# 用法: .\scripts\test_boss_harvester_l3_local.ps1 -JobName "Java_杭州 4-6K" -MaxCount 5
# =============================================================================

param(
    [string]$JobName = "Java_杭州 4-6K",
    [int]$MaxCount = 5,
    [switch]$NoRequestResume
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 确保 python 可用
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Host "[ERR] python not in PATH" -ForegroundColor Red; exit 1 }

function Test-ChromeDebugPort {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -Method GET -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $true
    } catch { return $false }
}

if (-not (Test-ChromeDebugPort)) {
    Write-Host "[0/2] Starting Chrome (port 9222)..." -ForegroundColor Yellow
    $chromeScript = Join-Path $ProjectRoot "skills_repo\plugin\scripts\launch_chrome_debug.ps1"
    if (-not (Test-Path $chromeScript)) { Write-Host "[ERR] launch_chrome_debug.ps1 not found" -ForegroundColor Red; exit 1 }
    & $chromeScript
    for ($i = 0; $i -lt 15; $i++) { Start-Sleep -Seconds 2; if (Test-ChromeDebugPort) { Write-Host "Chrome ready" -ForegroundColor Green; break } }
    if (-not (Test-ChromeDebugPort)) { Write-Host "[ERR] Chrome startup timeout" -ForegroundColor Red; exit 1 }
} else { Write-Host "[0/2] Chrome ready (9222)" -ForegroundColor Green }

Write-Host "[1/2] L3 Local boss_harvester" -ForegroundColor Cyan
Write-Host "  job_name: $JobName" -ForegroundColor White
Write-Host "  max_count: $MaxCount" -ForegroundColor White
Write-Host "  volume: ~/.jachin/client_volumes/global_resume_pool" -ForegroundColor White
Write-Host ""

try {
    if ($NoRequestResume) {
        python scripts\test_boss_harvester_l3_local.py --job "$JobName" --max $MaxCount --no-request
    } else {
        python scripts\test_boss_harvester_l3_local.py --job "$JobName" --max $MaxCount
    }
} catch {
    Write-Host "[ERR] Python script failed: $_" -ForegroundColor Red
    exit 1
}

$volPath = Join-Path $env:USERPROFILE ".jachin\client_volumes\global_resume_pool"
if (Test-Path $volPath) {
    $pdfs = Get-ChildItem -Path $volPath -Recurse -Filter "*.pdf" -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "L3 volume PDF count: $($pdfs.Count)" -ForegroundColor Cyan
}
