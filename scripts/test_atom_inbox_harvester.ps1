# =============================================================================
# atom_inbox_harvester - 已迁移至 L3 本地伴生 MCP
# 本脚本转发至 L3 本地测试（不再调用 L2）
# Usage: .\scripts\test_atom_inbox_harvester.ps1 [-JobName "Java_杭州 4-6K"] [-MaxCount 20]
# =============================================================================

param(
    [string]$JobName = "Java_杭州 4-6K",
    [int]$MaxCount = 20,
    [switch]$NoRequestResume
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$L3Script = Join-Path $ScriptDir "test_boss_harvester_l3_local.ps1"
if (-not (Test-Path $L3Script)) {
    Write-Host "[ERR] L3 script not found: $L3Script" -ForegroundColor Red
    exit 1
}

Write-Host "atom_inbox_harvester -> L3 local (job=$JobName, max=$MaxCount)" -ForegroundColor Cyan
Write-Host ""

# 转发至 L3 本地测试（含 Chrome 检测、数据卷 ~/.jachin/client_volumes/）
& $L3Script -JobName $JobName -MaxCount $MaxCount -NoRequestResume:$NoRequestResume
exit $LASTEXITCODE

<#
.SYNOPSIS
  以下为原 L2 调用逻辑，已废弃。atom_inbox_harvester 已迁移至 L3 本地伴生 MCP。
#>
function Test-ChromeDebugPort {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -Method GET -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-ChromeDebugPort)) {
    Write-Host "[0/3] Starting Chrome (port 9222)..." -ForegroundColor Yellow
    $chromeScript = Join-Path $ProjectRoot "skills_repo\plugin\scripts\launch_chrome_debug.ps1"
    if (-not (Test-Path $chromeScript)) {
        Write-Host "[ERR] launch_chrome_debug.ps1 not found" -ForegroundColor Red
        exit 1
    }
    & $chromeScript
    Write-Host "Waiting for Chrome..." -ForegroundColor Gray
    $maxWait = 15
    for ($i = 0; $i -lt $maxWait; $i++) {
        Start-Sleep -Seconds 2
        if (Test-ChromeDebugPort) {
            Write-Host "Chrome ready" -ForegroundColor Green
            break
        }
    }
    if (-not (Test-ChromeDebugPort)) {
        Write-Host "[ERR] Chrome startup timeout" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[0/3] Chrome ready (9222)" -ForegroundColor Green
}

$body = @{
    tool_name = "atom_inbox_harvester"
    arguments = @{
        job_name = $JobName
        max_count = $MaxCount
        target_volume = "global_resume_pool"
        filter_tab = ""
        request_if_no_resume = (-not $NoRequestResume)
    }
} | ConvertTo-Json -Depth 3 -Compress

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  atom_inbox_harvester" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  job_name: $JobName" -ForegroundColor White
Write-Host "  max_count: $MaxCount" -ForegroundColor White
Write-Host "  request_if_no_resume: $(-not $NoRequestResume)" -ForegroundColor White
Write-Host "  target_volume: global_resume_pool" -ForegroundColor White
Write-Host ""

try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:18888/health" -Method GET -UseBasicParsing -TimeoutSec 3
} catch {
    Write-Host "[ERR] L2 not running (18888). Run .\scripts\run-backend.ps1 first" -ForegroundColor Red
    exit 1
}

Write-Host "[1/3] L2 ready" -ForegroundColor Green
Write-Host "[2/3] Calling atom_inbox_harvester..." -ForegroundColor Yellow
$r = Invoke-WebRequest -Uri "http://127.0.0.1:18888/api/v2/mcp/invoke" -Method POST `
    -ContentType "application/json; charset=utf-8" -Body $body `
    -UseBasicParsing -TimeoutSec 450

$json = $r.Content | ConvertFrom-Json
$result = $json.result | ConvertFrom-Json

Write-Host ""
Write-Host "[3/3] Response:" -ForegroundColor Green
Write-Host $r.Content
Write-Host ""

$volPath = Join-Path $env:USERPROFILE ".jachin\volumes\global_resume_pool"
if (Test-Path $volPath) {
    $pdfs = Get-ChildItem -Path $volPath -Recurse -Filter "*.pdf" -ErrorAction SilentlyContinue
    Write-Host "  PDF count in volume: $($pdfs.Count)" -ForegroundColor Cyan
    if ($pdfs.Count -gt 0) {
        $pdfs | ForEach-Object { Write-Host "    - $($_.Name)" -ForegroundColor Gray }
    }
} else {
    Write-Host "  Volume dir not created yet" -ForegroundColor Gray
}

if ($result.status -eq "success") {
    Write-Host ""
    $dl = if ($result.downloaded_count) { $result.downloaded_count } else { 0 }
    $rq = if ($result.requested_count) { $result.requested_count } else { 0 }
    Write-Host "Downloaded: $dl | Requested: $rq" -ForegroundColor Green
} elseif ($result.error) {
    Write-Host ""
    Write-Host "Error:" -ForegroundColor Red
    Write-Host $result.error -ForegroundColor Red
}
