# 释放 L3 端口（WebSocket 18981 + HTTP 18990 系列），解决 Errno 10048
# 用法: .\scripts\kill_l3_ports.ps1
# 若端口仍被占，可先执行: .\scripts\kill_l3_processes.ps1（结束残留 l3_node 进程）

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$ports = @(18981..18999)  # WebSocket 18981+, HTTP 技能 API 18990-18999
foreach ($p in $ports) {
    & (Join-Path $ScriptDir "kill_port.ps1") -Port $p
}
Write-Host "[L3] 已释放 18981-18999 端口" -ForegroundColor Green
