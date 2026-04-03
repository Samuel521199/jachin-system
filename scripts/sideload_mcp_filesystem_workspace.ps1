# 侧载 L2_GATEWAY MCP：将 plugin.json 复制到 L2 inventory，触发重载后即可 GET /api/v2/mcp/tools 看到 write_file 等。
# 前置：已安装 Node/npx；L2 需重启或调用 inventory reload（若已运行会自动扫 inventory）。
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$src = Join-Path $repoRoot "skills_repo\plugin\com.jachin.mcp.filesystem_workspace\plugin.json"
# plugin id：com.jachin.mcp.fs.workspace（目录名仍为 filesystem_workspace）
$jh = if ($env:JACHIN_HOME) { $env:JACHIN_HOME } else { Join-Path $env:USERPROFILE ".jachin" }
$destDir = Join-Path $jh "inventory\mcps"
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
$dest = Join-Path $destDir "com.jachin.mcp.fs.workspace.json"
Copy-Item -LiteralPath $src -Destination $dest -Force
$ws = Join-Path $jh "workspace\mcp_demo"
New-Item -ItemType Directory -Force -Path $ws | Out-Null
Write-Host "已写入: $dest"
Write-Host "请确保 L2 已加载 inventory（重启 core 或等待 sync 后 reload）。验证: GET http://localhost:18888/api/v2/mcp/tools"
