# Fix l2_gateway_config.json: restore l2_base_url so L3 starts in gateway mode and sends heartbeat
# Usage: .\scripts\fix-gateway-config.ps1

$jachinDir = Join-Path $env:USERPROFILE ".jachin"
$configPath = Join-Path $jachinDir "l2_gateway_config.json"

if (-not (Test-Path $jachinDir)) {
    New-Item -ItemType Directory -Path $jachinDir -Force | Out-Null
}

$cfg = @{}
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable
    } catch {
        Write-Host "[WARN] Config parse failed, using empty" -ForegroundColor Yellow
    }
}

if (-not $cfg["l2_base_url"]) {
    $cfg["l2_base_url"] = "http://localhost:18888"
    Write-Host "[FIX] Added l2_base_url" -ForegroundColor Green
}

$cfg | ConvertTo-Json | Set-Content $configPath -Encoding UTF8
Write-Host "Config saved: $configPath" -ForegroundColor Cyan
Write-Host "Restart the desktop app to apply." -ForegroundColor Yellow
Write-Host ""
Write-Host "If device still offline, add node_id from DB:" -ForegroundColor Gray
Write-Host "  sqlite3 `"$env:USERPROFILE\.jachin\l2_control.db`" `"SELECT id FROM l3_nodes LIMIT 1`"" -ForegroundColor Gray
