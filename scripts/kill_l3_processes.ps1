# 清理残留的 L3 进程与端口
# 用法: .\scripts\kill_l3_processes.ps1
#       .\scripts\kill_l3_processes.ps1 -NoPause  # 不等待 Enter，适合脚本调用
#       .\scripts\kill_l3_processes.ps1 -NoPause -AlsoKillDesktopDev  # 另结束本仓库 tauri target 下 jachin-desktop（避免 single-instance 双开秒退）

param(
    [switch]$NoPause,
    [switch]$AlsoKillDesktopDev
)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force -ErrorAction SilentlyContinue
$ErrorActionPreference = 'Continue'
# 被 start-layer3 等调用时独立进程，须自设编码，否则 Windows PowerShell 5.1 下中文易乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = ".\scripts" }

Write-Host ""
Write-Host '[L3] 清理残留 L3 进程与端口...' -ForegroundColor Cyan
Write-Host ""

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
        if (Stop-TcpPortListener -Port $p) {
            Write-Host "  已尝试释放端口 $p" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "  端口 18981-18999 均未被占用" -ForegroundColor Gray
}

# 3. 可选：结束本仓库 cargo 输出目录下的桌面进程（Tauri single-instance 会令第二进程打印 [Kernel] 后立即退出）
if ($AlsoKillDesktopDev) {
    Write-Host ""
    Write-Host '[L3] 结束本仓库 src-tauri\target 下的 jachin-desktop（避免与上次 tauri dev 双开）...' -ForegroundColor Cyan
    $ProjectRoot = (Resolve-Path (Join-Path $ScriptDir '..')).Path
    $targetDir = Join-Path $ProjectRoot 'clients\desktop\src-tauri\target'
    if (-not (Test-Path -LiteralPath $targetDir)) {
        Write-Host "  跳过: 未找到 clients\desktop\src-tauri\target" -ForegroundColor Gray
    } else {
        $targetPrefix = ((Resolve-Path -LiteralPath $targetDir).Path).TrimEnd('\')
        $dk = 0
        try {
            $desk = Get-CimInstance Win32_Process -Filter "Name = 'jachin-desktop.exe'" -ErrorAction SilentlyContinue
            foreach ($p in $desk) {
                $exe = $p.ExecutablePath
                if ([string]::IsNullOrWhiteSpace($exe)) { continue }
                $norm = $exe.TrimEnd('\')
                $under = $norm.StartsWith($targetPrefix + '\', [StringComparison]::OrdinalIgnoreCase)
                if (-not $under) { continue }
                Write-Host "  结束: jachin-desktop (PID $($p.ProcessId)) $norm" -ForegroundColor Yellow
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
                $dk++
            }
        } catch {
            Write-Host "  查询 jachin-desktop 时出错: $_" -ForegroundColor Red
        }
        if ($dk -eq 0) {
            Write-Host "  未发现本仓库 target 下的 jachin-desktop" -ForegroundColor Gray
        }
    }
}

Write-Host ""
Write-Host '[L3] 清理完成，可重新启动 tauri dev' -ForegroundColor Green
Write-Host ""
if (-not $NoPause) { Read-Host "按 Enter 退出" }
