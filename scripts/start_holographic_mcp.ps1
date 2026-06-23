# Start Holographic Screen MCP (stdio) with OmniParser venv
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv-omniparser\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "Missing $Py — run .\scripts\setup_omniparser_venv.ps1 first"
    exit 1
}
Set-Location $Root
& $Py -m l3_client.local_mcps.holographic_screen_mcp.server
