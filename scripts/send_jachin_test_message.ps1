# 触发 Jachin 右下角陪伴弹窗（不模拟键盘）
# 前提：Jachin Desktop 正在运行，且为包含本脚本逻辑的较新版本
param(
  [string]$Title = "Jachin · 陪伴测试",
  [string]$Body = "这是一条右下角弹窗测试。时间：$(Get-Date -Format 'HH:mm:ss')",
  [int]$Count = 1,
  [int]$IntervalMs = 700
)

$ErrorActionPreference = "Stop"

if ($Count -lt 1) { $Count = 1 }
if ($IntervalMs -lt 50) { $IntervalMs = 50 }

$proc = Get-Process -Name "jachin-desktop" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) {
  throw "Jachin Desktop 未运行。请先启动桌面端（jachin-desktop）。"
}

$exe = $proc.Path
if (-not $exe -or -not (Test-Path -LiteralPath $exe)) {
  throw "无法定位 jachin-desktop.exe 路径。"
}

$dir = Join-Path $env:USERPROFILE ".jachin"
$ping = Join-Path $dir "desktop_sentry_ping.json"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
for ($i = 1; $i -le $Count; $i++) {
  $itemBody = if ($Count -eq 1) {
    $Body
  } else {
    "$Body（第 $i/$Count 条）"
  }

  # 方式 1：通过单实例 CLI 参数，让已运行进程立刻弹窗（推荐）
  Start-Process -FilePath $exe -ArgumentList @("--jachin-sentry-test", $Title, $itemBody)

  # 方式 2：写入 Ping 文件（桌面端每秒轮询，作为兜底）
  $payload = @{
    seq   = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + $i
    title = $Title
    body  = $itemBody
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($ping, ($payload | ConvertTo-Json -Compress), $utf8NoBom)

  if ($i -lt $Count) {
    Start-Sleep -Milliseconds $IntervalMs
  }
}

Write-Host "已触发哨兵测试 -> $exe（共 $Count 条，间隔 ${IntervalMs}ms）"
Write-Host "若仍未弹出：请先关闭 Jachin Desktop，再在 clients/desktop 执行 npm run tauri:dev 重新启动后再试本脚本。"
