# Check if running as administrator
# 检查是否以管理员身份运行

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    Write-Host "[OK] Running as Administrator" -ForegroundColor Green
    Write-Host ""
    Write-Host "You can now run:" -ForegroundColor Cyan
    Write-Host "  cd e:\jachin-system" -ForegroundColor White
    Write-Host "  .\scripts\final_docker_e_drive_setup.ps1" -ForegroundColor White
} else {
    Write-Host "[ERROR] NOT running as Administrator" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please:" -ForegroundColor Yellow
    Write-Host "  1. Close this PowerShell window" -ForegroundColor White
    Write-Host "  2. Right-click PowerShell icon" -ForegroundColor White
    Write-Host "  3. Select 'Run as Administrator'" -ForegroundColor White
    Write-Host "  4. Run the script again" -ForegroundColor White
    Write-Host ""
    Write-Host "Or press Win+X and select 'Windows PowerShell (Admin)'" -ForegroundColor Cyan
}
