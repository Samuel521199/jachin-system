# 通用端口关闭脚本（若权限不足请以管理员运行）
# 用法: .\scripts\kill_port.ps1 [端口号]
# 示例: .\scripts\kill_port.ps1 18888  或  .\scripts\kill_port.ps1 8000

param(
    [Parameter(Mandatory=$false)]
    [int]$Port = 18888
)

Write-Host "Attempting to free port $Port..." -ForegroundColor Cyan
Write-Host ""

# 查找占用端口的进程
$connections = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
if (-not $connections) {
    Write-Host "Port $Port is not in use." -ForegroundColor Green
    exit 0
}

$processIds = $connections | ForEach-Object {
    $_.ToString().Split()[-1]
} | Select-Object -Unique

$killed = @()
$failed = @()

foreach ($processId in $processIds) {
    try {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Found process: $($process.ProcessName) (PID: $processId)" -ForegroundColor Yellow
            
            # 尝试正常关闭
            try {
                Stop-Process -Id $processId -Force -ErrorAction Stop
                Write-Host "  [SUCCESS] Stopped process $processId" -ForegroundColor Green
                $killed += $processId
            } catch {
                # 如果失败，尝试使用 taskkill
                Write-Host "  Normal stop failed, trying taskkill..." -ForegroundColor Yellow
                $result = Start-Process -FilePath "taskkill" -ArgumentList "/F", "/PID", $processId -Wait -NoNewWindow -PassThru
                if ($result.ExitCode -eq 0) {
                    Write-Host "  [SUCCESS] Stopped process $processId using taskkill" -ForegroundColor Green
                    $killed += $processId
                } else {
                    Write-Host "  [FAILED] Could not stop process $processId" -ForegroundColor Red
                    Write-Host "    Access denied. Try: .\scripts\kill_port.ps1 $Port (as Administrator)" -ForegroundColor Yellow
                    Write-Host "    Or: taskkill /F /PID $processId" -ForegroundColor Cyan
                    $failed += $processId
                }
            }
        } else {
            Write-Host "Process $processId not found (may be stale connection)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "Failed to check process $processId : $_" -ForegroundColor Red
        $failed += $processId
    }
}

Write-Host ""
if ($killed.Count -gt 0) {
    Write-Host "[SUCCESS] Stopped $($killed.Count) process(es): $($killed -join ', ')" -ForegroundColor Green
}
if ($failed.Count -gt 0) {
    Write-Host "[WARNING] Failed to stop $($failed.Count) process(es): $($failed -join ', ')" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To stop these processes, run PowerShell as Administrator and execute:" -ForegroundColor Cyan
    foreach ($pid in $failed) {
        Write-Host "  taskkill /F /PID $pid" -ForegroundColor White
    }
}

# 等待端口释放
if ($killed.Count -gt 0) {
    Write-Host ""
    Write-Host "Waiting 2 seconds for port to be released..." -ForegroundColor Cyan
    Start-Sleep -Seconds 2
    
    $finalCheck = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
    if (-not $finalCheck) {
        Write-Host "[SUCCESS] Port $Port is now free!" -ForegroundColor Green
    } else {
        Write-Host "[WARNING] Port $Port may still be in use (check TIME_WAIT state)" -ForegroundColor Yellow
    }
}
