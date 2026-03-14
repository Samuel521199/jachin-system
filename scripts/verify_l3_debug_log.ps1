# 验证 L3 调试日志是否生成
# 用法: 从项目根运行 .\scripts\verify_l3_debug_log.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[Verify] Testing early_log (cwd=$root)..." -ForegroundColor Cyan
python -c "import sys; sys.path.insert(0, '.'); from l3_node.early_log import setup_early_logging; p=setup_early_logging(); print('Log path:', p)"

$logPath = Join-Path $root "l3_debug.log"
if (Test-Path $logPath) {
    Write-Host "[Verify] OK - Log exists: $logPath" -ForegroundColor Green
    Get-Content $logPath -Head 8
} else {
    $jachinLog = Join-Path $env:USERPROFILE ".jachin\l3_debug.log"
    if (Test-Path $jachinLog) {
        Write-Host "[Verify] OK - Log in ~/.jachin: $jachinLog" -ForegroundColor Green
        Get-Content $jachinLog -Head 8
    } else {
        Write-Host "[Verify] Log not found" -ForegroundColor Red
    }
}
