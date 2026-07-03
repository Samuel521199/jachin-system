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

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    } catch {
        try {
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        } catch { }
    }
}

function Invoke-TauriBuildNonInteractive {
    param(
        [string]$DesktopDir,
        [string]$RootDir
    )

    $npm = @(Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1)[0]
    if (-not $npm) {
        $npm = @(Get-Command npm -ErrorAction SilentlyContinue | Select-Object -First 1)[0]
    }
    if (-not $npm) {
        Write-Host "[ERR] npm not found in PATH" -ForegroundColor Red
        return 1
    }

    $logDir = Join-Path $RootDir "output\build_logs"
    $null = New-Item -ItemType Directory -Force -Path $logDir
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $stdoutLog = Join-Path $logDir "tauri_build_$stamp.stdout.log"
    $stderrLog = Join-Path $logDir "tauri_build_$stamp.stderr.log"
    $targetRelease = Join-Path $DesktopDir "src-tauri\target\release"
    $mainExe = Join-Path $targetRelease "jachin-desktop.exe"

    Write-Host "  Tauri build log:" -ForegroundColor Gray
    Write-Host "    stdout: $stdoutLog" -ForegroundColor Gray
    Write-Host "    stderr: $stderrLog" -ForegroundColor Gray

    "" | Set-Content -LiteralPath $stdoutLog -Encoding UTF8
    "" | Set-Content -LiteralPath $stderrLog -Encoding UTF8
    $oldSkipL3Prebuild = $env:JACHIN_SKIP_L3_PREBUILD
    $oldRustFlags = $env:RUSTFLAGS
    $env:JACHIN_SKIP_L3_PREBUILD = "1"
    if (-not $env:RUSTFLAGS) {
        $env:RUSTFLAGS = "-C linker=rust-lld"
        Write-Host "  RUSTFLAGS: -C linker=rust-lld（规避 Windows link.exe LNK1105 文件占用）" -ForegroundColor Gray
    } elseif ($env:RUSTFLAGS -notmatch "linker=") {
        $env:RUSTFLAGS = "$($env:RUSTFLAGS) -C linker=rust-lld"
        Write-Host "  RUSTFLAGS appended: -C linker=rust-lld" -ForegroundColor Gray
    }
    try {
        $proc = Start-Process `
            -FilePath $npm.Source `
            -ArgumentList @("run", "tauri", "build") `
            -WorkingDirectory $DesktopDir `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        if ($null -eq $oldSkipL3Prebuild) {
            Remove-Item Env:\JACHIN_SKIP_L3_PREBUILD -ErrorAction SilentlyContinue
        } else {
            $env:JACHIN_SKIP_L3_PREBUILD = $oldSkipL3Prebuild
        }
        if ($null -eq $oldRustFlags) {
            Remove-Item Env:\RUSTFLAGS -ErrorAction SilentlyContinue
        } else {
            $env:RUSTFLAGS = $oldRustFlags
        }
    }

    $startedAt = [DateTime]::UtcNow
    $bundleSeenAt = $null
    $timeoutAt = $startedAt.AddMinutes(45)

    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 2
        $stdoutText = ""
        $stderrText = ""
        try {
            if (Test-Path -LiteralPath $stdoutLog) {
                $stdoutText = Get-Content -LiteralPath $stdoutLog -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $stderrLog) {
                $stderrText = Get-Content -LiteralPath $stderrLog -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            }
        } catch { }
        $combinedText = "$stdoutText`n$stderrText"
        if ($combinedText -match "Finished\s+\d+\s+bundle\s+at:") {
            if ($null -eq $bundleSeenAt) { $bundleSeenAt = [DateTime]::UtcNow }
        }
        $bundleComplete = ($null -ne $bundleSeenAt) -and (Test-Path -LiteralPath $mainExe)
        if ($bundleComplete -and ([DateTime]::UtcNow - $bundleSeenAt).TotalSeconds -ge 8) {
            Write-Host "  Tauri build 已生成 bundle，但 npm/tauri 进程未自动退出；自动收尾继续打包。" -ForegroundColor Yellow
            Stop-ProcessTree -ProcessId $proc.Id
            Start-Sleep -Seconds 1
            return 0
        }

        if ([DateTime]::UtcNow -gt $timeoutAt) {
            Write-Host "[ERR] Tauri build timeout after 45 minutes" -ForegroundColor Red
            Stop-ProcessTree -ProcessId $proc.Id
            Start-Sleep -Seconds 1
            return 124
        }
    }

    try {
        $proc.WaitForExit()
        $proc.Refresh()
    } catch { }
    $code = $proc.ExitCode
    if ($null -eq $code) {
        $code = 0
    }
    if ($code -ne 0) {
        $stdoutText = ""
        $stderrText = ""
        try {
            if (Test-Path -LiteralPath $stdoutLog) {
                $stdoutText = Get-Content -LiteralPath $stdoutLog -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $stderrLog) {
                $stderrText = Get-Content -LiteralPath $stderrLog -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            }
        } catch { }
        $combinedText = "$stdoutText`n$stderrText"
        if (($combinedText -match "Finished\s+\d+\s+bundle\s+at:") -and (Test-Path -LiteralPath $mainExe)) {
            Write-Host "  Tauri build 已生成 bundle，但进程退出码为 $code；按成功产物继续打包。" -ForegroundColor Yellow
            return 0
        }
    }
    if ($code -ne 0) {
        Write-Host "  ---- tauri stdout tail ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $stdoutLog -Tail 80 -Encoding UTF8 -ErrorAction SilentlyContinue
        Write-Host "  ---- tauri stderr tail ----" -ForegroundColor Yellow
        Get-Content -LiteralPath $stderrLog -Tail 120 -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    return $code
}

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
    $tauriExit = Invoke-TauriBuildNonInteractive -DesktopDir (Join-Path $root "clients\desktop") -RootDir $root
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

# Copy .env.example（模板）；.env 与 clients/desktop/scripts/prepare-installer-payload.mjs 同优先级
$envExamplePath = Join-Path $root ".env.example"
$envRootPath = Join-Path $root ".env"
$envDstPortable = Join-Path $outDir ".env"
if (Test-Path $envExamplePath) {
    Copy-Item $envExamplePath -Destination $outDir -Force
    Write-Host "  Copied .env.example" -ForegroundColor Gray
}
$bundleSrc = $null
$fromBundleEnv = $env:JACHIN_DESKTOP_BUNDLE_ENV_FILE
if ($fromBundleEnv -and $fromBundleEnv.Trim()) {
    $p = $fromBundleEnv.Trim()
    if (Test-Path $p) { $bundleSrc = $p }
}
if (-not $bundleSrc) {
    $pointerPath = Join-Path $root "clients\desktop\.jachin_bundle_env_path"
    if (Test-Path $pointerPath) {
        foreach ($line in Get-Content $pointerPath) {
            $t = $line.Trim()
            if (-not $t -or $t.StartsWith("#")) { continue }
            if (Test-Path $t) { $bundleSrc = $t; break }
        }
    }
}
if (-not $bundleSrc -and (Test-Path $envRootPath)) {
    $bundleSrc = $envRootPath
}
if ($bundleSrc) {
    Copy-Item $bundleSrc -Destination $envDstPortable -Force
    Write-Host "  Copied .env <- $bundleSrc" -ForegroundColor Gray
} elseif (Test-Path $envExamplePath) {
    Copy-Item $envExamplePath -Destination $envDstPortable -Force
    Write-Host "  Copied .env from .env.example (no repo .env / override)" -ForegroundColor Gray
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
