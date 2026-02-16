# 清理端口占用
# 停止可能占用端口的 Dapr 应用和进程

param(
    [int]$Port = 3501
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Port Cleanup - Port $Port" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. 停止 mock-iot-device Dapr 应用
Write-Host "Stopping mock-iot-device Dapr application..." -ForegroundColor Blue
dapr stop --app-id mock-iot-device 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Stopped mock-iot-device" -ForegroundColor Green
} else {
    Write-Host "[INFO] mock-iot-device not running" -ForegroundColor Gray
}

Write-Host ""

# 2. 查找占用端口的进程
Write-Host "Finding processes using port $Port..." -ForegroundColor Blue
$connections = netstat -ano | Select-String ":$Port\s"

if ($connections) {
    $pids = @()
    $connections | ForEach-Object {
        if ($_.Line -match '\s+(\d+)\s*$') {
            $pid = [int]$matches[1]
            if ($pid -notin $pids) {
                $pids += $pid
            }
        }
    }
    
    if ($pids.Count -gt 0) {
        Write-Host "[WARN] Found processes using port $Port:" -ForegroundColor Yellow
        foreach ($pid in $pids) {
            try {
                $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($process) {
                    Write-Host "  PID $pid : $($process.ProcessName)" -ForegroundColor Gray
                    
                    # 询问是否终止
                    $response = Read-Host "  Kill this process? (y/N)"
                    if ($response -eq 'y' -or $response -eq 'Y') {
                        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                        Write-Host "  [OK] Process $pid terminated" -ForegroundColor Green
                    }
                }
            } catch {
                Write-Host "  [INFO] Process $pid not found (may have already terminated)" -ForegroundColor Gray
            }
        }
    }
} else {
    Write-Host "[OK] No processes found using port $Port" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Cleanup completed!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
