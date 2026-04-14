# 将 com.jachin.tool.util-weather-lite 单独发布到远端 L1 Nexus。
# 若 500：请先在远端 Postgres 执行 cloud/nexus/scripts/l1-fix-tool-weather-remote.sql，
# 并部署含 publish 路由修复的 Nexus（更新时不 SET plugin_id），见 cloud/nexus/src/app/api/v1/store/publish/route.ts
#
# 用法（仓库根）:
#   $env:JACHIN_DEV_TOKEN = "<与远端 JACHIN_DEV_TOKEN 一致>"
#   $env:JACHIN_NEXUS_URL = "http://47.86.39.173:3000"
#   .\scripts\republish-util-weather-tool.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Nexus = ($env:JACHIN_NEXUS_URL -as [string]).Trim().TrimEnd("/")
if (-not $Nexus) { $Nexus = "http://47.86.39.173:3000" }
if (-not $env:JACHIN_DEV_TOKEN) {
    Write-Host "请设置 JACHIN_DEV_TOKEN" -ForegroundColor Red
    exit 1
}

$env:NEXUS_URL = $Nexus
Push-Location (Join-Path $Root "cloud\nexus")
try {
    node scripts/republish-util-weather.cjs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
