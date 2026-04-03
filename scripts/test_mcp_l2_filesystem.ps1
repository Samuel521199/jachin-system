#Requires -Version 5.1
# Test: official @modelcontextprotocol/server-filesystem is mounted and invokable via MCP HTTP API.
# Run: .\scripts\test_mcp_l2_filesystem.ps1
#      .\scripts\test_mcp_l2_filesystem.ps1 -L2BaseUrl "http://127.0.0.1:18888"
# Prereq: sideload JSON to ~/.jachin/inventory/mcps/ ; restart Layer2 API host ; Node npx on that host.

param(
    [string] $L2BaseUrl = "http://127.0.0.1:18888"
)

$ErrorActionPreference = "Stop"
$base = $L2BaseUrl.TrimEnd("/")

function Invoke-JachinJson {
    param(
        [string] $Method,
        [string] $Path,
        [string] $JsonBody = ""
    )
    $uri = "$base$Path"
    if ($JsonBody.Length -gt 0) {
        return Invoke-RestMethod -Uri $uri -Method $Method -ContentType "application/json; charset=utf-8" -Body $JsonBody
    }
    return Invoke-RestMethod -Uri $uri -Method $Method
}

Write-Host "[1] Check npx" -ForegroundColor Cyan
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
if (-not $npxCmd) {
    Write-Host "FAIL: npx not found. Install Node.js." -ForegroundColor Red
    exit 1
}
Write-Host ("npx: " + $npxCmd.Source)

Write-Host ""
Write-Host "[2] GET /api/v2/mcp/tools" -ForegroundColor Cyan
try {
    $toolsResp = Invoke-JachinJson -Method "Get" -Path "/api/v2/mcp/tools"
}
catch {
    Write-Host ("FAIL: " + $_) -ForegroundColor Red
    Write-Host "Hint: start Layer2 API first (default port 18888). Example: .\start.bat or & .\scripts\start.ps1" -ForegroundColor Yellow
    Write-Host "Then open http://127.0.0.1:18888/health — if wrong port, use -L2BaseUrl http://127.0.0.1:YOUR_PORT" -ForegroundColor Yellow
    exit 1
}
$names = New-Object System.Collections.Generic.List[string]
if ($toolsResp.tools) {
    foreach ($t in $toolsResp.tools) {
        if ($t.name) {
            [void]$names.Add([string]$t.name)
        }
    }
}
Write-Host ("tool count: " + $names.Count)
$want = @("list_allowed_directories", "list_directory", "write_file", "read_text_file")
$found = @($want | Where-Object { $names -contains $_ })
Write-Host ("expected tools found: " + ($found -join ", "))
$missing = @($want | Where-Object { $names -notcontains $_ })
if ($missing.Count -gt 0) {
    Write-Host ("WARN missing (restart API after inventory sideload): " + ($missing -join ", ")) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[3] POST invoke list_allowed_directories" -ForegroundColor Cyan
$body1 = (@{ tool_name = "list_allowed_directories"; arguments = @{} } | ConvertTo-Json -Compress)
try {
    $r1 = Invoke-JachinJson -Method "Post" -Path "/api/v2/mcp/invoke" -JsonBody $body1
    Write-Host ("ok=" + $r1.ok + " result preview:")
    $preview = [string]$r1.result
    if ($preview.Length -gt 500) {
        $preview = $preview.Substring(0, 500) + "..."
    }
    Write-Host $preview
}
catch {
    Write-Host ("FAIL invoke: " + $_) -ForegroundColor Red
    exit 1
}

$jh = if ($env:JACHIN_HOME) { $env:JACHIN_HOME } else { Join-Path $env:USERPROFILE ".jachin" }
$ws = Join-Path $jh "workspace"
if (-not (Test-Path -LiteralPath $ws)) {
    New-Item -ItemType Directory -Force -Path $ws | Out-Null
}
$mcpDemo = Join-Path $ws "mcp_demo"
if (-not (Test-Path -LiteralPath $mcpDemo)) {
    New-Item -ItemType Directory -Force -Path $mcpDemo | Out-Null
}

Write-Host ""
Write-Host "[4] POST invoke write_file -> mcp_demo/mcp_selftest.txt" -ForegroundColor Cyan
$testPath = Join-Path $mcpDemo "mcp_selftest.txt"
$pathForJson = $testPath -replace "\\", "/"
$content = "mcp ok " + (Get-Date).ToString("o")
$body2 = (@{
        tool_name = "write_file"
        arguments = @{
            path    = $pathForJson
            content = $content
        }
    } | ConvertTo-Json -Compress -Depth 5)
try {
    $r2 = Invoke-JachinJson -Method "Post" -Path "/api/v2/mcp/invoke" -JsonBody $body2
    Write-Host ("ok=" + $r2.ok)
    Write-Host $r2.result
}
catch {
    Write-Host ("FAIL write_file: " + $_) -ForegroundColor Red
    exit 1
}

if (Test-Path -LiteralPath $testPath) {
    Write-Host ""
    Write-Host ("PASS: file exists " + $testPath) -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host ("WARN: file missing " + $testPath) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
