# 以调试模式启动 Chrome（用于 Boss 直聘职位发布、简历下载等）
# 使用独立用户数据目录，避免与已有 Chrome 实例冲突，确保 9222 端口生效
# 用法: .\launch_chrome_debug.ps1 [可选: 启动后打开的 URL]
param([string]$OpenUrl = "")

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userDataDir = "$env:TEMP\chrome-debug-boss"

if (-not (Test-Path $chromePath)) {
    Write-Host "错误: 未找到 Chrome，请检查路径: $chromePath" -ForegroundColor Red
    exit 1
}

$args = @("--remote-debugging-port=9222", "--user-data-dir=$userDataDir")
if ($OpenUrl) { $args += $OpenUrl }

Write-Host "正在启动 Chrome（调试端口 9222）..." -ForegroundColor Green
Start-Process -FilePath $chromePath -ArgumentList $args
Write-Host "Chrome 已启动。请登录 Boss 直聘后继续操作。" -ForegroundColor Yellow
