# 释放桌面客户端 Vite 开发端口 1421（便于 tauri dev 重启）
$port = 1421
$conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if (-not $conn) {
  Write-Host "Port $port is not in use."
  exit 0
}
$pids = $conn.OwningProcess | Sort-Object -Unique
foreach ($pid in $pids) {
  Write-Host "Killing process $pid (using port $port)..."
  Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
}
Write-Host "Port $port released."
