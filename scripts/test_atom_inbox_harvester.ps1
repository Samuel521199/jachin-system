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
