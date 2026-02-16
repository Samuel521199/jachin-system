# Kill process using port 8000
Write-Host "Finding processes using port 8000..." -ForegroundColor Yellow

$connections = netstat -ano | Select-String ":8000\s" | Select-String "LISTENING"

if ($connections) {
    $processIds = $connections | ForEach-Object {
        $_.ToString().Split()[-1]
    } | Select-Object -Unique

    foreach ($processId in $processIds) {
        try {
            $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($process) {
                Write-Host "Found process: $($process.ProcessName) (PID: $processId)" -ForegroundColor Cyan
                Write-Host "Killing process $processId..." -ForegroundColor Yellow
                
                # 尝试正常关闭
                try {
                    Stop-Process -Id $processId -Force -ErrorAction Stop
                    Write-Host "Process $processId killed successfully" -ForegroundColor Green
                } catch {
                    # 如果失败，尝试使用 taskkill（可能需要管理员权限）
                    Write-Host "Normal stop failed, trying taskkill..." -ForegroundColor Yellow
                    $result = Start-Process -FilePath "taskkill" -ArgumentList "/F", "/PID", $processId -Wait -NoNewWindow -PassThru
                    if ($result.ExitCode -eq 0) {
                        Write-Host "Process $processId killed successfully using taskkill" -ForegroundColor Green
                    } else {
                        Write-Host "Failed to kill process $processId. Access denied." -ForegroundColor Red
                        Write-Host "Please run this script as Administrator, or manually close the process:" -ForegroundColor Yellow
                        Write-Host "  taskkill /F /PID $processId" -ForegroundColor Cyan
                        Write-Host "  Or close it from Task Manager (PID: $processId)" -ForegroundColor Cyan
                    }
                }
            }
        } catch {
            Write-Host "Failed to kill process $processId : $_" -ForegroundColor Red
            Write-Host "Please run this script as Administrator, or manually close the process:" -ForegroundColor Yellow
            Write-Host "  taskkill /F /PID $processId" -ForegroundColor Cyan
        }
    }
} else {
    Write-Host "No process found using port 8000" -ForegroundColor Yellow
}

Write-Host "Done" -ForegroundColor Green
