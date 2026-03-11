# Build L1 and L2 deploy bundles (separate)
# Usage: .\deploy\pack.ps1

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================"
Write-Host "  Build L1 + L2 deploy bundles"
Write-Host "========================================"
Write-Host ""

$rootDir = Split-Path $PSScriptRoot -Parent
Set-Location $rootDir

Write-Host "[1/4] Building images..."
& docker compose -f docker-compose.deploy-l1-l2.yml build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "[2/4] Exporting L1 images..."
$l1Dir = Join-Path $rootDir "deploy-bundle-l1"
$l1Tar = Join-Path $l1Dir "jachin-l1-images.tar"
New-Item -ItemType Directory -Force -Path $l1Dir | Out-Null
docker save -o $l1Tar jachin/l1-db-init:latest jachin/l1-nexus:latest

Write-Host "[3/4] Exporting L2 images..."
$l2Dir = Join-Path $rootDir "deploy-bundle-l2"
$l2Tar = Join-Path $l2Dir "jachin-l2-images.tar"
New-Item -ItemType Directory -Force -Path $l2Dir | Out-Null
docker save -o $l2Tar jachin/l2-control:latest

Write-Host ""
Write-Host "[4/4] Done"
Write-Host ""
Write-Host "L1 bundle: deploy-bundle-l1\"
Write-Host "L2 bundle: deploy-bundle-l2\"
Write-Host ""
Write-Host "Copy each folder to target machine, run .\启动.ps1"
Write-Host "L1+L2 pairing: see deploy\PAIRING_L1_L2.md"
Write-Host ""
Read-Host "Press Enter to exit"
