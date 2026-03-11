# Jachin L2 Deploy (Control Plane + Redis) - 等价 start-layer2 选 [3] Gateway
# Usage: .\启动.ps1
# Pair with L1: $env:NEXUS_BASE_URL="http://L1_IP:3000"; .\启动.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host "  Jachin L2 Deploy (Gateway)"
Write-Host "========================================"
Write-Host ""

# 脚本目录：优先 $PSScriptRoot，否则从 $MyInvocation 获取
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir -and $MyInvocation.MyCommand.Path) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $ScriptDir) {
    $ScriptDir = (Get-Location).Path
}
Set-Location $ScriptDir

# 自动生成 JACHIN_L2_ADMIN_TOKEN（与 run-gateway.ps1 一致，审批 L3 需此 Token）
$EnvPath = Join-Path $ScriptDir ".env"
if (-not $EnvPath) { $EnvPath = ".env" }
$TokenLine = "JACHIN_L2_ADMIN_TOKEN="
if (-not $env:JACHIN_L2_ADMIN_TOKEN) {
    $existing = $false
    if (Test-Path $EnvPath) {
        Get-Content $EnvPath -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match "^\s*JACHIN_L2_ADMIN_TOKEN\s*=\s*(.+)$" -and $matches[1] -and $matches[1].Trim() -notmatch "^\s*#") {
                $v = ($matches[1] -replace '^["'']|["'']$', '').Trim()
                if ($v) { $existing = $true; $env:JACHIN_L2_ADMIN_TOKEN = $v }
            }
        }
    }
    if (-not $existing) {
        $token = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
        $line = "`n# Auto-generated for Gateway mode`n$TokenLine$token"
        if (-not (Test-Path $EnvPath)) { New-Item -Path $EnvPath -ItemType File -Force | Out-Null }
        Add-Content -Path $EnvPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
        $env:JACHIN_L2_ADMIN_TOKEN = $token
        Write-Host "[Gateway] 已自动生成 JACHIN_L2_ADMIN_TOKEN 并写入 .env" -ForegroundColor Green
    }
}

$ComposeFile = "docker-compose.yml"
$TarFile = "jachin-l2-images.tar"

if (-not (Test-Path $TarFile)) {
    Write-Host "[Error] File not found: $TarFile"
    Write-Host "Run from project root: .\deploy\pack.ps1"
    Read-Host "Press Enter to exit"
    exit 1
}

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Host "[Error] Docker not found. Please install Docker Desktop"
    Read-Host "Press Enter to exit"
    exit 1
}

docker compose version 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $ComposeCmd = "docker compose" } else { $ComposeCmd = "docker-compose" }

$TarPath = (Resolve-Path $TarFile).Path
$sizeMB = [math]::Round((Get-Item $TarFile).Length / 1MB, 1)
Write-Host "[1/2] Loading images ($sizeMB MB)..."
Write-Host "      l2-control (may take 1-2 min)..."
$job = Start-Job -ArgumentList $TarPath { param($p) docker load -i $p 2>&1 }
$chars = @("|", "/", "-", "\")
$i = 0
while ($job.State -eq "Running") {
    Write-Host "`r      $($chars[$i % 4]) Loading... " -NoNewline
    Start-Sleep -Milliseconds 200
    $i++
}
Write-Host ""
Receive-Job $job | ForEach-Object { Write-Host "      $_" }
Remove-Job $job -Force
Write-Host "      Done."

Write-Host ""
Write-Host "[2/2] Starting services..."
$l2Port = if ($env:L2_PORT) { $env:L2_PORT } else { "18888" }
Invoke-Expression "$ComposeCmd -f `"$ComposeFile`" up -d"

Write-Host ""
Write-Host "========================================"
Write-Host "  Done"
Write-Host "========================================"
Write-Host "  L2 API:     http://localhost:$l2Port"
Write-Host "  武库大盘:   http://localhost:$l2Port/admin/"
Write-Host "  审批 L3:   http://localhost:$l2Port/gateway/"
if ($env:NEXUS_BASE_URL) { Write-Host "  NEXUS_BASE_URL: $env:NEXUS_BASE_URL" }
Write-Host ""
Read-Host "Press Enter to exit"
