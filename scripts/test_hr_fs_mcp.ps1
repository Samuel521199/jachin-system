# hr-filesystem MCP 功能测试
# 前置：L2 已启动 (python -m core.main)
# 用法：.\scripts\test_hr_fs_mcp.ps1

# 强制 UTF-8 输出，避免中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

# Python 输出 UTF-8
$env:PYTHONIOENCODING = "utf-8"

$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

$scriptPath = Join-Path $root "scripts\test_hr_fs_mcp.py"
if (-not (Test-Path $scriptPath)) {
    Write-Host "[FAIL] Script not found: $scriptPath" -ForegroundColor Red
    exit 1
}

Set-Location $root

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " hr-filesystem MCP Test" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

python $scriptPath
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -eq 0) {
    Write-Host ">>> All tests passed" -ForegroundColor Green
} else {
    Write-Host ">>> Test failed (exit $exitCode)" -ForegroundColor Red
    Write-Host "Ensure L2 is running: python -m core.main" -ForegroundColor Yellow
}
Write-Host ""
exit $exitCode
