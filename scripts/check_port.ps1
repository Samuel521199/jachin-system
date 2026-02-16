# 通用端口检查脚本
# 用法: .\scripts\check_port.ps1 [端口号]
# 示例: .\scripts\check_port.ps1 18888

param(
    [Parameter(Mandatory=$false)]
    [int]$Port = 18888
)

Write-Host "Checking port $Port status..." -ForegroundColor Cyan
Write-Host ""

# 检查所有占用指定端口的连接
$connections = netstat -ano | Select-String ":$Port\s"
Write-Host "All connections on port $Port:" -ForegroundColor Yellow
if ($connections) {
    $connections | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host "  No connections found" -ForegroundColor Green
}

Write-Host ""
Write-Host "LISTENING connections:" -ForegroundColor Yellow
$listening = $connections | Select-String "LISTENING"
if ($listening) {
    $listening | ForEach-Object {
        $line = $_.ToString()
        $parts = $line -split '\s+'
        $processId = $parts[-1]
        
        Write-Host "  PID: $processId" -ForegroundColor White
        
        # 检查进程是否存在
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "    Process: $($process.ProcessName)" -ForegroundColor Green
            Write-Host "    Status: Running" -ForegroundColor Green
            Write-Host "    Path: $($process.Path)" -ForegroundColor Gray
        } else {
            Write-Host "    Process: NOT FOUND" -ForegroundColor Red
            Write-Host "    Status: Stale connection (may be TIME_WAIT)" -ForegroundColor Yellow
            Write-Host "    Action: Wait a few seconds for port to be released" -ForegroundColor Cyan
        }
    }
} else {
    Write-Host "  No LISTENING connections found" -ForegroundColor Green
    Write-Host "  Port $Port is available!" -ForegroundColor Green
}

Write-Host ""
Write-Host "TIME_WAIT connections:" -ForegroundColor Yellow
$timeWait = $connections | Select-String "TIME_WAIT"
if ($timeWait) {
    Write-Host "  Found TIME_WAIT connections (these will clear automatically)" -ForegroundColor Cyan
    $timeWait | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host "  No TIME_WAIT connections" -ForegroundColor Green
}
