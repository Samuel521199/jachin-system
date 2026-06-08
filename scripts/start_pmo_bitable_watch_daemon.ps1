# PMO 多维表变更监控 — 后台启动（事件驱动 + debounce）
# 1) Lark 长连接收 bitable 变更推送
# 2) debounce 检查器在 idle 后分析并推群
#
# 用法：
#   .\scripts\start_pmo_bitable_watch_daemon.ps1           # 后台启动
#   .\scripts\start_pmo_bitable_watch_daemon.ps1 -Foreground  # 前台运行（看报错）

param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) { $Python = "python" }

$LogDir = Join-Path $env:USERPROFILE ".jachin\data"
$LogFile = Join-Path $LogDir "pmo_bitable_watch_long_connection.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$PidFile = Join-Path $LogDir "pmo_bitable_watch_long_connection.pid"

if (Test-Path $PidFile) {
    $oldPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1).ToString().Trim()
    if ($oldPid -match '^\d+$' -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        Write-Host "[pmo_bitable_watch] 长连接已在运行 PID=$oldPid"
        if (-not $Foreground) { Read-Host "按 Enter 关闭此窗口" }
        exit 0
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$lcScript = Join-Path $Root "scripts\run_pmo_bitable_watch_long_connection.py"
$args = @($lcScript, "--domain", "lark")

if ($Foreground) {
    Write-Host "[pmo_bitable_watch] 前台启动（Ctrl+C 退出）…"
    Write-Host "  日志: $LogFile"
    & $Python @args
    exit $LASTEXITCODE
}

$proc = Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $Root -WindowStyle Hidden -PassThru
$proc.Id | Set-Content $PidFile -Encoding utf8
Write-Host "[pmo_bitable_watch] Lark 长连接已后台启动 PID=$($proc.Id)"
Write-Host "  日志: $LogFile"
Write-Host "  回调: $LogDir\pmo_bitable_watch_callbacks\latest.md"
Write-Host ""

Start-Sleep -Seconds 3
if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
    Write-Host "[pmo_bitable_watch] 错误：进程已退出，最近日志：" -ForegroundColor Red
    if (Test-Path $LogFile) {
        Get-Content $LogFile -Tail 25 -ErrorAction SilentlyContinue
    } else {
        Write-Host "  （日志文件尚未生成）"
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Read-Host "按 Enter 关闭此窗口"
    exit 1
}

Write-Host "请在 Lark 开放平台确认："
Write-Host "  - 订阅方式 = 使用长连接接收事件"
Write-Host "  - 已添加事件：多维表格记录变更 (drive.file.bitable_record_changed_v1)"
Write-Host "  - 已在目标多维表文档内订阅变更"
Write-Host ""
Read-Host "按 Enter 关闭此窗口"
