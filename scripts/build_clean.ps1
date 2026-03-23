# 清除所有 Build 产物（参见 docs/HR_RECRUITMENT.md）
# 用法: .\scripts\build_clean.ps1
# 可选: .\scripts\build_clean.ps1 -Root "D:\path\to\project"

param([string]$Root)

$ErrorActionPreference = "Stop"
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
if (-not (Test-Path (Join-Path $Root "l3_node"))) {
    $Root = $PSScriptRoot
    while ($Root -and -not (Test-Path (Join-Path $Root "l3_node"))) {
        $Root = Split-Path -Parent $Root
    }
}
if (-not $Root) {
    $Root = (Get-Location).Path
}
if (-not $Root) { Write-Error "Project root not found" }
$root = $Root

Set-Location $root

$cleaned = @()

# PyInstaller L3 临时目录
foreach ($d in @("dist_l3", "build_l3")) {
    $p = Join-Path $root $d
    if ($p -and (Test-Path $p)) {
        Remove-Item -Recurse -Force $p
        $cleaned += $d
    }
}

# PyInstaller spec 文件
$spec = Join-Path $root "l3_node.spec"
if ($spec -and (Test-Path $spec)) {
    Remove-Item -Force $spec
    $cleaned += "l3_node.spec"
}

# Desktop 前端构建
$desktopDist = Join-Path $root "clients\desktop\dist"
if ($desktopDist -and (Test-Path $desktopDist)) {
    Remove-Item -Recurse -Force $desktopDist
    $cleaned += "clients/desktop/dist"
}

# Tauri target（可选：完整清理含 Rust 编译缓存）
$tauriTarget = Join-Path $root "clients\desktop\src-tauri\target"
if ($tauriTarget -and (Test-Path $tauriTarget)) {
    Remove-Item -Recurse -Force $tauriTarget
    $cleaned += "clients/desktop/src-tauri/target"
}

# 便携版输出目录
$portableOut = Join-Path $root "dist_jachin_desktop"
if ($portableOut -and (Test-Path $portableOut)) {
    Remove-Item -Recurse -Force $portableOut
    $cleaned += "dist_jachin_desktop"
}

# L3 Sidecar bin 目录中的 exe（可选，若需彻底重编）
$binDir = Join-Path $root "clients\desktop\src-tauri\bin"
if ($binDir -and (Test-Path $binDir)) {
    Get-ChildItem $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | Remove-Item -Force
    $cleaned += "bin/l3_node*.exe"
}

if ($cleaned.Count -gt 0) {
    Write-Host "[build_clean] 已清除: $($cleaned -join ', ')" -ForegroundColor Green
} else {
    Write-Host "[build_clean] 无待清除的 Build 产物" -ForegroundColor Gray
}
