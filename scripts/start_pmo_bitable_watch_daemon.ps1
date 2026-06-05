# PMO 多维表变更监控 — 后台启动（事件驱动 + debounce）
# 1) Lark 长连接收 bitable 变更推送
# 2) debounce 检查器在 idle 后分析并推群

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = "python" }

$LogDir = Join-Path $env:USERPROFILE ".jachin\data"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$PidFile = Join-Path $LogDir "pmo_bitable_watch_long_connection.pid"

if (Test-Path $PidFile) {
    $oldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
        Write-Host "[pmo_bitable_watch] 长连接已在运行 PID=$oldPid"
        exit 0
    }
}

$lcScript = Join-Path $Root "scripts\run_pmo_bitable_watch_long_connection.py"
$proc = Start-Process -FilePath $Python -ArgumentList $lcScript, "--domain", "lark" -WorkingDirectory $Root -WindowStyle Hidden -PassThru
$proc.Id | Set-Content $PidFile -Encoding utf8
Write-Host "[pmo_bitable_watch] Lark 长连接已后台启动 PID=$($proc.Id)"
Write-Host "  日志: $LogDir\pmo_bitable_watch_long_connection.log"
Write-Host "  回调: $LogDir\pmo_bitable_watch_callbacks\latest.md"
Write-Host ""
Write-Host "请在 Lark 开放平台确认："
Write-Host "  - 订阅方式 = 使用长连接接收事件"
Write-Host "  - 已添加事件：多维表格记录变更 (drive.file.bitable_record_changed_v1)"
Write-Host "  - 已在目标多维表文档内订阅变更"
