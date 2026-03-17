# 清理残留的 L3 进程与端口
# 用法: .\scripts\kill_l3_processes.ps1
#       .\scripts\kill_l3_processes.ps1 -NoPause  # 不等待 Enter，适合脚本调用

param([switch]$NoPause)
$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = ".\scripts" }

Write-Host ""
Write-Host '[L3] 清理残留 L3 进程与端口...' -ForegroundColor Cyan
Write-Host ""

# 1. 结束 L3 相关进程（python l3_node + l3_node-*.exe Sidecar）
$killed = 0
try {
    $allProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    foreach ($p in $allProcs) {
        $name = $p.Name
        $cmd = if ($p.CommandLine) { $p.CommandLine } else { "" }
        $isL3 = ($name -eq "python.exe" -and $cmd -match "l3_node") -or ($name -match "^l3_node-.*\.exe$")
        if ($isL3) {
            Write-Host "  结束: $name (PID $($p.ProcessId))" -ForegroundColor Yellow
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $killed++
        }
    }
} catch {
    Write-Host "  查询进程时出错: $_" -ForegroundColor Red
}
if ($killed -eq 0) {
    Write-Host "  未发现 L3 进程" -ForegroundColor Gray
}

# 2. 查找并释放 18981-18999 中被占用的端口
Write-Host ""
$portsInUse = @()
foreach ($port in 18981..18999) {
    $line = netstat -ano 2>$null | Select-String ":$port\s" | Select-String "LISTENING"
    if ($line) { $portsInUse += $port }
}

if ($portsInUse.Count -gt 0) {
    Write-Host "  释放端口: $($portsInUse -join ', ')" -ForegroundColor Yellow
    foreach ($p in $portsInUse) {
        & (Join-Path $ScriptDir "kill_port.ps1") -Port $p 2>$null | Out-Null
    }
} else {
    Write-Host "  端口 18981-18999 均未被占用" -ForegroundColor Gray
}

Write-Host ""
Write-Host '[L3] 清理完成，可重新启动 tauri dev' -ForegroundColor Green
Write-Host ""
if (-not $NoPause) { Read-Host "按 Enter 退出" }
