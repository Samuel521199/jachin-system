# SPA mode - Launch Chrome with remote debugging for atom_web_scraper
# Usage: .\scripts\launch_chrome_debug_bi.ps1 [URL]
# Example: .\scripts\launch_chrome_debug_bi.ps1
# Example: .\scripts\launch_chrome_debug_bi.ps1 "https://bi-admin-web.heronpro.xin/#/layout/person"

param([string]$OpenUrl = "")

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chromePath)) {
    $chromePath = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
}
if (-not (Test-Path $chromePath)) {
    Write-Host "Error: Chrome not found" -ForegroundColor Red
    exit 1
}

$userDataDir = "$env:TEMP\chrome-debug-bi"
$chromeArgs = @("--remote-debugging-port=9222", "--user-data-dir=$userDataDir")
if ($OpenUrl) {
    $chromeArgs += $OpenUrl
}
else {
    $chromeArgs += "https://bi-admin-web.heronpro.xin/#/layout/person"
}

Write-Host "Starting Chrome (debug port 9222)..." -ForegroundColor Green
Start-Process -FilePath $chromePath -ArgumentList $chromeArgs
Write-Host "Chrome started. Debug URL: http://127.0.0.1:9222" -ForegroundColor Cyan
Write-Host "Next: Login in Chrome, then run the scraper." -ForegroundColor Yellow
