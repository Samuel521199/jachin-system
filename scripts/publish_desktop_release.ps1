# 从仓库根目录执行：先加载 cloud/nexus/.env.local 中的变量，再调用发布脚本。
# 用法:
#   .\scripts\publish_desktop_release.ps1 --installer "clients\desktop\src-tauri\target\release\jachin-desktop.exe" --unsigned
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$nexusEnv = Join-Path $RepoRoot "cloud\nexus\.env.local"
if (Test-Path $nexusEnv) {
  Get-Content $nexusEnv -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq "") { return }
    if ($line -match '^\s*export\s+') { $line = $line -replace '^\s*export\s+', '' }
    $eq = $line.IndexOf('=')
    if ($eq -lt 1) { return }
    $name = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim()
    if ($val.Length -ge 2 -and ($val[0] -eq $val[-1]) -and ($val[0] -eq '"' -or $val[0] -eq "'")) {
      $val = $val.Substring(1, $val.Length - 2)
    }
    if ($name) {
      [Environment]::SetEnvironmentVariable($name, $val, "Process")
    }
  }
  Write-Host "已加载: $nexusEnv" -ForegroundColor DarkGray
} else {
  Write-Host "未找到 $nexusEnv ，请复制 cloud\nexus\.env.example 为 .env.local 并填写 DESKTOP_RELEASES_* 与 NEXUS_ADMIN_SECRET" -ForegroundColor Yellow
}

python (Join-Path $PSScriptRoot "publish_desktop_release.py") @args
exit $LASTEXITCODE
