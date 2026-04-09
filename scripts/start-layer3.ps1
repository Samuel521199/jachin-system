# =============================================================================
# Layer3 (Desktop) - One-click start (Windows)
# clients/desktop - Jachin Terminal
# 单实例：启动前清理已有 L3 进程与端口，确保一台机器仅一个 L3 实例
#
# 仅源码 L3（前台、无桌面）：
#   .\scripts\start-layer3.ps1 -Source
#   等价项目根: python -m l3_node --gateway
#   仅 WS：.\scripts\start-layer3.ps1 -Source -WsOnly
#
# 仅桌面（前台）：Tauri/Vite 启动后由桌面自动拉起 L3，优先编译 Sidecar（bin/l3_node-*.exe），失败可回退 python
#   .\scripts\start-layer3.ps1
#
# 桌面 + 后台源码 L3（双开）：
#   .\scripts\start-layer3.ps1 -WithBackgroundSource
#   - 新窗口后台：python -m l3_node（必须用仓库代码，cwd=项目根）
#   - 前台：npm tauri:dev / npm run dev，并设置 JACHIN_SKIP_L3_SPAWN=1，避免桌面再启第二个 L3（端口冲突）
#   - 与 -Source 互斥
# =============================================================================
param(
    [switch]$Source,
    [switch]$WsOnly,
    [switch]$WithBackgroundSource
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

if ($Source -and $WithBackgroundSource) {
    Write-Host "[Layer3] 错误：不可同时使用 -Source 与 -WithBackgroundSource。" -ForegroundColor Red
    Write-Host "  -Source        = 仅前台源码 L3，不启桌面" -ForegroundColor Gray
    Write-Host "  -WithBackgroundSource = 后台源码 L3 新窗口 + 前台桌面（桌面内不再自动起 L3）" -ForegroundColor Gray
    exit 1
}

# 桌面进程继承本脚本环境：双开时必须跳过 Tauri 内自动 spawn（见 clients/desktop l3_spawn.rs）
if ($WithBackgroundSource -and -not $Source) {
    $env:JACHIN_SKIP_L3_SPAWN = "1"
} else {
    Remove-Item Env:\JACHIN_SKIP_L3_SPAWN -ErrorAction SilentlyContinue
}

# HR 招聘：Sidecar/L3 加载 recruitment_scheduler 时默认先读 ~/.jachin/l3_mcp_cache，易与仓库代码不一致
$env:JACHIN_APP_ROOT = $ProjectRoot
$env:JACHIN_DEV_HR_FIRST = "1"
$ErrorActionPreference = "Continue"

try {
    Write-Host "[Layer3] 检查并清理已有 L3 实例..." -ForegroundColor Gray
    & (Join-Path $ScriptDir "kill_l3_processes.ps1") -NoPause

    # Tauri dev 的 beforeDevCommand 会起 Vite，固定端口 31421（见 clients/desktop/vite.config.ts 与 tauri.conf.json devUrl）
    if (-not $Source) {
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

    if ($WithBackgroundSource -and -not $Source) {
        $pyMode = if ($WsOnly) { "--ws-only" } else { "--gateway" }
        Write-Host "[Layer3] 后台：新窗口启动源码 L3  python -m l3_node $pyMode  (cwd=$ProjectRoot)" -ForegroundColor Cyan
        $prEsc = $ProjectRoot.Replace("'", "''")
        # 子窗口脚本：用单引号模板 + -f 拼接，避免外层双引号与 '、$prEsc、[Type]:: 组合触发解析歧义
        $setEnc = '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8'
        $psCmd = (
            '$Host.UI.RawUI.WindowTitle = ''Jachin L3 (source)''; Set-Location -LiteralPath ''{0}''; $env:JACHIN_APP_ROOT = ''{0}''; $env:JACHIN_DEV_HR_FIRST = 1; $env:PYTHONUTF8 = 1; {1}; & python -m l3_node {2}' `
                -f $prEsc, $setEnc, $pyMode
        )
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $psCmd
        )
        Start-Sleep -Seconds 2
        Write-Host "[Layer3] 已设置 JACHIN_SKIP_L3_SPAWN=1，桌面不会自动再起 L3（引擎以后台窗口为准）。" -ForegroundColor Gray
    }

    if ($Source) {
        $pyArgs = @("-m", "l3_node")
        if ($WsOnly) {
            $pyArgs += "--ws-only"
            Write-Host "[Layer3] Source mode: python -m l3_node --ws-only" -ForegroundColor Cyan
        } else {
            $pyArgs += "--gateway"
            Write-Host "[Layer3] Source mode: python -m l3_node --gateway" -ForegroundColor Cyan
        }
        Write-Host "[Layer3] cwd=$ProjectRoot" -ForegroundColor Gray
        & python @pyArgs
        exit $LASTEXITCODE
    }

    $DesktopDir = Join-Path $ProjectRoot "clients\desktop"
    # 字面量 @：避免在源码中出现 "'@' + …" 或 "= '@' + …" 等片段，部分 PS 版本会把 @' 解析为 here-string
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

    # Tauri externalBin：必须存在「当前 Cargo 版本」对应的 l3_node-<ver>-<triple>.exe；仅有旧版 exe 时仍会构建失败
    $BinDir = Join-Path $DesktopDir "src-tauri\bin"
    Write-Host "[Layer3] 确保当前版本 Sidecar 路径存在（供 Tauri 构建；已有真实 exe 则保留）..." -ForegroundColor Gray
    & python (Join-Path $ScriptDir "create_l3_stub.py") --if-missing
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] create_l3_stub.py 失败" -ForegroundColor Red
        exit 1
    }

    $L3Exe = Get-ChildItem -Path $BinDir -Filter "l3_node-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $L3Exe) {
        Write-Host ""
        Write-Host "[Layer3] 仍未找到 l3_node-*.exe，尝试完整打包 Sidecar (需 PyInstaller)..." -ForegroundColor Yellow
        & python (Join-Path $ScriptDir "build_l3_sidecar.py")
        if ($LASTEXITCODE -ne 0) {
            $sidecarHint = Join-Path $ScriptDir "build_l3_sidecar.py"
            Write-Host "[ERROR] Run: pip install pyinstaller ; python $sidecarHint" -ForegroundColor Red
            exit 1
        }
        Write-Host "[Layer3] L3 Sidecar 构建完成" -ForegroundColor Green
        Write-Host ""
    }

    $UtcNow = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    Write-Host ""
    Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
    Write-Host "[$UtcNow]   Layer3 (Desktop)" -ForegroundColor Cyan
    Write-Host "[$UtcNow] ==========================================" -ForegroundColor Cyan
    Write-Host "[$UtcNow]  L2: local gateway .\scripts\run-gateway.ps1 ; remote L2 URL in app (e.g. http://YOUR_HOST:18888)" -ForegroundColor Yellow
    Write-Host "[$UtcNow]  Skills empty? Run diagnose: .\scripts\diagnose-skill-sync.ps1" -ForegroundColor Gray
    if ($WithBackgroundSource) {
        Write-Host "[$UtcNow]  双开模式：L3 引擎在独立窗口（源码）；本窗口为桌面。" -ForegroundColor Yellow
    } else {
        Write-Host ('[' + $UtcNow + ']  默认 npm run tauri:dev（需 Rust + node_modules/' + ($at + 'tauri-apps/cli') + '）；否则回退 npm run dev。') -ForegroundColor Gray
        Write-Host "[$UtcNow]  默认由桌面自动起 L3（优先编译 Sidecar）。" -ForegroundColor Gray
    }
    Write-Host "[$UtcNow]  Press Ctrl+C to stop"
    Write-Host ""

    Push-Location $DesktopDir
    # 优先走 Tauri：页面与 v0.8.x 桌面逻辑（Omni/更新条等）在壳内 + 自动起 L3 Sidecar。
    # 勿仅用 Get-Command tauri：CLI 通常在 devDependencies，npm run 会从 node_modules\.bin 解析。
    $tauriPkgDir = $at + 'tauri-apps'
    $tauriBin = Join-Path (Join-Path (Join-Path $DesktopDir "node_modules") $tauriPkgDir) 'cli'
    $hasLocalTauriCli = (Test-Path $tauriBin)
    $hasGlobalTauri = [bool](Get-Command tauri -ErrorAction SilentlyContinue)
    if ($hasLocalTauriCli -or $hasGlobalTauri) {
        npm run tauri:dev
    } else {
        Write-Host ('[INFO] 未找到 ' + ($at + 'tauri-apps/cli') + '。请先 npm install；否则为纯 Vite（无 Tauri、无自动 Sidecar）。') -ForegroundColor Yellow
        npm run dev
    }
    Pop-Location
} finally {
    Write-Host ""
    if ($env:JACHIN_PAUSE_ON_EXIT -eq "1") {
        Read-Host "Press Enter to exit"
    }
}
