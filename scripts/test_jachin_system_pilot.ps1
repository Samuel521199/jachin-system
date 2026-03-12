# jachin-system-pilot 本地测试 - 一键运行
# 用法: .\scripts\test_jachin_system_pilot.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

Set-Location $root

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " jachin-system-pilot 本地测试" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 编译 wasm
$pilotDir = Join-Path $root "skills_repo\jachin-system-pilot"
if (-not (Test-Path $pilotDir)) {
    Write-Host "[FAIL] 未找到 skills_repo\jachin-system-pilot" -ForegroundColor Red
    exit 1
}
Set-Location $pilotDir
Write-Host "[1/3] 编译 wasm..." -ForegroundColor Yellow
cargo build --target wasm32-unknown-unknown --release 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] cargo build 失败" -ForegroundColor Red
    exit 1
}
Copy-Item "target\wasm32-unknown-unknown\release\jachin_system_pilot.wasm" -Destination "main.wasm" -Force
Write-Host "      完成" -ForegroundColor Green

# 2. 复制到 wasm_plugins
$wasmPlugins = Join-Path $root "l3_node\skills\wasm_plugins"
$null = New-Item -ItemType Directory -Force -Path $wasmPlugins
Copy-Item "main.wasm" -Destination (Join-Path $wasmPlugins "main.wasm") -Force
Copy-Item "plugin.json" -Destination (Join-Path $wasmPlugins "plugin.json") -Force
Write-Host "[2/3] 已复制到 l3_node\skills\wasm_plugins" -ForegroundColor Green

# 3. 运行测试
Set-Location $root
Write-Host "[3/3] 运行测试..." -ForegroundColor Yellow
Write-Host ""
$out = python scripts\test_jachin_system_pilot_local.py 2>&1
$exitCode = $LASTEXITCODE

# 输出结果
$out | Where-Object { $_ -match "PASS|FAIL|OK|====|jachin-system-pilot" } | ForEach-Object {
    if ($_ -match "PASS") { Write-Host $_ -ForegroundColor Green }
    elseif ($_ -match "FAIL") { Write-Host $_ -ForegroundColor Red }
    else { Write-Host $_ }
}

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host ">>> 全部测试通过" -ForegroundColor Green
} else {
    Write-Host ">>> 测试失败" -ForegroundColor Red
}
Write-Host ""
exit $exitCode
