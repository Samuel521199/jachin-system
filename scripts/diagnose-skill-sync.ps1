# =============================================================================
# Skill sync diagnostic - L2/L3 skills empty
# Usage: .\scripts\diagnose-skill-sync.ps1
# =============================================================================

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$invSkills = Join-Path $env:USERPROFILE ".jachin\inventory\skills"
$l3Cache = Join-Path $env:USERPROFILE ".jachin\l3_skill_cache"
$gatewayCfg = Join-Path $env:USERPROFILE ".jachin\l2_gateway_config.json"
$nexusCfg = Join-Path $env:USERPROFILE ".jachin\nexus_config.json"

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Skill Sync Diagnostic" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# 1. L2 inventory
Write-Host "[1] L2 inventory ~/.jachin/inventory/skills/" -ForegroundColor Yellow
if (Test-Path $invSkills) {
    $dirs = Get-ChildItem -Path $invSkills -Directory -ErrorAction SilentlyContinue
    $cnt = $dirs.Count
    if ($cnt -gt 0) {
        Write-Host "    Exists, dirs: $cnt" -ForegroundColor Green
        $dirs | Select-Object -First 5 | ForEach-Object { Write-Host "      - $($_.Name)" -ForegroundColor Gray }
    } else {
        Write-Host "    Exists but empty (0 skills)" -ForegroundColor Red
        Write-Host "    Fix: L2 admin http://localhost:18888/admin/ click Sync" -ForegroundColor Red
    }
} else {
    Write-Host "    Not found" -ForegroundColor Red
}
Write-Host ""

# 2. L3 cache
Write-Host "[2] L3 cache ~/.jachin/l3_skill_cache/" -ForegroundColor Yellow
if (Test-Path $l3Cache) {
    $dirs = Get-ChildItem -Path $l3Cache -Directory -ErrorAction SilentlyContinue
    $cnt = $dirs.Count
    if ($cnt -gt 0) {
        Write-Host "    Exists, dirs: $cnt" -ForegroundColor Green
        $dirs | Select-Object -First 5 | ForEach-Object { Write-Host "      - $($_.Name)" -ForegroundColor Gray }
    } else {
        Write-Host "    Exists but empty" -ForegroundColor Red
    }
} else {
    Write-Host "    Not found" -ForegroundColor Red
}
Write-Host ""

# 3. L2 API
Write-Host "[3] L2 API (http://localhost:18888)" -ForegroundColor Yellow
$subId = $null
if (Test-Path $gatewayCfg) {
    try {
        $cfg = Get-Content $gatewayCfg -Raw -Encoding UTF8 | ConvertFrom-Json
        $subId = $cfg.sub_account_id
    } catch {
        $null
    }
}
if (-not $subId) {
    Write-Host "    No sub_account_id. L3 needs pairing approval first" -ForegroundColor Red
} else {
    $headers = @{ "X-Sub-Account-Id" = $subId }
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:18888/api/v2/inventory/skills" -Headers $headers -Method Get -TimeoutSec 5 -ErrorAction Stop
        $count = 0
        if ($r.skills) { $count = $r.skills.Count }
        if ($count -gt 0) {
            Write-Host "    [OK] L2 returned $count skills" -ForegroundColor Green
        } else {
            Write-Host "    [OK] L2 returned 0 skills" -ForegroundColor Red
            Write-Host "    Fix: Restart L2 (Ctrl+C run-gateway.ps1, then run again)" -ForegroundColor Red
        }
    } catch {
        Write-Host "    [FAIL] L2 not running: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    Run first: .\scripts\run-gateway.ps1" -ForegroundColor Yellow
    }
}
Write-Host ""

# 4. L1 pairing
Write-Host "[4] L1 pairing (nexus_config.json)" -ForegroundColor Yellow
if (Test-Path $nexusCfg) {
    try {
        $nc = Get-Content $nexusCfg -Raw -Encoding UTF8 | ConvertFrom-Json
        $hasUrl = ($nc.nexus_base_url -or $nc.base_url)
        $hasToken = ($nc.access_token -and $nc.access_token.Length -gt 0)
        Write-Host "    Paired, nexus_base_url=$hasUrl access_token=$hasToken" -ForegroundColor Green
    } catch {
        Write-Host "    File exists but parse failed" -ForegroundColor Red
    }
} else {
    Write-Host "    Not paired. Run: .\scripts\run-pair.ps1" -ForegroundColor Red
}
Write-Host ""

# 5. L3 ports
Write-Host "[5] L3 WebSocket ports (18981 series)" -ForegroundColor Yellow
$l3Ports = @(18981, 18982, 18983, 18984, 18985)
$used = @()
foreach ($p in $l3Ports) {
    $c = netstat -ano 2>$null | Select-String ":$p\s" | Select-String "LISTENING"
    if ($c) { $used += $p }
}
if ($used.Count -gt 0) {
    Write-Host "    Ports in use: $($used -join ', ')" -ForegroundColor Red
    Write-Host "    Free: .\scripts\kill_port.ps1 18981" -ForegroundColor Gray
} else {
    Write-Host "    Ports free" -ForegroundColor Green
}
Write-Host ""

# 6. Startup order
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  Startup Order" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1) Terminal 1: .\scripts\run-gateway.ps1" -ForegroundColor White
Write-Host "  2) Terminal 2: .\scripts\start-layer3.ps1" -ForegroundColor White
Write-Host ""
Write-Host "  If L2 returns 0 skills: restart L2 (run-gateway.ps1) to load permission bypass" -ForegroundColor Yellow
Write-Host "  If port conflict: .\scripts\kill_port.ps1 18981  or  .\scripts\kill_port.ps1 18990" -ForegroundColor Yellow
Write-Host ""
