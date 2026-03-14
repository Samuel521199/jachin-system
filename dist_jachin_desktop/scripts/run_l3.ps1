# L3 独立运行脚本 - 在 PowerShell 中查看完整日志
# 用法: .\scripts\run_l3.ps1  或  .\scripts\run_l3.ps1 --ws-only
# 配对后使用 --gateway；未配对用 --ws-only（需 .env 有 DASHSCOPE_API_KEY）
# 打包模式：无 Python 时自动调用 bin/l3_node-*.exe

$ErrorActionPreference = "Stop"
# 修复 Windows 控制台中文乱码（UTF-8）
try { chcp 65001 | Out-Null } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$appRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $appRoot "l3_node")) -and -not (Test-Path (Join-Path $appRoot "bin"))) {
    $appRoot = $PSScriptRoot
    while ($appRoot -and -not (Test-Path (Join-Path $appRoot "l3_node")) -and -not (Test-Path (Join-Path $appRoot "bin"))) {
        $appRoot = Split-Path -Parent $appRoot
    }
}
if (-not $appRoot) { Write-Error "Project root (l3_node or bin) not found" }

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"
$env:LOG_LEVEL = "DEBUG"

$mode = "--gateway"
if ($args -contains "--ws-only") { $mode = "--ws-only" }

# 优先 Python，否则用 exe（打包模式）
$binDir = Join-Path $appRoot "bin"
$l3Exe = Get-ChildItem -Path $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$hasPython = $null -ne (Get-Command python -ErrorAction SilentlyContinue)

if ($hasPython -and (Test-Path (Join-Path $appRoot "l3_node"))) {
    Set-Location $appRoot
    Write-Host "[L3] Python mode, logs to terminal (cwd=$appRoot)" -ForegroundColor Cyan
    python -m l3_node $mode
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
                $logPath = Join-Path $appRoot "l3_debug.log"
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
