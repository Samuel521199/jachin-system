# Chrome remote debugging for CDP (Kalaroko MCP, Boss Zhipin, GameQA attach, etc.)
# 默认 9222 与 GameQA（GAMEQA_REMOTE_DEBUG_PORT / cdp_http.txt 回退）、KALAROKO_CDP_ENDPOINT 一致。
# 1) Run this script. 2) Set KALAROKO_CDP_ENDPOINT=http://127.0.0.1:9222 in .env.
# Uses %TEMP%\chrome-debug-boss as user-data-dir to avoid clashing with normal Chrome.
# Usage: .\scripts\launch_chrome_debug.ps1  ["https://example.com/"]

param([string]$OpenUrl = "")

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userDataDir = "$env:TEMP\chrome-debug-boss"

if (-not (Test-Path $chromePath)) {
    Write-Host "ERROR: Chrome not found: $chromePath" -ForegroundColor Red
    exit 1
}

$args = @("--remote-debugging-port=9222", "--user-data-dir=$userDataDir")
if ($OpenUrl) {
    $args += $OpenUrl
}

Write-Host "Starting Chrome (CDP port 9222)..." -ForegroundColor Green
Start-Process -FilePath $chromePath -ArgumentList $args
Write-Host "CDP URL: http://127.0.0.1:9222" -ForegroundColor Gray
if ($OpenUrl) {
    Write-Host "Open URL: $OpenUrl" -ForegroundColor Gray
}
