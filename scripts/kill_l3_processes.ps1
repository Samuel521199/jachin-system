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

function Stop-JachinProcessTree {
    param([int]$TargetProcessId)
    if (-not $TargetProcessId -or $TargetProcessId -le 0) { return }
    try {
        Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 20; $i++) {
            $proc = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
            if (-not $proc -or $proc.HasExited) { break }
            Start-Sleep -Milliseconds 100
        }
    } catch { }
    try {
        $stillAlive = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
        if ($stillAlive -and $stillAlive.HasExited) { $stillAlive = $null }
        $stillCim = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
        if ($stillCim) {
            try {
                Invoke-CimMethod -InputObject $stillCim -MethodName Terminate -ErrorAction SilentlyContinue *> $null
                Start-Sleep -Milliseconds 200
            } catch { }
        }
        if ($stillAlive -or $stillCim) {
            & taskkill /F /T /PID $TargetProcessId *> $null
            for ($i = 0; $i -lt 20; $i++) {
                $proc = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
                if (-not $proc -or $proc.HasExited) { break }
                Start-Sleep -Milliseconds 100
            }
        }
    } catch {
        & taskkill /F /T /PID $TargetProcessId *> $null
    }
}

function Stop-TcpPortListener {
    param([int]$Port)
    $connections = @(netstat -ano 2>$null | Select-String ":$Port\s" | Select-String "LISTENING")
    if (-not $connections.Count) { return $false }
    $processIds = $connections | ForEach-Object { $_.ToString().Split()[-1] } | Select-Object -Unique
    foreach ($processId in $processIds) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) {
            Stop-JachinProcessTree -TargetProcessId ([int]$processId)
        }
    }
    return $true
}

# 1. 结束 L3 相关进程（python l3_node + l3_node-*.exe Sidecar）
#    同时结束由桌面端拉起的英语背词常驻服务。它不是 l3_node，
#    但会占用 18987；Ctrl+C 关闭桌面/L3 后若残留，会导致下次启动复用旧服务或卡住。
$killed = 0
try {
    $allProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    foreach ($p in $allProcs) {
        $name = $p.Name
        $cmd = if ($p.CommandLine) { $p.CommandLine } else { "" }
        $isL3 = ($name -eq "python.exe" -and $cmd -match "l3_node") -or ($name -match "^l3_node-.*\.exe$")
        $isEnglishVocabService = ($name -eq "python.exe" -and $cmd -match "english_vocab_service\.py")
        $shouldStop = $isL3 -or $isEnglishVocabService
        if ($shouldStop) {
            Write-Host "  结束: $name (PID $($p.ProcessId))" -ForegroundColor Yellow
            Stop-JachinProcessTree -TargetProcessId ([int]$p.ProcessId)
            $killed++
        }
    }
} catch {
    Write-Host "  查询进程时出错: $_" -ForegroundColor Red
}
if ($killed -eq 0) {
    Write-Host "  未发现 L3/英语背词服务进程" -ForegroundColor Gray
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

# 2.5 清理 L3 单实例锁。这个脚本的语义就是“我要重新启动 L3”，因此端口/进程清理后
# 可以安全移除旧锁，避免 PyInstaller 父子进程退出后留下 PID 导致 packaged L3 秒退。
try {
    $lockPath = Join-Path $HOME ".jachin\l3.lock"
    if (Test-Path -LiteralPath $lockPath) {
        $pidText = (Get-Content -LiteralPath $lockPath -Raw -ErrorAction SilentlyContinue).Trim()
        $pidNum = 0
        [void][int]::TryParse($pidText, [ref]$pidNum)
        $alive = $false
        if ($pidNum -gt 0) {
            $aliveProc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidNum" -ErrorAction SilentlyContinue
            $cmd = if ($aliveProc -and $aliveProc.CommandLine) { [string]$aliveProc.CommandLine } else { "" }
            $alive = $aliveProc -and ($cmd -match "l3_node")
        }
        if (-not $alive) {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
            Write-Host "  已清理 stale L3 锁: $lockPath" -ForegroundColor Gray
        } else {
            Write-Host "  L3 锁仍指向存活实例 PID $pidNum，跳过删除" -ForegroundColor Gray
        }
    }
} catch {
    Write-Host "  清理 L3 锁时出错: $_" -ForegroundColor DarkGray
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
