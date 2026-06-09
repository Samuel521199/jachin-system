# L3 独立运行脚本 - 在 PowerShell 中查看完整日志
# 用法: 默认 --ws-only（不依赖 L2）。需 L2 配对/心跳时: .\scripts\run_l3.ps1 --gateway
# 需 .env 有 DASHSCOPE_API_KEY（或 OPENAI_API_KEY）
# 打包模式：无 Python 时自动调用 bin/l3_node-*.exe

$ErrorActionPreference = "Stop"
try { chcp 65001 | Out-Null } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Get-L3Health {
    param([int[]]$Ports = @(18991, 18990, 18992, 18993, 18994))
    foreach ($port in $Ports) {
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2 -ErrorAction Stop
            if ($r -and $r.ok) {
                return @{ ok = $true; port = $port; body = $r }
            }
        } catch {}
    }
    return $null
}

function Show-L3LogTail {
    param([string]$LogPath, [int]$Lines = 35)
    if (-not (Test-Path $LogPath)) {
        Write-Host "[L3] 日志文件不存在: $LogPath" -ForegroundColor Yellow
        return
    }
    Write-Host "[L3] ---- 日志末尾 ($LogPath) ----" -ForegroundColor DarkYellow
    Get-Content -Path $LogPath -Tail $Lines -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_ -match "ERROR|WARNING|Traceback|启动跳过|已有 L3|crashed|ImportError|未检测到任何 API Key") {
            Write-Host $_ -ForegroundColor Red
        } else {
            Write-Host $_
        }
    }
    Write-Host "[L3] ---- end log ----" -ForegroundColor DarkYellow
}

# 推断应用根
$scriptPath = $PSCommandPath
if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Path }
$scriptDir = if ($scriptPath) { Split-Path -Parent $scriptPath } else { $PSScriptRoot }
$appRoot = if ($scriptDir) { Split-Path -Parent $scriptDir } else { $null }
if (-not $appRoot -or (-not (Test-Path (Join-Path $appRoot "l3_node")) -and -not (Test-Path (Join-Path $appRoot "bin")))) {
    $appRoot = $scriptDir
    while ($appRoot -and -not (Test-Path (Join-Path $appRoot "l3_node")) -and -not (Test-Path (Join-Path $appRoot "bin"))) {
        $appRoot = Split-Path -Parent $appRoot
    }
}
$cwd = (Get-Location).Path
foreach ($candidate in @($cwd, (Split-Path $cwd -Parent), (Split-Path (Split-Path $cwd -Parent) -Parent))) {
    if ($candidate -and (Test-Path (Join-Path $candidate "bin"))) { $appRoot = $candidate; break }
}
if (-not $appRoot -or -not (Test-Path (Join-Path $appRoot "bin"))) { $appRoot = $cwd }
if (-not (Test-Path (Join-Path $appRoot "l3_node")) -and -not (Test-Path (Join-Path $appRoot "bin"))) {
    Write-Error "Project root (l3_node or bin) not found. Current appRoot=$appRoot"
}

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"
$env:LOG_LEVEL = "DEBUG"
$env:JACHIN_L3_DEEP_LOG = "1"
$env:JACHIN_APP_ROOT = $appRoot
$env:JACHIN_DEV_HR_FIRST = "1"
$env:JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL = "1"

$binDir = Join-Path $appRoot "bin"
$logsDir = Join-Path $appRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
$env:JACHIN_LOG_DIR = $logsDir
$logPath = Join-Path $logsDir "l3_debug.log"

# 模式：显式 --ws-only 优先；否则有 l2_gateway_config 则 --gateway（与 Desktop 一致）
$mode = "--ws-only"
if ($args -contains "--gateway") {
    $mode = "--gateway"
} elseif ($args -contains "--ws-only") {
    $mode = "--ws-only"
} else {
    $gwCfg = Join-Path $env:USERPROFILE ".jachin\l2_gateway_config.json"
    if (Test-Path $gwCfg) {
        try {
            $gw = Get-Content $gwCfg -Raw | ConvertFrom-Json
            if ($gw.l2_base_url) {
                $mode = "--gateway"
                $env:L2_BASE_URL = [string]$gw.l2_base_url
            }
        } catch {}
    }
}

$existing = Get-L3Health
if ($existing) {
    $r = $existing.body
    $l2 = if ($r.l2_reachable) { "reachable" } else { "unreachable" }
    $pair = if ($r.l2_paired) { "paired" } else { "pending" }
    Write-Host "[L3] 本机已有 L3 在运行 (port=$($existing.port)) | L2 $l2 | $pair | node=$($r.node_id)" -ForegroundColor Green
    Write-Host "[L3] 若 Jachin Desktop 已打开，无需再运行本脚本；重复启动会因单实例锁退出。" -ForegroundColor Gray
    exit 0
}

$l3Exe = $null
if ($binDir -and (Test-Path $binDir)) {
    $l3Exe = Get-ChildItem -Path $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
$hasPython = $null -ne (Get-Command python -ErrorAction SilentlyContinue)

if ($hasPython -and (Test-Path (Join-Path $appRoot "l3_node"))) {
    Set-Location $appRoot
    Write-Host "[L3] Python mode (cwd=$appRoot, mode=$mode)" -ForegroundColor Cyan
    python -m l3_node $mode
} elseif ($l3Exe) {
    Set-Location $appRoot
    Write-Host "[L3] Exe mode (cwd=$appRoot, mode=$mode)" -ForegroundColor Cyan
    Write-Host "[L3] 以下为正常提示（不是报错）：侧车无控制台，脚本会轮询健康检查并写入 logs\l3_debug.log" -ForegroundColor Gray
    Write-Host "[L3] Health: http://127.0.0.1:18991/api/health | Log: $logPath" -ForegroundColor Gray

    if (-not (Test-Path (Join-Path $appRoot ".env"))) {
        Write-Host "[L3] 警告: 安装目录缺少 .env（请从 .env.example 复制并填写 DASHSCOPE_API_KEY）" -ForegroundColor Yellow
    }

    $p = Start-Process -FilePath $l3Exe.FullName -ArgumentList $mode -PassThru -NoNewWindow -WorkingDirectory $appRoot
    $healthShown = $false
    $waitCount = 0
    try {
        while (-not $p.HasExited) {
            if (-not $healthShown) {
                $h = Get-L3Health
                if ($h) {
                    $healthShown = $true
                    $r = $h.body
                    $l2 = if ($r.l2_reachable) { "reachable" } else { "unreachable" }
                    $pair = if ($r.l2_paired) { "paired" } else { "pending" }
                    Write-Host "[L3] Health OK (port=$($h.port)) | L2 $l2 | $pair | node=$($r.node_id)" -ForegroundColor Green
                    Write-Host "[L3] 长连接/聊天已就绪；按 Ctrl+C 结束本脚本（会终止侧车进程）" -ForegroundColor Gray
                } else {
                    $waitCount++
                    if ($waitCount -eq 8) {
                        Write-Host "[L3] 仍在等待 HTTP 就绪（首次启动可能 30~90s，Memory Nexus 加载较慢）…" -ForegroundColor Yellow
                        if (Test-Path $logPath) { Show-L3LogTail -LogPath $logPath -Lines 15 }
                    }
                    if ($waitCount -eq 25) {
                        Write-Host "[L3] 长时间未就绪：请查看下方日志；常见原因：Desktop 已占用 L3、缺少 API Key、需重建侧车" -ForegroundColor Yellow
                        Show-L3LogTail -LogPath $logPath
                    }
                }
            }
            Start-Sleep -Seconds 2
        }
        $code = $p.ExitCode
        Write-Host "[L3] Process exited (code=$code)" -ForegroundColor $(if ($code -eq 0) { "Gray" } else { "Red" })
        if (-not $healthShown) {
            Write-Host "[L3] 启动失败或未通过健康检查。常见原因：" -ForegroundColor Red
            Write-Host "  1) Jachin Desktop 已在运行（单实例：请先关 Desktop 或勿重复 run_l3）" -ForegroundColor Yellow
            Write-Host "  2) 安装目录 .env 缺少 DASHSCOPE_API_KEY / OPENAI_API_KEY" -ForegroundColor Yellow
            Write-Host "  3) bin\l3_node-*.exe 缺失或被杀软删除 → 重新安装" -ForegroundColor Yellow
            Write-Host "  4) 侧车版本过旧 → 重新 build_l3_sidecar 并打包" -ForegroundColor Yellow
            Show-L3LogTail -LogPath $logPath
        }
        if ($Host.Name -eq "ConsoleHost") {
            Read-Host "按 Enter 关闭此窗口"
        }
    } finally {
        if (-not $p.HasExited) {
            $p.Kill()
            Write-Host "[L3] Killed by user (Ctrl+C)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[ERR] 未找到 Python 模块 l3_node，也未找到 bin\l3_node*.exe" -ForegroundColor Red
    Write-Host "      安装目录: $appRoot" -ForegroundColor Gray
    Write-Host "      请确认 NSIS 安装后存在 bin\l3_node-x86_64-pc-windows-msvc.exe（重新打包并安装）" -ForegroundColor Yellow
    if ($Host.Name -eq "ConsoleHost") { Read-Host "按 Enter 关闭" }
    exit 1
}
