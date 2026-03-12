# 将 local-hr-fs MCP 配置部署到 ~/.jachin/inventory/mcps/
# 供 HR 简历透视镜技能使用
$ErrorActionPreference = "Stop"
$dest = Join-Path $env:USERPROFILE ".jachin\inventory\mcps\local-hr-fs"
$src = Join-Path (Split-Path $PSScriptRoot -Parent) "config\local-hr-fs"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item "$src\*" $dest -Recurse -Force
Write-Host "Deployed local-hr-fs to $dest"
Write-Host "Restart L2: python -m core.main"
