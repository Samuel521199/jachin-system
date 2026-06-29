# 释放 L3 端口（WebSocket 18981 + HTTP 18991 系列），解决 Errno 10048
# 用法: .\scripts\kill_l3_ports.ps1
# 若端口仍被占，可先执行: .\scripts\kill_l3_processes.ps1（结束残留 l3_node 进程）

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force -ErrorAction SilentlyContinue

function Stop-TcpPortListener {
    param([int]$Port)
    $connections = @(netstat -ano 2>$null | Select-String ":$Port\s" | Select-String "LISTENING")
    if (-not $connections.Count) { return $false }
    $processIds = $connections | ForEach-Object { $_.ToString().Split()[-1] } | Select-Object -Unique
    foreach ($processId in $processIds) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
        } catch {
            Start-Process -FilePath "taskkill" -ArgumentList "/F", "/PID", $processId -Wait -NoNewWindow -ErrorAction SilentlyContinue | Out-Null
        }
    }
    return $true
}

$ports = @(18981..18999)
foreach ($p in $ports) {
    if (Stop-TcpPortListener -Port $p) {
        Write-Host "  已尝试释放端口 $p" -ForegroundColor Gray
    }
}
Write-Host "[L3] 已释放 18981-18999 端口" -ForegroundColor Green
