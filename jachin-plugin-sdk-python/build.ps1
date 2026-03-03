# Jachin Plugin SDK (Python) - Windows 编译脚本
# 用法: .\build.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "`n🔧 Jachin Plugin SDK - 编译 Python 插件为 Wasm...`n" -ForegroundColor Cyan

# 检查 py2wasm
$null = Get-Command py2wasm -ErrorAction SilentlyContinue
if (-not $?) {
    Write-Host "❌ 请先安装 py2wasm: pip install py2wasm" -ForegroundColor Red
    exit 1
}

# 创建 dist
New-Item -ItemType Directory -Force -Path dist | Out-Null

# 编译
Push-Location src
try {
    py2wasm main.py -o ..\dist\plugin.wasm
} finally {
    Pop-Location
}

Copy-Item plugin.json dist\
Write-Host "`n✅ 编译完成: dist\plugin.wasm" -ForegroundColor Green
Write-Host "📤 上传至 Jachin Nexus 控制台: dist\plugin.wasm + dist\plugin.json`n" -ForegroundColor Gray
