# 创建占位图标的 PowerShell 脚本
# 使用 .NET 创建简单的 ICO 文件

$iconPath = "src-tauri\icons\icon.ico"
$iconDir = "src-tauri\icons"

# 确保目录存在
if (-not (Test-Path $iconDir)) {
    New-Item -ItemType Directory -Path $iconDir -Force | Out-Null
}

# 使用 PowerShell 和 .NET 创建简单的 ICO
# 注意：这需要 System.Drawing，可能需要安装额外的模块
# 更简单的方法：使用在线工具或 Python

Write-Host "Creating placeholder icon..." -ForegroundColor Cyan
Write-Host ""
Write-Host "Option 1: Use Python (if PIL/Pillow is installed):" -ForegroundColor Yellow
Write-Host "  python scripts/create_placeholder_icon.py" -ForegroundColor Gray
Write-Host ""
Write-Host "Option 2: Download a placeholder icon:" -ForegroundColor Yellow
Write-Host "  Visit: https://www.icoconverter.com/" -ForegroundColor Gray
Write-Host "  Create a 32x32 icon and save as: $iconPath" -ForegroundColor Gray
Write-Host ""
Write-Host "Option 3: Use Tauri CLI (if you have an icon PNG):" -ForegroundColor Yellow
Write-Host "  npx tauri icon path/to/your-icon.png" -ForegroundColor Gray
Write-Host ""

# 尝试使用 .NET 创建（如果可用）
try {
    Add-Type -AssemblyName System.Drawing
    
    # 创建 32x32 位图
    $bitmap = New-Object System.Drawing.Bitmap(32, 32)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    
    # 绘制紫色背景
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(102, 126, 234))
    $graphics.FillRectangle($brush, 0, 0, 32, 32)
    
    # 绘制白色圆圈
    $whiteBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $graphics.FillEllipse($whiteBrush, 4, 4, 24, 24)
    
    # 绘制 "J" 字母
    $font = New-Object System.Drawing.Font("Arial", 18, [System.Drawing.FontStyle]::Bold)
    $textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(102, 126, 234))
    $graphics.DrawString("J", $font, $textBrush, 8, 4)
    
    # 保存为 ICO
    $icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
    $fileStream = New-Object System.IO.FileStream($iconPath, [System.IO.FileMode]::Create)
    $icon.Save($fileStream)
    $fileStream.Close()
    
    $graphics.Dispose()
    $bitmap.Dispose()
    $icon.Dispose()
    
    Write-Host "[OK] Created placeholder icon: $iconPath" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Could not create icon automatically: $_" -ForegroundColor Yellow
    Write-Host "Please use one of the options above to create the icon file." -ForegroundColor Yellow
}
