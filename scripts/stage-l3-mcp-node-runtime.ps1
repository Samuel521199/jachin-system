#Requires -Version 5.1
<#
.SYNOPSIS
  将本机已安装的 Node 运行时复制到 Jachin MCP 嵌入式目录（~/.jachin/runtime/node），
  便于无系统 Node 的机器或 PyInstaller Sidecar 使用 npx MCP。

.DESCRIPTION
  默认源目录：优先环境变量 NODE_SOURCE_DIR，否则常见路径：
    $env:ProgramFiles\nodejs
  目标目录：$env:USERPROFILE\.jachin\runtime\node（可用 JACHIN_HOME 覆盖根目录）

  复制 node.exe、npx.cmd、npm.cmd 及 node_modules（npx 依赖）。若源为官方 zip 解压目录亦可。

.EXAMPLE
  .\scripts\stage-l3-mcp-node-runtime.ps1
  $env:NODE_SOURCE_DIR = "D:\tools\node-v20-win-x64"; .\scripts\stage-l3-mcp-node-runtime.ps1
#>
param(
    [switch] $WhatIf
)

$ErrorActionPreference = "Stop"
$jachinHome = if ($env:JACHIN_HOME) { $env:JACHIN_HOME } else { Join-Path $env:USERPROFILE ".jachin" }
$destRoot = Join-Path $jachinHome "runtime\node"

$src = $env:NODE_SOURCE_DIR
if (-not $src) {
    $pf86 = $null
    try { $pf86 = (Get-Item "Env:ProgramFiles(x86)" -ErrorAction SilentlyContinue).Value } catch {}
    $candidates = @(
        (Join-Path $env:ProgramFiles "nodejs")
    )
    if ($pf86) { $candidates += (Join-Path $pf86 "nodejs") }
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c "node.exe")) {
            $src = $c
            break
        }
    }
}

if (-not $src -or -not (Test-Path (Join-Path $src "node.exe"))) {
    Write-Host "[stage-l3-mcp-node-runtime] 未找到 Node 源目录。请安装 Node 或设置 NODE_SOURCE_DIR 指向含 node.exe 的目录。" -ForegroundColor Yellow
    exit 1
}

Write-Host "[stage-l3-mcp-node-runtime] 源: $src" -ForegroundColor Cyan
Write-Host "[stage-l3-mcp-node-runtime] 目标: $destRoot" -ForegroundColor Cyan

if ($WhatIf) {
    Write-Host "WhatIf: 将复制 node.exe, npx.cmd, npm.cmd, npm, node_modules 等到 $destRoot"
    exit 0
}

New-Item -ItemType Directory -Force -Path $destRoot | Out-Null

$items = @("node.exe", "npx.cmd", "npm.cmd", "npm", "npx")
foreach ($name in $items) {
    $p = Join-Path $src $name
    if (Test-Path $p) {
        Copy-Item -Path $p -Destination (Join-Path $destRoot $name) -Force
    }
}
$nm = Join-Path $src "node_modules"
if (Test-Path $nm) {
    $destNm = Join-Path $destRoot "node_modules"
    if (Test-Path $destNm) { Remove-Item -Recurse -Force $destNm }
    Copy-Item -Path $nm -Destination $destNm -Recurse -Force
}

Write-Host "[stage-l3-mcp-node-runtime] 完成。L3 将把裸 command=npx 解析到 $destRoot\npx.cmd" -ForegroundColor Green
