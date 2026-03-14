# 测试打包后的 L3 exe，验证调试日志并定位问题
# 用法: .\scripts\test_packaged_l3.ps1
# 从项目根运行，会先构建再测试

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "`n[1/3] Building L3 with debug log (--force)..." -ForegroundColor Cyan
python scripts\build_l3_sidecar.py --force
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n[2/3] Copying to dist_jachin_desktop..." -ForegroundColor Cyan
$binSrc = Join-Path $root "clients\desktop\src-tauri\bin"
$binDst = Join-Path $root "dist_jachin_desktop\bin"
$null = New-Item -ItemType Directory -Force -Path $binDst
Get-ChildItem $binSrc -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName -Destination $binDst -Force
    Write-Host "  Copied $($_.Name)" -ForegroundColor Gray
}

# 清理旧日志
$logPath = Join-Path $root "dist_jachin_desktop\l3_debug.log"
if (Test-Path $logPath) { Remove-Item $logPath -Force }

Write-Host "`n[3/3] Running exe (5 sec then check log)..." -ForegroundColor Cyan
$distDir = Join-Path $root "dist_jachin_desktop"
$exe = Get-ChildItem (Join-Path $distDir "bin") -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) {
    Write-Host "[ERR] No l3_node exe in bin" -ForegroundColor Red
    exit 1
}

$p = Start-Process -FilePath $exe.FullName -ArgumentList "--gateway" -PassThru -NoNewWindow -WorkingDirectory $distDir
Start-Sleep -Seconds 5

if (Test-Path $logPath) {
    Write-Host "`n[OK] Debug log generated:" -ForegroundColor Green
    Write-Host $logPath
    Get-Content $logPath
} else {
    $jachinLog = Join-Path $env:USERPROFILE ".jachin\l3_debug.log"
    if (Test-Path $jachinLog) {
        Write-Host "`n[OK] Debug log in ~/.jachin:" -ForegroundColor Green
        Get-Content $jachinLog
    } else {
        Write-Host "`n[WARN] No log file - exe may have crashed before early_log" -ForegroundColor Yellow
    }
}

$p.Kill() 2>$null
Write-Host "`nDone. Check log for errors." -ForegroundColor Gray
