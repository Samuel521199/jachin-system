# =============================================================================
# L3 本地伴生 MCP - Boss 直聘收网（Stdio 模式）
# 通过 stdin/stdout 与 L3 主进程通信，绝不启动 HTTP 服务
# 用法: .\scripts\run_local_mcp_boss_harvester.ps1
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "L3 Local MCP: boss_harvester (stdio)" -ForegroundColor Cyan
Write-Host "  Data volume: ~/.jachin/client_volumes/" -ForegroundColor Gray
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

python -m l3_client.local_mcps.boss_harvester.server
