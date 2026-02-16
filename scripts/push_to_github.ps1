# 推送到 GitHub - 以本地为准，清理远程无用文件
# 用法: .\scripts\push_to_github.ps1
# 或指定仓库: .\scripts\push_to_github.ps1 -RepoUrl "https://github.com/Samuel521199/jachin-system.git"

param(
    [string]$RepoUrl = "https://github.com/Samuel521199/jachin-system.git"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

# 可通过 -RepoUrl 参数或环境变量 GITHUB_REPO_URL 覆盖默认 URL
if ($env:GITHUB_REPO_URL) { $RepoUrl = $env:GITHUB_REPO_URL }

# 移除已有 origin（若存在）
$existing = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0) {
    if ($existing -ne $RepoUrl) {
        git remote remove origin
    } else {
        Write-Host "[INFO] 远程已指向: $RepoUrl" -ForegroundColor Cyan
    }
}

if (-not (git remote get-url origin 2>$null)) {
    git remote add origin $RepoUrl
    Write-Host "[OK] 已添加远程: $RepoUrl" -ForegroundColor Green
}

# 强制推送 - 使远程与本地完全一致，清理远程无用文件
Write-Host "[INFO] 强制推送 master（远程将与本地一致，旧文件将被清理）..." -ForegroundColor Cyan
git push -u origin master --force

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 推送失败。请检查：" -ForegroundColor Red
    Write-Host "  1. 仓库是否已在 GitHub 创建" -ForegroundColor Gray
    Write-Host "  2. 是否有推送权限" -ForegroundColor Gray
    Write-Host "  3. Network / auth OK" -ForegroundColor Gray
    exit 1
}

# 推送标签
Write-Host "[INFO] 推送标签 v0.2.0..." -ForegroundColor Cyan
git push origin v0.2.0 --force

Write-Host ""
Write-Host "[SUCCESS] Push completed, remote synced with local" -ForegroundColor Green
