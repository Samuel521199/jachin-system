# 以调试模式启动 Chrome（用于 Boss 直聘简历下载测试）
# 使用独立用户数据目录，避免与已有 Chrome 实例冲突，确保 9222 端口生效

$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userDataDir = "$env:TEMP\chrome-debug-boss"

if (-not (Test-Path $chromePath)) {
    Write-Host "错误: 未找到 Chrome，请检查路径: $chromePath" -ForegroundColor Red
    exit 1
}

# 使用独立用户数据目录，可与日常 Chrome 并存，确保调试端口生效
Write-Host "正在启动 Chrome（调试端口 9222，独立配置）..." -ForegroundColor Green
Start-Process -FilePath $chromePath -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=$userDataDir"
Write-Host "Chrome 已启动。请在该窗口中登录 Boss 直聘，打开简历预览弹窗后运行: python scripts\test_download_resume.py" -ForegroundColor Yellow
