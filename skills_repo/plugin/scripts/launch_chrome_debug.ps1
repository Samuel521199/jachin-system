# Chrome remote debugging for CDP (Boss Zhipin automation, etc.)
# Uses %TEMP%\chrome-debug-boss as user-data-dir to avoid clashing with normal Chrome.
# Usage: .\launch_chrome_debug.ps1  ["https://example.com/"]

param([string]$OpenUrl = "")

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userDataDir = "$env:TEMP\chrome-debug-boss"

if (-not (Test-Path $chromePath)) {
    Write-Host "ERROR: Chrome not found: $chromePath" -ForegroundColor Red
    exit 1
}

$args = @("--remote-debugging-port=9222", "--user-data-dir=$userDataDir")
if ($OpenUrl) { $args += $OpenUrl }

Write-Host "Starting Chrome (CDP port 9222)..." -ForegroundColor Green
Start-Process -FilePath $chromePath -ArgumentList $args
Write-Host "CDP URL: http://127.0.0.1:9222" -ForegroundColor Gray
Write-Host "Boss Zhipin: sign in in this Chrome window before using MCP." -ForegroundColor Yellow
