# =============================================================================
# Layer3 one-click start (Windows)
# Single instance: kill_l3_processes.ps1 then start
#
# 说明：用 Ctrl+C 结束 tauri dev / cargo 时，Windows 上可能出现
#   error: process didn't exit successfully ... (exit code: 0xc000013a, STATUS_CONTROL_C_EXIT)
# 这是「用户中断」的正常状态码，不是编译失败。
#
# DEFAULT = desktop + source L3 in the SAME console (recommended)
#   - python -m l3_node via Start-Process -NoNewWindow (logs appear in this window, mixed with npm)
#   - then npm run tauri:dev ; JACHIN_SKIP_L3_SPAWN=1 (desktop does not spawn second L3)
#   .\scripts\start-layer3.ps1
#   Optional old behavior (second window): -SeparateL3Window
#   （已默认行为）桌面会随启动打开控制台 + Omni；无需 -ShowOmni。自动化可设 JACHIN_SKIP_STARTUP_WINDOWS=1
#   （默认仅托盘图标；也可左键托盘或 Alt+Shift+Space）
#
# Source only (no Tauri, one window):
#   .\scripts\start-layer3.ps1 -SourceOnly
#
# Desktop only (Tauri spawns Sidecar L3; no background python window):
#   .\scripts\start-layer3.ps1 -DesktopOnly
# =============================================================================
param(
    [switch]$WsOnly,
    [switch]$SourceOnly,
    [switch]$DesktopOnly,
    [switch]$SeparateL3Window,
    [switch]$SkipRepairMcp,
    [switch]$ShowOmni
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# 尽量让传统 conhost 用 UTF-8 代码页输出中文（Windows Terminal 通常已 OK）
try {
    if ($env:OS -match 'Windows') {
        & cmd.exe /c "chcp 65001>nul" 2>$null
    }
} catch { }

function Resolve-PythonExePath {
    $c = @(Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1)[0]
    if (-not $c) { return $null }
    foreach ($p in @($c.Source, $c.Path, $c.Definition)) {
        if ($p -and ($p -match '\.(exe|EXE)$') -and (Test-Path -LiteralPath $p)) { return $p }
    }
    foreach ($p in @($c.Source, $c.Path, $c.Definition)) {
        if ($p) { return $p }
    }
    return $null
}

if ($SourceOnly -and $DesktopOnly) {
    Write-Host "[Layer3] ERROR: use -SourceOnly OR -DesktopOnly, not both." -ForegroundColor Red
    exit 1
}

$env:JACHIN_APP_ROOT = $ProjectRoot
$env:JACHIN_DEV_HR_FIRST = "1"
$ErrorActionPreference = "Continue"

# 修正 ~/.jachin/mcp_servers.json 里过期的 hr-atomic-tools 路径（换仓库目录名后常见），避免拖死整轮 MCP 握手
# （repair_mcp_servers.py 以 utf-8-sig 读取，兼容带 BOM 的 JSON，与 L3 mcp_client 一致）
if (-not $SkipRepairMcp) {
    try {
        & python (Join-Path $ScriptDir "repair_mcp_servers.py") --project-root $ProjectRoot
    } catch {
        Write-Host "[Layer3] repair_mcp_servers.py 跳过: $_" -ForegroundColor DarkGray
    }
}

# Desktop+Sidecar path: do not skip spawn. Dual path: skip so desktop does not start a second L3.
if ($DesktopOnly) {
    Remove-Item Env:\JACHIN_SKIP_L3_SPAWN -ErrorAction SilentlyContinue
} else {
    if (-not $SourceOnly) {
        $env:JACHIN_SKIP_L3_SPAWN = "1"
    } else {
        Remove-Item Env:\JACHIN_SKIP_L3_SPAWN -ErrorAction SilentlyContinue
    }
}

if ($ShowOmni) {
    Write-Host "[Layer3] -ShowOmni 已省略效：桌面默认会打开 Omni 与控制台" -ForegroundColor DarkGray
}

# Memory Nexus：须与「本脚本将调用的 python」同一解释器安装 fastembed（避免 base 里装了但 PATH 指向别的 python）
Write-Host "[Layer3] Python probe (pip install fastembed must target same exe):" -ForegroundColor Gray
try {
    $pyProbe = Resolve-PythonExePath
    if (-not $pyProbe) { throw "python not in PATH" }
    Write-Host "  python -> $pyProbe" -ForegroundColor Gray
    & $pyProbe -c "import sys, importlib.util as u; print('  sys.executable =', sys.executable); print('  fastembed_find_spec =', bool(u.find_spec('fastembed')))"
} catch {
    Write-Host "  [WARN] python probe failed: $_" -ForegroundColor Yellow
}

function Start-L3SourceForeground {
    param([string]$Mode)
    $pyArgs = @("-m", "l3_node")
    if ($Mode -eq "ws") {
        $pyArgs += "--ws-only"
        Write-Host "[Layer3] python -m l3_node --ws-only" -ForegroundColor Cyan
    } else {
        $pyArgs += "--gateway"
        Write-Host "[Layer3] python -m l3_node --gateway" -ForegroundColor Cyan
    }
    Write-Host "[Layer3] cwd=$ProjectRoot" -ForegroundColor Gray
    & python @pyArgs
    exit $LASTEXITCODE
}

try {
    if ($SourceOnly) {
        Write-Host "[Layer3] kill old L3..." -ForegroundColor Gray
        & (Join-Path $ScriptDir "kill_l3_processes.ps1") -NoPause
    } else {
        Write-Host "[Layer3] 检查并清理已有 L3 实例..." -ForegroundColor Gray
        # 桌面启 Tauri 前结束本仓库 target 下 jachin-desktop，避免 single-instance 双开秒退
        & (Join-Path $ScriptDir "kill_l3_processes.ps1") -NoPause -AlsoKillDesktopDev
    }

    # 本地热更新 / 桌面联调：释放 Vite 31421，避免上次 dev 残留占用
    if (-not $SourceOnly) {
        Write-Host "[Layer3] 检查 Vite 前端端口 31421..." -ForegroundColor Gray
        $viteListen = netstat -ano 2>$null | Select-String ":31421\s" | Select-String "LISTENING"
        if ($viteListen) {
            Write-Host "  端口 31421 已被占用，正在释放（多为上次 npm run dev / tauri dev 未退出）..." -ForegroundColor Yellow
            & (Join-Path $ScriptDir "kill_port.ps1") -Port 31421
        } else {
            Write-Host "  端口 31421 未被占用" -ForegroundColor Gray
        }
        Write-Host ""
    }

    $pyMode = if ($WsOnly) { "--ws-only" } else { "--gateway" }

    if ($SourceOnly) {
        if ($WsOnly) {
            Start-L3SourceForeground -Mode "ws"
        } else {
            Start-L3SourceForeground -Mode "gateway"
        }
    }

    if (-not $SourceOnly) {
        if (-not $DesktopOnly) {
            if ($SeparateL3Window) {
                Write-Host "[Layer3] separate window: python -m l3_node $pyMode" -ForegroundColor Cyan
                $prEsc = $ProjectRoot.Replace("'", "''")
                $setEnc = '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8'
                $psCmd = (
                    '$Host.UI.RawUI.WindowTitle = ''Jachin L3 (source)''; Set-Location -LiteralPath ''{0}''; $env:JACHIN_APP_ROOT = ''{0}''; $env:JACHIN_DEV_HR_FIRST = 1; $env:PYTHONUTF8 = 1; {1}; & python -m l3_node {2}' `
                        -f $prEsc, $setEnc, $pyMode
                )
                Start-Process -FilePath "powershell.exe" -ArgumentList @(
                    "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $psCmd
                )
            } else {
                Write-Host "[Layer3] same console: python -m l3_node $pyMode (L3 logs below mix with npm)" -ForegroundColor Cyan
                $pyExe = Resolve-PythonExePath
                if (-not $pyExe) {
                    Write-Host "[Layer3] ERROR: python not found in PATH." -ForegroundColor Red
                    exit 1
                }
                $argList = @("-m", "l3_node")
                if ($WsOnly) { $argList += "--ws-only" } else { $argList += "--gateway" }
                # 子进程继承当前 PowerShell 的环境（脚本已设置 JACHIN_APP_ROOT 等）。
                # 不用 ProcessStartInfo：部分 Windows PowerShell 上 New-Object 得到的对象无 FileName 属性（.NET/宿主差异）。
                Write-Host "[Layer3] Start-Process: $pyExe $($argList -join ' ')" -ForegroundColor Gray
                Start-Process -FilePath $pyExe -WorkingDirectory $ProjectRoot -ArgumentList $argList -NoNewWindow
            }
            Start-Sleep -Seconds 2
            Write-Host "[Layer3] JACHIN_SKIP_L3_SPAWN=1 (Tauri window does not spawn second L3)" -ForegroundColor Gray
        }

        $DesktopDir = Join-Path $ProjectRoot "clients\desktop"
        $at = [char]64
        if (-not (Test-Path $DesktopDir)) {
            Write-Host "[ERROR] clients\desktop not found. Run: .\scripts\install-layer3.ps1" -ForegroundColor Red
            exit 1
        }
        if (-not (Test-Path (Join-Path $DesktopDir "node_modules"))) {
            Write-Host "[INFO] Installing dependencies..." -ForegroundColor Yellow
            Push-Location $DesktopDir
            npm install
            Pop-Location
        }

        # 对齐当前 Cargo 版本路径，避免 Tauri 仅因缺 exe 失败；需要完整 Sidecar 时再走 PyInstaller
        Write-Host "[Layer3] 确保 Sidecar 路径存在（stub 按需）..." -ForegroundColor Gray
        & python (Join-Path $ScriptDir "create_l3_stub.py") --if-missing
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] create_l3_stub.py 失败" -ForegroundColor Red
            exit 1
        }

        $BinDir = Join-Path $DesktopDir "src-tauri\bin"
        $L3Exe = Get-ChildItem -Path $BinDir -Filter "l3_node-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $L3Exe) {
            Write-Host ""
            Write-Host "[Layer3] L3 Sidecar missing, building (PyInstaller)..." -ForegroundColor Yellow
            & python (Join-Path $ScriptDir "build_l3_sidecar.py")
            if ($LASTEXITCODE -ne 0) {
                Write-Host ""
                Write-Host "[Layer3] build failed, trying stub..." -ForegroundColor Yellow
                & python (Join-Path $ScriptDir "create_l3_stub.py")
                if ($LASTEXITCODE -ne 0) {
                    $sidecarHint = Join-Path $ScriptDir "build_l3_sidecar.py"
                    Write-Host "[ERROR] pip install pyinstaller ; python $sidecarHint" -ForegroundColor Red
                    exit 1
                }
                $sidecarHint = Join-Path $ScriptDir "build_l3_sidecar.py"
                Write-Host "[WARN] stub only; full: python $sidecarHint" -ForegroundColor Yellow
            } else {
                Write-Host "[Layer3] Sidecar build OK" -ForegroundColor Green
            }
            Write-Host ""
        }

        $UtcNow = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        Write-Host ""
        Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
        Write-Host "[$UtcNow]   Layer3 + Desktop" -ForegroundColor Cyan
        Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
        Write-Host "[$UtcNow] L2: .\scripts\run-gateway.ps1" -ForegroundColor Yellow
        if ($DesktopOnly) {
            Write-Host "[$UtcNow] Mode: desktop only (Tauri may spawn Sidecar L3)" -ForegroundColor Gray
        } else {
            if ($SeparateL3Window) {
                Write-Host "[$UtcNow] Mode: L3 in other window ; this window = npm/Tauri" -ForegroundColor Yellow
            } else {
                Write-Host "[$UtcNow] Mode: L3 + npm share this console (output interleaved)" -ForegroundColor Yellow
            }
        }
        Write-Host "[$UtcNow] Ctrl+C usually stops npm first; L3 may keep running (use kill_l3_processes.ps1)" -ForegroundColor Gray
        Write-Host ""

        Write-Host "[$UtcNow] Skills 为空可运行: .\scripts\diagnose-skill-sync.ps1" -ForegroundColor Gray
        Write-Host "[$UtcNow] 热更新联调: npm run tauri:dev:with-updater（日常开发: npm run tauri:dev）" -ForegroundColor Gray
        Write-Host "[$UtcNow] 发布/签名: 仓库根 npm run publish-desktop-release（见 clients/desktop/package.json）" -ForegroundColor Gray

        Push-Location $DesktopDir
        $tauriPkgDir = $at + "tauri-apps"
        $tauriBin = Join-Path (Join-Path (Join-Path $DesktopDir "node_modules") $tauriPkgDir) "cli"
        $hasLocalTauriCli = (Test-Path $tauriBin)
        $hasGlobalTauri = [bool](Get-Command tauri -ErrorAction SilentlyContinue)
        if ($hasLocalTauriCli -or $hasGlobalTauri) {
            npm run tauri:dev
        } else {
            Write-Host "[INFO] @tauri-apps/cli not found. npm install first; else npm run dev (Vite only)." -ForegroundColor Yellow
            npm run dev
        }
        Pop-Location
        exit $LASTEXITCODE
    }
} finally {
    Write-Host ""
    if ($env:JACHIN_PAUSE_ON_EXIT -eq "1") {
        Read-Host "Press Enter to exit"
    }
}
