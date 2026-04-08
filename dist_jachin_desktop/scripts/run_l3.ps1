# L3 独立运行脚本 - 在 PowerShell 中查看完整日志
# 用法: .\scripts\run_l3.ps1  或  .\scripts\run_l3.ps1 --ws-only
# 配对后使用 --gateway；未配对用 --ws-only（需 .env 有 DASHSCOPE_API_KEY）
# 打包模式：无 Python 时自动调用 bin/l3_node-*.exe

$ErrorActionPreference = "Stop"
# 修复 Windows 控制台中文乱码（UTF-8）
try { chcp 65001 | Out-Null } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
# 推断应用根：脚本在 scripts/ 下，appRoot 为父目录；若脚本被复制到 Temp 执行，则用当前目录
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
# 便携包：当前目录或父目录含 bin 时优先使用（避免脚本被复制到 Temp 时路径错误）
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
# 与 start-layer3 一致：避免 l3_mcp_cache 旧 HR 包盖过仓库 recruitment_scheduler
$env:JACHIN_APP_ROOT = $appRoot
$env:JACHIN_DEV_HR_FIRST = "1"

# 便携包模式：日志写入 logs/，应用根目录明确，便于移植
$binDir = Join-Path $appRoot "bin"
if ($binDir -and (Test-Path $binDir)) {
    $logsDir = Join-Path $appRoot "logs"
    if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
    $env:JACHIN_LOG_DIR = $logsDir
    $env:JACHIN_APP_ROOT = $appRoot
}

$mode = "--gateway"
if ($args -contains "--ws-only") { $mode = "--ws-only" }

# 优先 Python，否则用 exe（打包模式）
$l3Exe = $null
if ($binDir -and (Test-Path $binDir)) {
    $l3Exe = Get-ChildItem -Path $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
$hasPython = $null -ne (Get-Command python -ErrorAction SilentlyContinue)

if ($hasPython -and (Test-Path (Join-Path $appRoot "l3_node"))) {
    Set-Location $appRoot
    Write-Host "[L3] Python mode, logs to terminal (cwd=$appRoot)" -ForegroundColor Cyan
    $tlog = Join-Path $env:USERPROFILE ".jachin\l3_powershell_transcript.log"
    $tDir = Split-Path $tlog -Parent
    if (-not (Test-Path $tDir)) { New-Item -ItemType Directory -Path $tDir -Force | Out-Null }
    Write-Host "[L3] PS transcript: $tlog" -ForegroundColor Gray
    try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
    $trOn = $false
    try {
        Start-Transcript -Path $tlog -Force -ErrorAction Stop | Out-Null
        $trOn = $true
    } catch {
        Write-Host "[L3] Start-Transcript 不可用，跳过 PS 抄本" -ForegroundColor DarkYellow
    }
    try {
        python -m l3_node $mode
    } finally {
        if ($trOn) { try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {} }
    }
} elseif ($l3Exe) {
    Set-Location $appRoot
    Write-Host "[L3] Exe mode (cwd=$appRoot)" -ForegroundColor Cyan
    Write-Host "[L3] Exe has no console. Polling health at http://127.0.0.1:18991/api/health" -ForegroundColor Gray
    $p = Start-Process -FilePath $l3Exe.FullName -ArgumentList $mode -PassThru -NoNewWindow
    $healthShown = $false
    $ports = 18991, 18990, 18992, 18993, 18994
    $waitCount = 0
    $logChecked = $false
    try {
        while (-not $p.HasExited) {
            if (-not $logChecked) {
                $logPath = if ($env:JACHIN_LOG_DIR) { Join-Path $env:JACHIN_LOG_DIR "l3_debug.log" } else { Join-Path $appRoot "l3_debug.log" }
                if (Test-Path $logPath) {
                    $logChecked = $true
                    Write-Host "[L3] Debug log: $logPath" -ForegroundColor Gray
                }
            }
            if (-not $healthShown) {
                foreach ($port in $ports) {
                    try {
                        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
                        if ($r -and $r.ok) {
                            $healthShown = $true
                            $l2 = if ($r.l2_reachable) { "reachable" } else { "unreachable" }
                            $pair = if ($r.l2_paired) { "paired" } else { "pending" }
                            Write-Host "[L3] Health OK | L2 $l2 | $pair | node=$($r.node_id)" -ForegroundColor Green
                            break
                        }
                    } catch {}
                }
                $waitCount++
                if ($waitCount -eq 5) { Write-Host "[L3] Still waiting for L3 HTTP... (if exe crashed, rebuild with: python scripts/build_l3_sidecar.py --force)" -ForegroundColor Yellow }
            }
            Start-Sleep -Seconds 2
        }
        $code = $p.ExitCode
        Write-Host "[L3] Process exited (code=$code)" -ForegroundColor Gray
        if ($code -ne 0 -and -not $healthShown) { Write-Host "[L3] L3 may have crashed. Rebuild: python scripts/build_l3_sidecar.py --force" -ForegroundColor Yellow }
    } finally {
        if (-not $p.HasExited) {
            $p.Kill()
            Write-Host "[L3] Killed by user (Ctrl+C)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Error "Python (l3_node) or bin/l3_node*.exe not found. Run Build first."
}
