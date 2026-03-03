# =============================================================================
# Jachin 主菜单 - 双击 start.bat 时显示，无需记命令
# =============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Show-Menu {
    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host "  Jachin Nexus Layer 2" -ForegroundColor Cyan
    Write-Host "==========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] Start Nexus Console" -ForegroundColor White
    Write-Host "      http://localhost:3000" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [2] Start Layer2 Edge" -ForegroundColor White
    Write-Host "      Full or Light mode | pairing required first" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [3] Install / Check dependencies" -ForegroundColor White
    Write-Host ""
    Write-Host "  [4] Edge agent pairing" -ForegroundColor White
    Write-Host "      6-digit code to connect" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [0] Exit" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Choose [0-4]: " -NoNewline -ForegroundColor Yellow
}

Show-Menu
$choice = Read-Host

switch ($choice) {
    "1" {
        Write-Host ""
        & (Join-Path $ScriptDir "start-cloud.ps1")
    }
    "2" {
        Write-Host ""
        & (Join-Path $ScriptDir "start-layer2.ps1")
    }
    "3" {
        Write-Host ""
        & (Join-Path $ScriptDir "check-prerequisites.ps1") cloud
        if ($LASTEXITCODE -eq 0) {
            & (Join-Path $ScriptDir "install-cloud.ps1")
        }
        Write-Host ""
        Write-Host "Press any key to close..." -ForegroundColor DarkGray
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
    "4" {
        Write-Host ""
        & (Join-Path $ScriptDir "run-pair.ps1")
        Write-Host ""
        Write-Host "Press any key to close..." -ForegroundColor DarkGray
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
    default {
        Write-Host "Exited." -ForegroundColor DarkGray
    }
}
