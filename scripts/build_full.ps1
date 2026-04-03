# Full Build Script（招聘/打包约定见 docs/HR_RECRUITMENT.md）
# Usage: .\scripts\build_full.ps1
# Options: -SkipTauri (L3 only), -NoClean (skip clean, incremental), -Force (force L3+Tauri rebuild)
# -SkipMcpRuntime: 跳过便携包内嵌 Python + mcp-official（Win amd64，需联网下载 embeddable CPython）

param(
    [switch]$SkipTauri,
    [switch]$NoClean,
    [switch]$Force,
    [switch]$SkipMcpRuntime
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "l3_node"))) {
    $root = $PSScriptRoot
    while ($root -and -not (Test-Path (Join-Path $root "l3_node"))) {
        $root = Split-Path -Parent $root
    }
}
if (-not $root) { Write-Error "Project root not found" }

Set-Location $root

# 1. Clean (unless -NoClean)
if (-not $NoClean) {
    Write-Host "`n[1/5] Cleaning build artifacts..." -ForegroundColor Cyan
    . "$PSScriptRoot\build_clean.ps1" -Root $root
} else {
    Write-Host "`n[1/5] Skip clean (-NoClean)" -ForegroundColor Gray
}

# 2. Build L3 Sidecar (skips if binary newer than source, unless -Force)
Write-Host "`n[2/5] Building L3 Sidecar (PyInstaller)..." -ForegroundColor Cyan
$l3Args = @()
if ($Force) { $l3Args += "--force" }
python scripts\build_l3_sidecar.py @l3Args
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERR] L3 Sidecar build failed" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Build Tauri Desktop (optional)
if (-not $SkipTauri) {
    Write-Host "`n[3/5] Building Tauri Desktop..." -ForegroundColor Cyan
    Push-Location (Join-Path $root "clients\desktop")
    npm run tauri build
    $tauriExit = $LASTEXITCODE
    Pop-Location
    if ($tauriExit -ne 0) {
        Write-Host "[ERR] Tauri build failed" -ForegroundColor Red
        exit $tauriExit
    }
} else {
    Write-Host "`n[3/5] Skip Tauri (-SkipTauri)" -ForegroundColor Gray
}

# 4. Assemble portable output (L3 轻量架构：仅 exe + 脚本 + 最小配置，MCP/Skill 通过 L1 订阅下载)
Write-Host "`n[4/5] Assembling portable output..." -ForegroundColor Cyan
$tauriTarget = Join-Path $root "clients\desktop\src-tauri\target\release"
$outDir = Join-Path $root "dist_jachin_desktop"
if (-not (Test-Path $tauriTarget)) {
    Write-Host "[WARN] Tauri target not found, copying L3 and resources only" -ForegroundColor Yellow
}

$null = New-Item -ItemType Directory -Force -Path $outDir
$outScripts = Join-Path $outDir "scripts"
$outBin = Join-Path $outDir "bin"
$outConfig = Join-Path $outDir "config"

# Copy main exe (if Tauri built)
$mainExe = Get-ChildItem $tauriTarget -Filter "*.exe" -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "l3_node" } | Select-Object -First 1
if ($mainExe) {
    Copy-Item $mainExe.FullName -Destination $outDir -Force
    Write-Host "  Copied main: $($mainExe.Name)" -ForegroundColor Gray
}

# Copy bin/l3_node
$binDir = Join-Path $root "clients\desktop\src-tauri\bin"
if (Test-Path $binDir) {
    $null = New-Item -ItemType Directory -Force -Path $outBin
    Get-ChildItem $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item $_.FullName -Destination $outBin -Force
    }
    Write-Host "  Copied bin/l3_node*.exe" -ForegroundColor Gray
}

# Copy scripts
$null = New-Item -ItemType Directory -Force -Path $outScripts
$chromeScript = Join-Path $root "scripts\launch_chrome_debug.ps1"
if (-not (Test-Path $chromeScript)) { $chromeScript = Join-Path $root "skills_repo\plugin\scripts\launch_chrome_debug.ps1" }
if (Test-Path $chromeScript) {
    Copy-Item $chromeScript -Destination $outScripts -Force
}
Copy-Item (Join-Path $root "scripts\run_l3.ps1") -Destination $outScripts -Force
if (Test-Path (Join-Path $root "scripts\run_l3.bat")) {
    Copy-Item (Join-Path $root "scripts\run_l3.bat") -Destination $outDir -Force
    Write-Host "  Copied run_l3.bat (double-click to start L3)" -ForegroundColor Gray
}
if (Test-Path (Join-Path $root "scripts\run_l3_standalone.bat")) {
    Copy-Item (Join-Path $root "scripts\run_l3_standalone.bat") -Destination $outDir -Force
    Write-Host "  Copied run_l3_standalone.bat (L2 未启动时使用)" -ForegroundColor Gray
}
Write-Host "  Copied scripts: launch_chrome_debug.ps1, run_l3.ps1" -ForegroundColor Gray

# 不复制 MCP/Skill：按架构 L3 轻量，MCP 与 Skill 通过 L1 订阅 → L2 同步 → L3 拉取到 ~/.jachin/l3_mcp_cache / l3_skill_cache

# Copy config
$null = New-Item -ItemType Directory -Force -Path $outConfig
# 创建 logs 目录（便携包日志落盘）
$null = New-Item -ItemType Directory -Force -Path (Join-Path $outDir "logs")
Write-Host "  Created logs/" -ForegroundColor Gray
if (Test-Path (Join-Path $root "config\skills_config.yaml")) {
    Copy-Item (Join-Path $root "config\skills_config.yaml") -Destination $outConfig -Force
}
if (Test-Path (Join-Path $root "config\l3_recruitment.yaml.example")) {
    Copy-Item (Join-Path $root "config\l3_recruitment.yaml.example") -Destination (Join-Path $outConfig "l3_recruitment.yaml.example") -Force
}
if (Test-Path (Join-Path $root "config\im_channels.yaml.example")) {
    Copy-Item (Join-Path $root "config\im_channels.yaml.example") -Destination (Join-Path $outConfig "im_channels.yaml.example") -Force
}
Write-Host "  Copied config/" -ForegroundColor Gray

# Copy .env.example
if (Test-Path (Join-Path $root ".env.example")) {
    Copy-Item (Join-Path $root ".env.example") -Destination $outDir -Force
}

# Copy README_DEPLOY.md (optional)
if (Test-Path (Join-Path $root "docs\README_DEPLOY.md")) {
    Copy-Item (Join-Path $root "docs\README_DEPLOY.md") -Destination $outDir -Force
} elseif (Test-Path (Join-Path $root "README_DEPLOY.md")) {
    Copy-Item (Join-Path $root "README_DEPLOY.md") -Destination $outDir -Force
}

# 5. Embedded MCP runtime（订阅 fetch 等 stdio MCP 时，零系统 Python 机器可仅用此解释器）
if (-not $SkipMcpRuntime) {
    Write-Host "`n[5/5] Bundling MCP embedded runtime (Python + official MCP wheels)..." -ForegroundColor Cyan
    # 须用 hashtable splat；@("-Root", $x) 数组展开是按「位置参数」绑定，不会按 -Name 解析
    $bundleArgs = @{ Root = $root; OutDir = $outDir }
    if ($Force) { $bundleArgs.Force = $true }
    & "$PSScriptRoot\bundle_l3_mcp_runtime.ps1" @bundleArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERR] bundle_l3_mcp_runtime.ps1 failed (use -SkipMcpRuntime to skip)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
} else {
    Write-Host "`n[5/5] Skip MCP runtime (-SkipMcpRuntime)" -ForegroundColor Gray
}

Write-Host "`n[Done] Portable output: $outDir" -ForegroundColor Green
Write-Host "  Run: $outDir\*.exe" -ForegroundColor Gray
Write-Host "  Debug L3: set JACHIN_SKIP_L3_SPAWN=1 then run $outScripts\run_l3.ps1 --ws-only" -ForegroundColor Gray
if (-not $SkipMcpRuntime) {
    Write-Host "  MCP runtime: $outDir\runtime\python\python.exe (fetch/time/git PyPI)" -ForegroundColor Gray
}
