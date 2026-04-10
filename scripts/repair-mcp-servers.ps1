# =============================================================================
# 修复 ~/.jachin/mcp_servers.json 中过期的 hr-atomic-tools 路径。
# 用法（仓库根）: .\scripts\repair-mcp-servers.ps1
# =============================================================================
param([string]$ProjectRoot = "")

$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $ScriptDir }
$py = Join-Path $ScriptDir "repair_mcp_servers.py"
& python $py --project-root $ProjectRoot
exit $LASTEXITCODE
