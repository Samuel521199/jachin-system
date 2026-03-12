# =============================================================================
# L2 FastAPI 后端启动 - Admin 面板: http://localhost:18888/admin/
# 用法: .\scripts\run-backend.ps1
# =============================================================================

$ErrorActionPreference = "Continue"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 加载项目根 .env 到当前进程，确保 DASHSCOPE_API_KEY 等传给 Python（conda run 可能不继承）
$EnvFile = Join-Path $ProjectRoot ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
            $key = $matches[1].Trim(); $val = $matches[2].Trim() -replace '^["'']|["'']$'
            if ($key -and -not [string]::IsNullOrEmpty($val)) { Set-Item -Path "Env:$key" -Value $val }
        }
    }
}

$Port = if ($env:APP_PORT) { $env:APP_PORT } elseif ($env:SERVER_PORT) { $env:SERVER_PORT } else { 18888 }

if (-not $env:JACHIN_L2_ADMIN_TOKEN) {
    Write-Host "[WARN] JACHIN_L2_ADMIN_TOKEN 未设置，Admin 面板将不可用 (503)" -ForegroundColor Yellow
    Write-Host "  在 .env 中配置或: `$env:JACHIN_L2_ADMIN_TOKEN='your-token'" -ForegroundColor Gray
    Write-Host ""
}
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  L2 Backend (FastAPI)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  API:    http://localhost:$Port" -ForegroundColor White
Write-Host "  Admin:  http://localhost:$Port/admin/" -ForegroundColor Green
Write-Host "  Docs:   http://localhost:$Port/docs" -ForegroundColor White
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

$Python = $env:JACHIN_PYTHON
if (-not $Python) { $Python = "python" }

# Try conda jachin-layer2 first
if (Get-Command conda -ErrorAction SilentlyContinue) {
    $null = conda run -n jachin-layer2 --no-capture-output python -c "import fastapi" 2>&1
    if ($LASTEXITCODE -eq 0) {
        conda run -n jachin-layer2 --no-capture-output python -m uvicorn core.main:app --host 0.0.0.0 --port $Port
        exit $LASTEXITCODE
    }
}

# Fallback: system python
& $Python -m uvicorn core.main:app --host 0.0.0.0 --port $Port
exit $LASTEXITCODE
