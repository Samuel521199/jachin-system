# L3 独立运行脚本 - 在 PowerShell 中查看完整日志
# 用法: .\scripts\run_l3.ps1  或  .\scripts\run_l3.ps1 --gateway
# 配对后使用 --gateway；未配对用 --ws-only（需 .env 有 DASHSCOPE_API_KEY）
# --gateway 需 L2 已启动（python -m core.main），否则无法分配 Key

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "l3_node"))) {
    $root = $PSScriptRoot
    while ($root -and -not (Test-Path (Join-Path $root "l3_node"))) {
        $root = Split-Path -Parent $root
    }
}
if (-not $root) { Write-Error "未找到项目根目录 (l3_node)" }

Set-Location $root
$env:PYTHONUNBUFFERED = "1"
$env:LOG_LEVEL = "DEBUG"

$mode = "--gateway"
if ($args -contains "--ws-only") { $mode = "--ws-only" }

Write-Host "[L3] 启动 L3 节点，日志将输出到本终端 (cwd=$root)" -ForegroundColor Cyan
python -m l3_node $mode
