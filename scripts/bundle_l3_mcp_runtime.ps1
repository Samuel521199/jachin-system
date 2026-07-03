# Bundle embedded CPython + official MCP PyPI wheels into portable L3 output (Windows amd64).
# Layout: <OutDir>/runtime/python/python.exe — matches core/mcp_embedded_runtime.py + JACHIN_APP_ROOT.
#
# Usage (from repo root):
#   .\scripts\bundle_l3_mcp_runtime.ps1 -OutDir dist_jachin_desktop
#   .\scripts\bundle_l3_mcp_runtime.ps1 -OutDir dist_jachin_desktop -Force   # redownload/reinstall
#
# Requires: network, ~80MB+ disk (embed + wheels). ARM64 Windows not supported by this script.

param(
    [Parameter(Mandatory = $false)]
    [string]$Root = "",
    [Parameter(Mandatory = $true)]
    [string]$OutDir,
    [string]$PythonVersion = "3.12.9",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$runtimePython = Join-Path $OutDir "runtime\python"
$reqFile = Join-Path $Root "tools\mcp-official\requirements-official-mcp.txt"
$mcpRtReadme = Join-Path $Root "tools\mcp-runtime\README.txt"
$mcpRtManifest = Join-Path $Root "tools\mcp-runtime\manifest.example.json"

if (-not (Test-Path $reqFile)) {
    Write-Error "requirements not found: $reqFile"
}

if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") {
    Write-Warning "ARM64: embed zip in this script is amd64-only; skip or extend URL for win_arm64."
}

$marker = Join-Path $runtimePython ".jachin_mcp_runtime_ok"
$pyTarget = Join-Path $runtimePython "python.exe"
if ((Test-Path $marker) -and (Test-Path $pyTarget) -and -not $Force) {
    Write-Host "[MCP Runtime] Already bundled (use -Force to refresh): $runtimePython" -ForegroundColor Gray
    exit 0
}

if ($Force -and (Test-Path $runtimePython)) {
    Remove-Item $runtimePython -Recurse -Force
}
$null = New-Item -ItemType Directory -Force -Path $runtimePython | Out-Null
$zipName = "python-$PythonVersion-embed-amd64.zip"
$zipUrl = "https://www.python.org/ftp/python/$PythonVersion/$zipName"
$zipPath = Join-Path $env:TEMP $zipName

Write-Host "[MCP Runtime] Downloading $zipUrl ..." -ForegroundColor Cyan
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

Write-Host "[MCP Runtime] Extracting to $runtimePython ..." -ForegroundColor Cyan
Expand-Archive -Path $zipPath -DestinationPath $runtimePython -Force
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

$pthFiles = Get-ChildItem -Path $runtimePython -Filter "*._pth" -File
foreach ($p in $pthFiles) {
    $lines = Get-Content $p.FullName
    $needsSite = $true
    foreach ($line in $lines) {
        if ($line -match '^\s*import\s+site\s*$') { $needsSite = $false; break }
    }
    if ($needsSite) {
        Add-Content -Path $p.FullName -Value "`r`nimport site`r`n"
        Write-Host "[MCP Runtime] Appended 'import site' to $($p.Name)" -ForegroundColor Gray
    }
}

$py = Join-Path $runtimePython "python.exe"
if (-not (Test-Path $py)) {
    Write-Error "python.exe missing under $runtimePython"
}

$getPip = Join-Path $env:TEMP "get-pip.py"
Write-Host "[MCP Runtime] get-pip.py ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
& $py $getPip --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    Write-Error "get-pip failed with exit $LASTEXITCODE"
}

Write-Host "[MCP Runtime] pip install -r mcp-official (ASCII-only file) ..." -ForegroundColor Cyan
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r $reqFile --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install requirements failed with exit $LASTEXITCODE"
}

Write-Host "[MCP Runtime] pip install local vocabulary model runtime deps ..." -ForegroundColor Cyan
& $py -m pip install ctranslate2==4.8.0 sentencepiece==0.2.0 --only-binary=:all: --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install local vocabulary runtime deps failed with exit $LASTEXITCODE"
}

if (Test-Path $mcpRtReadme) {
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $OutDir "runtime") | Out-Null
    Copy-Item $mcpRtReadme -Destination (Join-Path $OutDir "runtime\README_MCP_RUNTIME.txt") -Force
}
if (Test-Path $mcpRtManifest) {
    Copy-Item $mcpRtManifest -Destination (Join-Path $OutDir "runtime\manifest.example.json") -Force
}

@"
schema_version=1
python_embed=$PythonVersion
bundled_at_utc=$(Get-Date -Format "o")
packages=mcp-server-fetch,mcp-server-time,mcp-server-git (see tools/mcp-official/requirements-official-mcp.txt)
"@ | Set-Content -Path $marker -Encoding UTF8

Write-Host "[MCP Runtime] Done: $py" -ForegroundColor Green
Write-Host "  Test: & `"$py`" -c `"import mcp_server_fetch`"" -ForegroundColor Gray
