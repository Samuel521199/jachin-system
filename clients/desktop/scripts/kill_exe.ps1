# Kill jachin-desktop.exe process to release file lock
Write-Host "Finding jachin-desktop.exe processes..." -ForegroundColor Yellow

$processes = Get-Process -Name "jachin-desktop" -ErrorAction SilentlyContinue

if ($processes) {
    foreach ($proc in $processes) {
        Write-Host "Killing process: $($proc.ProcessName) (PID: $($proc.Id))" -ForegroundColor Cyan
        Stop-Process -Id $proc.Id -Force -ErrorAction Stop
        Write-Host "Process $($proc.Id) killed successfully" -ForegroundColor Green
    }
} else {
    Write-Host "No jachin-desktop.exe process found" -ForegroundColor Yellow
}

Write-Host "Done" -ForegroundColor Green
