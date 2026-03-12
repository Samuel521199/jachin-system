# Kill process using specified port
param(
    [Parameter(Mandatory=$true)]
    [int]$Port
)

Write-Host "Finding processes using port $Port..." -ForegroundColor Yellow

# Find processes using the port
$connections = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"

if ($connections) {
    $pids = $connections | ForEach-Object {
        $_.ToString().Split()[-1]
    } | Select-Object -Unique

    foreach ($pid in $pids) {
        try {
            $process = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($process) {
                Write-Host "Found process: $($process.ProcessName) (PID: $pid)" -ForegroundColor Cyan
                Write-Host "Killing process $pid..." -ForegroundColor Yellow
                Stop-Process -Id $pid -Force -ErrorAction Stop
                Write-Host "Process $pid killed successfully" -ForegroundColor Green
            }
        } catch {
            Write-Host "Failed to kill process $pid : $_" -ForegroundColor Red
        }
    }
} else {
    Write-Host "No process found using port $Port" -ForegroundColor Yellow
}

Write-Host "Done" -ForegroundColor Green
