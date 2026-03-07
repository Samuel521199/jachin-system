# =============================================================================
# L2 Gateway 模式 - 审批 L3 节点
# 自动配置 JACHIN_L2_ADMIN_TOKEN，启动 Admin 面板，打开即可审批
# 用法: .\scripts\run-gateway.ps1  或  start-layer2.ps1 选 [3]
# =============================================================================

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$EnvPath = Join-Path $ProjectRoot ".env"
$TokenLine = "JACHIN_L2_ADMIN_TOKEN="

# 若未设置 Token，自动生成并写入 .env
if (-not $env:JACHIN_L2_ADMIN_TOKEN) {
    $existing = $false
    $existingToken = $null
    if (Test-Path $EnvPath) {
        Get-Content $EnvPath -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match "^\s*JACHIN_L2_ADMIN_TOKEN\s*=\s*(.+)$" -and $matches[1] -and $matches[1].Trim() -notmatch "^\s*#") {
                $v = ($matches[1] -replace '^["'']|["'']$', '').Trim()
                if ($v) { $existing = $true; $existingToken = $v }
            }
        }
    }
    if (-not $existing) {
        $token = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
        $line = "`n# Auto-generated for Gateway mode`n$TokenLine$token"
        if (-not (Test-Path $EnvPath)) { New-Item -Path $EnvPath -ItemType File -Force | Out-Null }
        Add-Content -Path $EnvPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
        $env:JACHIN_L2_ADMIN_TOKEN = $token
        Write-Host "[Gateway] 已自动生成 JACHIN_L2_ADMIN_TOKEN 并写入 .env" -ForegroundColor Green
    } else {
        $env:JACHIN_L2_ADMIN_TOKEN = $existingToken
        Write-Host "[Gateway] 使用 .env 中的 JACHIN_L2_ADMIN_TOKEN" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  L2 Gateway - 神经接驳审批" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  武库大盘:  http://localhost:18888/admin/" -ForegroundColor Green
Write-Host "  审批 L3:  http://localhost:18888/gateway/" -ForegroundColor Green
Write-Host "  Token 已自动绑定，审批节点请打开 /gateway/" -ForegroundColor Gray
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

& (Join-Path $ScriptDir "run-backend.ps1")
exit $LASTEXITCODE
