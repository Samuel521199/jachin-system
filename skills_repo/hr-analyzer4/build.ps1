# HR 透析镜 4 - Windows 编译脚本
# 用法: .\build.ps1
# 使用 cargo + wasm32-unknown-unknown（无需 wasm-pack）
# 输出: target/wasm32-unknown-unknown/release/hr_analyzer4.wasm -> l3_node/skills/wasm_plugins/hr-analyzer4/main.wasm

$ErrorActionPreference = "Stop"
$projRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

Write-Host ">>> Compiling hr-analyzer4 (cargo --target wasm32-unknown-unknown)..."
Push-Location $PSScriptRoot
cargo build --target wasm32-unknown-unknown --release
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

$src = Join-Path $PSScriptRoot "target\wasm32-unknown-unknown\release\hr_analyzer4.wasm"
$dest = Join-Path $projRoot "l3_node\skills\wasm_plugins\hr-analyzer4\main.wasm"
$destDir = Split-Path $dest -Parent
if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
Copy-Item $src $dest -Force
Write-Host ">>> Done: $dest"
