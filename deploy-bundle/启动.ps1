# Jachin L1 + L2 Deploy
# Usage: .\启动.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host "  Jachin L1 + L2 Deploy"
Write-Host "========================================"
Write-Host ""

Set-Location $PSScriptRoot

$ComposeFile = "docker-compose.deploy-l1-l2-images.yml"
$TarFile = "jachin-l1-l2-images.tar"

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
if ($LASTEXITCODE -eq 0) {
    $ComposeCmd = "docker compose"
} else {
    $ComposeCmd = "docker-compose"
}

$TarPath = (Resolve-Path $TarFile).Path
$sizeMB = [math]::Round((Get-Item $TarFile).Length / 1MB, 1)
Write-Host "[1/2] Loading images from $TarFile ($sizeMB MB)..."
Write-Host "      Importing l1-db-init, l1-nexus, l2-control (may take 1-2 min)..."
$job = Start-Job -ArgumentList $TarPath { param($p) docker load -i $p 2>&1 }
$chars = @("|", "/", "-", "\")
$i = 0
while ($job.State -eq "Running") {
    Write-Host "`r      $($chars[$i % 4]) Loading... " -NoNewline
    Start-Sleep -Milliseconds 200
    $i++
}
Write-Host ""
$out = Receive-Job $job
Wait-Job $job | Out-Null
Remove-Job $job -Force
$out | ForEach-Object { Write-Host "      $_" }
Write-Host "      Done."

Write-Host ""
Write-Host "[2/2] Starting services..."
$l1Port = if ($env:L1_PORT) { $env:L1_PORT } else { "3000" }
$l2Port = if ($env:L2_PORT) { $env:L2_PORT } else { "18888" }
Invoke-Expression "$ComposeCmd -f `"$ComposeFile`" up -d"

Write-Host ""
Write-Host "========================================"
Write-Host "  Done"
Write-Host "========================================"
Write-Host "  L1: http://localhost:$l1Port"
Write-Host "  L2: http://localhost:$l2Port"
Write-Host ""
if ($l1Port -ne "3000") { Write-Host "  (L1_PORT=$l1Port)" }
if ($l2Port -ne "18888") { Write-Host "  (L2_PORT=$l2Port)" }
Write-Host ""
Read-Host "Press Enter to exit"
