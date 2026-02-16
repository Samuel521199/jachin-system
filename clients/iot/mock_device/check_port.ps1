# 检查端口占用情况
# 用于诊断端口冲突问题

param(
    [int]$Port = 3501
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Port Checker - Port $Port" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 检查端口占用
Write-Host "Checking port $Port..." -ForegroundColor Blue
$connections = netstat -ano | Select-String ":$Port\s"

if ($connections) {
    Write-Host "[WARN] Port $Port is in use:" -ForegroundColor Yellow
    Write-Host ""
    
    $connections | ForEach-Object {
        $line = $_.Line
        Write-Host "  $line" -ForegroundColor Gray
        
        # 提取 PID
        if ($line -match '\s+(\d+)\s*$') {
            $pid = $matches[1]
            Write-Host "    PID: $pid" -ForegroundColor DarkGray
            
            # 尝试获取进程信息
            try {
                $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($process) {
                    Write-Host "    Process: $($process.ProcessName) ($($process.Path))" -ForegroundColor DarkGray
                }
            } catch {
                # 进程可能已经结束
            }
        }
    }
    Write-Host ""
    Write-Host "[INFO] To free the port, you can:" -ForegroundColor Cyan
    Write-Host "  1. Stop the process using the PID above" -ForegroundColor Gray
    Write-Host "  2. Or run: dapr stop --app-id mock-iot-device" -ForegroundColor Gray
    Write-Host "  3. Or kill the process: taskkill /PID <PID> /F" -ForegroundColor Gray
} else {
    Write-Host "[OK] Port $Port is available" -ForegroundColor Green
}

Write-Host ""
Write-Host "Checking Dapr applications..." -ForegroundColor Blue
$daprApps = dapr list 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host $daprApps
} else {
    Write-Host "[WARN] Could not list Dapr applications" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
