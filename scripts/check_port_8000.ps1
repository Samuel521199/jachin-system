# 检查端口 8000 的详细状态（已弃用，请使用 check_port.ps1）
# 此脚本保留用于向后兼容，建议使用: .\scripts\check_port.ps1 8000

Write-Host "[DEPRECATED] This script is deprecated. Use: .\scripts\check_port.ps1 8000" -ForegroundColor Yellow
Write-Host ""

# 调用通用脚本
& "$PSScriptRoot\check_port.ps1" -Port 8000
