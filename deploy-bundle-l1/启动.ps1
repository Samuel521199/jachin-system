# Jachin L1 Deploy (Nexus + PostgreSQL)
# Usage: .\启动.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host "  Jachin L1 Deploy"
Write-Host "========================================"
Write-Host ""

Set-Location $PSScriptRoot

$ComposeFile = "docker-compose.yml"
$TarFile = "jachin-l1-images.tar"

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
Write-Host "      l1-db-init, l1-nexus (may take 1-2 min)..."
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
$l1Port = if ($env:L1_PORT) { $env:L1_PORT } else { "3000" }
Invoke-Expression "$ComposeCmd -f `"$ComposeFile`" up -d"

Write-Host ""
Write-Host "========================================"
Write-Host "  Done"
Write-Host "========================================"
Write-Host "  L1: http://localhost:$l1Port"
Write-Host ""
Read-Host "Press Enter to exit"
