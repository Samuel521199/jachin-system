# 以调试模式启动 Chrome（Boss 直聘 / Kalaroko MCP 等 CDP 连接）
# Kalaroko：先运行本脚本，再在仓库根 .env 设置 KALAROKO_CDP_ENDPOINT=http://127.0.0.1:9222 后跑巡检（Playwright connect_over_cdp）
# 使用独立用户数据目录，避免与已有 Chrome 实例冲突，确保 9222 端口生效
# 用法: .\scripts\launch_chrome_debug.ps1 [可选: 启动后打开的 URL]
# 示例: .\scripts\launch_chrome_debug.ps1 "https://www.zhipin.com/web/user/?ka=header-login"

param([string]$OpenUrl = "")

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userDataDir = "$env:TEMP\chrome-debug-boss"

if (-not (Test-Path $chromePath)) {
    Write-Host "错误: 未找到 Chrome，请检查路径: $chromePath" -ForegroundColor Red
    exit 1
}

$args = @("--remote-debugging-port=9222", "--user-data-dir=$userDataDir")
if ($OpenUrl) {
    $args += $OpenUrl
}

Write-Host "正在启动 Chrome（调试端口 9222）..." -ForegroundColor Green
Start-Process -FilePath $chromePath -ArgumentList $args
Write-Host "Chrome 已启动。调试地址: http://127.0.0.1:9222" -ForegroundColor Gray
if ($OpenUrl) {
    Write-Host "已打开: $OpenUrl" -ForegroundColor Gray
}
