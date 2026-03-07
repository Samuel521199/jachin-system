# Jachin System Pilot - Windows 编译脚本
# 用法: .\build.ps1

$ErrorActionPreference = "Stop"
$target = "wasm32-unknown-unknown"
$crate = "jachin_system_pilot"
$out = "main.wasm"

Write-Host ">>> Compiling jachin-system-pilot..."
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
rustup target add $target 2>&1 | Out-Null
$ErrorActionPreference = $prevErr
cargo build --target $target --release
Copy-Item "target\$target\release\$crate.wasm" $out
Write-Host ">>> Done: $out"
