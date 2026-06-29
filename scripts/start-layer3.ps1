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
#   - then npm run tauri:dev:ambient（默认，含 VAD/语音唤起）；JACHIN_SKIP_L3_SPAWN=1
#   - 不需要 ambient 时：.\scripts\start-layer3.ps1 -NoAmbient
#   - 同脚本会尝试启动 JVS voice_server (18982)，供陪伴语音 TTS/STT
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
    [switch]$ShowOmni,
    [switch]$NoAmbient,
    [switch]$NoPause
)

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force -ErrorAction SilentlyContinue

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$script:Layer3ExitCode = 0

function Ensure-TauriJsonNoBom {
    $mjs = Join-Path $ProjectRoot "clients\desktop\scripts\ensure-json-no-bom.mjs"
    if (-not (Test-Path -LiteralPath $mjs)) { return }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Host "[Layer3] WARN: node 不在 PATH，跳过 ensure-json-no-bom（若 tauri dev 报 JSON 解析错误，请先安装 Node）" -ForegroundColor Yellow
        return
    }
    Write-Host "[Layer3] 检查 Tauri/Vite 关键 JSON 是否带 BOM..." -ForegroundColor Gray
    & node $mjs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[Layer3] WARN: ensure-json-no-bom 退出码 $LASTEXITCODE" -ForegroundColor Yellow
    }
}

function Wait-Layer3PauseIfNeeded {
    if ($NoPause) { return }
    if ($env:CI -eq "true") { return }
    if ($env:JACHIN_PAUSE_ON_EXIT -eq "0") { return }
    $code = if ($script:Layer3ExitCode -ne 0) { $script:Layer3ExitCode } else { $LASTEXITCODE }
    if ($env:JACHIN_PAUSE_ON_EXIT -eq "1" -or ($code -ne 0 -and $null -ne $code)) {
        if ($code -ne 0) {
            Write-Host "[Layer3] 异常退出，代码: $code" -ForegroundColor Red
        }
        Read-Host "按 Enter 关闭此窗口"
    }
}

# 尽量让传统 conhost 用 UTF-8 代码页输出中文（Windows Terminal 通常已 OK）
try {
    if ($env:OS -match 'Windows') {
        & cmd.exe /c "chcp 65001>nul" 2>$null
    }
} catch { }

Ensure-TauriJsonNoBom

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
# L2 白名单非空时放行本地已注册 MCP（Puppeteer / browser-use / K11）；与 l3_node tool_pool.expand_allowed_skills_with_local_mcp 一致
$env:JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL = "1"
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

function Stop-TcpPortListener {
    param([int]$Port)
    $connections = @(netstat -ano 2>$null | Select-String ":$Port\s" | Select-String "LISTENING")
    if (-not $connections.Count) { return $false }
    $processIds = $connections | ForEach-Object { $_.ToString().Split()[-1] } | Select-Object -Unique
    foreach ($processId in $processIds) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
        } catch {
            Start-Process -FilePath "taskkill" -ArgumentList "/F", "/PID", $processId -Wait -NoNewWindow -ErrorAction SilentlyContinue | Out-Null
        }
    }
    return $true
}

function Resolve-VoicePythonExe {
    $venvPy = Join-Path $ProjectRoot ".venv-voice\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }
    return Resolve-PythonExePath
}

function Start-JvsVoiceServer {
    param(
        # start-layer3 一键启动时强制重启 JVS，避免旧进程无 CORS 等修复仍占用 18982
        [switch]$Refresh
    )
    $baseUrl = if ($env:JACHIN_VOICE_SERVER_URL) { $env:JACHIN_VOICE_SERVER_URL.TrimEnd('/') } else { "http://127.0.0.1:18982" }
    $healthUrl = "$baseUrl/health"

    if ($Refresh) {
        Write-Host "[Layer3] 重启 JVS voice_server（18982）以加载当前代码..." -ForegroundColor Cyan
        if (Stop-TcpPortListener -Port 18982) {
            Start-Sleep -Seconds 1
        }
    } else {
        try {
            $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
            if ($h.ok -and $h.tts_ready) {
                Write-Host "[Layer3] JVS 已就绪 ($healthUrl, tts_ready=true)" -ForegroundColor Green
                return
            }
            if ($h.ok) {
                Write-Host "[Layer3] JVS 已监听，模型仍在预热 (tts_ready=false)..." -ForegroundColor Yellow
            }
        } catch {
            Write-Host "[Layer3] JVS 未响应，准备启动 voice_server..." -ForegroundColor Cyan
        }
    }

    $mainPy = Join-Path $ProjectRoot "voice_server\main.py"
    if (-not (Test-Path -LiteralPath $mainPy)) {
        Write-Host "[Layer3] WARN: 未找到 voice_server\main.py，跳过 JVS（语音将不可用）" -ForegroundColor Yellow
        return
    }

    $pyExe = Resolve-VoicePythonExe
    if (-not $pyExe) {
        Write-Host "[Layer3] WARN: 找不到 python，无法启动 JVS" -ForegroundColor Yellow
        return
    }

    $modelRoot = if ($env:JACHIN_VOICE_MODEL_ROOT) { $env:JACHIN_VOICE_MODEL_ROOT } else { Join-Path $ProjectRoot "data\models\voice" }
    Write-Host "[Layer3] 启动 JVS: $pyExe voice_server\main.py (模型预热可能需 30~90s)" -ForegroundColor Cyan
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $pyExe
        $psi.Arguments = $mainPy
        $psi.WorkingDirectory = $ProjectRoot
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $false
        $evs = $psi.EnvironmentVariables
        foreach ($de in [System.Environment]::GetEnvironmentVariables([System.EnvironmentVariableTarget]::Process).GetEnumerator()) {
            $k = $de.Key.ToString()
            try { $evs[$k] = $de.Value.ToString() } catch { }
        }
        $evs["JACHIN_VOICE_MODEL_ROOT"] = $modelRoot
        $evs["PYTHONUNBUFFERED"] = "1"
        $evs["PYTHONUTF8"] = "1"
        [void][System.Diagnostics.Process]::Start($psi)
    } catch {
        Write-Host "[Layer3] WARN: JVS 启动失败: $_" -ForegroundColor Yellow
        return
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
            if ($h.ok -and $h.tts_ready) {
                Write-Host "[Layer3] JVS 就绪: tts_voice=$($h.tts_voice) model_root=$($h.model_root)" -ForegroundColor Green
                return
            }
            if ($h.ok) {
                Write-Host "[Layer3] JVS 监听中，等待 TTS 模型预热..." -ForegroundColor Gray
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Write-Host "[Layer3] WARN: JVS 120s 内 tts_ready 仍为 false；请检查 data\models\voice 与 pip install -r voice_server\requirements.txt" -ForegroundColor Yellow
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
    $script:Layer3ExitCode = if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 0 }
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
            if (Stop-TcpPortListener -Port 31421) {
                Write-Host "  已尝试释放端口 31421" -ForegroundColor Gray
            }
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
        exit $script:Layer3ExitCode
    }

    if (-not $SourceOnly) {
        if (-not $DesktopOnly) {
            if ($SeparateL3Window) {
                Write-Host "[Layer3] separate window: python -m l3_node $pyMode" -ForegroundColor Cyan
                $prEsc = $ProjectRoot.Replace("'", "''")
                $setEnc = '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8'
                $psCmd = (
                    '$Host.UI.RawUI.WindowTitle = ''Jachin L3 (source)''; Set-Location -LiteralPath ''{0}''; $env:JACHIN_APP_ROOT = ''{0}''; $env:JACHIN_DEV_HR_FIRST = 1; $env:JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL = ''1''; $env:PYTHONUTF8 = 1; {1}; & python -m l3_node {2}' `
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
                # 优先用 ProcessStartInfo 显式复制本进程环境并写入 JACHIN_*，避免少数机器上子进程未继承 $env:。
                # FileName 一律用上面的 $pyExe（与 Resolve-PythonExePath 一致，勿用未解析变量）。
                $argLine = $argList -join ' '
                Write-Host "[Layer3] python child: $pyExe $argLine" -ForegroundColor Gray
                try {
                    $psi = New-Object System.Diagnostics.ProcessStartInfo
                    $psi.FileName = $pyExe
                    $psi.Arguments = $argLine
                    $psi.WorkingDirectory = $ProjectRoot
                    $psi.UseShellExecute = $false
                    $psi.CreateNoWindow = $false
                    $evs = $psi.EnvironmentVariables
                    foreach ($de in [System.Environment]::GetEnvironmentVariables([System.EnvironmentVariableTarget]::Process).GetEnumerator()) {
                        $k = $de.Key.ToString()
                        try { $evs[$k] = $de.Value.ToString() } catch { }
                    }
                    $evs["JACHIN_APP_ROOT"] = $ProjectRoot
                    $evs["JACHIN_DEV_HR_FIRST"] = "1"
                    $evs["JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL"] = "1"
                    $evs["PYTHONUTF8"] = "1"
                    $proc = [System.Diagnostics.Process]::Start($psi)
                    if ($null -ne $proc) { [void]$proc.Id }
                } catch {
                    Write-Host "[Layer3] ProcessStartInfo 启动失败，回退 Start-Process: $_" -ForegroundColor Yellow
                    Start-Process -FilePath $pyExe -WorkingDirectory $ProjectRoot -ArgumentList $argList -NoNewWindow
                }
            }
            Start-Sleep -Seconds 2
            Write-Host "[Layer3] JACHIN_SKIP_L3_SPAWN=1 (Tauri window does not spawn second L3)" -ForegroundColor Gray
            Start-JvsVoiceServer -Refresh
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
            Start-JvsVoiceServer -Refresh
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
        Write-Host "[$UtcNow] 热更新联调: npm run tauri:dev:with-updater（日常开发: npm run tauri:dev:ambient）" -ForegroundColor Gray
        Write-Host "[$UtcNow] 发布/签名: 仓库根 npm run publish-desktop-release（见 clients/desktop/package.json）" -ForegroundColor Gray

        if (-not $NoAmbient) {
            $env:JACHIN_AUTO_WAKE_LISTENER = "1"
            Write-Host "[$UtcNow] 语音唤起: JACHIN_AUTO_WAKE_LISTENER=1（桌面启动后自动监听麦克风）" -ForegroundColor Gray
            $vadPath = Join-Path (Join-Path (Join-Path $env:LOCALAPPDATA "jachin") "desktop") "data\vad\silero_vad.onnx"
            if (-not (Test-Path -LiteralPath $vadPath)) {
                Write-Host "[$UtcNow] VAD 模型缺失，自动下载到用户目录..." -ForegroundColor Yellow
                try {
                    $env:JACHIN_VAD_DEBUG_PATH = (Join-Path (Join-Path $env:LOCALAPPDATA "jachin") "desktop\data")
                    & python (Join-Path $ScriptDir "download_vad_model.py")
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "[$UtcNow] WARN: 自动下载 VAD 失败，可手动执行: python scripts\download_vad_model.py" -ForegroundColor Yellow
                    } else {
                        Write-Host "[$UtcNow] VAD 模型就绪: $vadPath" -ForegroundColor Green
                    }
                } catch {
                    Write-Host "[$UtcNow] WARN: 自动下载 VAD 失败: $_" -ForegroundColor Yellow
                }
            }
        }
        Push-Location $DesktopDir
        Ensure-TauriJsonNoBom
        $tauriPkgDir = $at + "tauri-apps"
        $tauriBin = Join-Path (Join-Path (Join-Path $DesktopDir "node_modules") $tauriPkgDir) "cli"
        $hasLocalTauriCli = (Test-Path $tauriBin)
        $hasGlobalTauri = [bool](Get-Command tauri -ErrorAction SilentlyContinue)
        $tauriDevScript = if ($NoAmbient) { "tauri:dev" } else { "tauri:dev:ambient" }
        if ($NoAmbient) {
            Write-Host "[$UtcNow] Tauri: npm run tauri:dev（未启用 ambient，无 VAD/语音唤起）" -ForegroundColor Yellow
        } else {
            Write-Host "[$UtcNow] Tauri: npm run tauri:dev:ambient（VAD + 语音唤起）" -ForegroundColor Gray
        }
        if ($hasLocalTauriCli -or $hasGlobalTauri) {
            npm run $tauriDevScript
        } else {
            Write-Host "[INFO] @tauri-apps/cli not found. npm install first; else npm run dev (Vite only)." -ForegroundColor Yellow
            npm run dev
        }
        Pop-Location
        $script:Layer3ExitCode = if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 0 }
        exit $script:Layer3ExitCode
    }
} catch {
    Write-Host "[Layer3] FATAL: $_" -ForegroundColor Red
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    }
    $script:Layer3ExitCode = 1
    exit 1
} finally {
    Wait-Layer3PauseIfNeeded
}
