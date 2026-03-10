# HR 透析镜 4 - Windows 编译脚本
# 用法: .\build.ps1
# 编译后需将 pkg/hr_analyzer4_bg.wasm 复制到 l3_node/skills/wasm_plugins/hr-analyzer4/main.wasm

$ErrorActionPreference = "Stop"
$projRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent

Write-Host ">>> Compiling hr-analyzer4..."
wasm-pack build --target web
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$src = Join-Path $PSScriptRoot "pkg\hr_analyzer4_bg.wasm"
$dest = Join-Path $projRoot "l3_node\skills\wasm_plugins\hr-analyzer4\main.wasm"
$destDir = Split-Path $dest -Parent
if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
Copy-Item $src $dest -Force
Write-Host ">>> Done: $dest"
