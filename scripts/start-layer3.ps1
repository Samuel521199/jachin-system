# =============================================================================
# Layer3 one-click start (Windows)
# Single instance: kill_l3_processes.ps1 then start
#
# 说明：用 Ctrl+C 结束 tauri dev / cargo 时，Windows 上可能出现
#   error: process didn't exit successfully ... (exit code: 0xc000013a, STATUS_CONTROL_C_EXIT)
# 这是「用户中断」的正常状态码，不是编译失败。
#
# Startup mode:
#   1) Dev mode: desktop + source L3; load local repo MCP/Skill packages for development tests.
#   2) Packaged mode: desktop + sidecar L3; do not load repo business packages, matching release runtime.
#
# DEFAULT = ask for mode, then start desktop + source L3 in the SAME console for dev mode
#   - python -m l3_node via Start-Process -NoNewWindow (logs appear in this window, mixed with npm)
#   - then npm run tauri:dev:ambient（默认，含 VAD/语音唤起）；JACHIN_SKIP_L3_SPAWN=1
#   - 不需要 ambient 时：.\scripts\start-layer3.ps1 -NoAmbient
#   - 同脚本会尝试启动 JVS voice_server (18982)，供陪伴语音 TTS/STT
#   .\scripts\start-layer3.ps1
#   .\scripts\start-layer3.ps1 -RunMode dev
#   .\scripts\start-layer3.ps1 -RunMode packaged
#   .\scripts\start-layer3.ps1 -RunMode packaged -ForcePackagedBuild
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
    [ValidateSet("dev", "packaged")]
    [string]$RunMode,
    [switch]$WsOnly,
    [switch]$SourceOnly,
    [switch]$DesktopOnly,
    [switch]$SeparateL3Window,
    [switch]$SkipRepairMcp,
    [switch]$ForcePackagedBuild,
    [switch]$NoPackagedBuild,
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
$script:L3SourceChild = $null
$script:TauriDevChild = $null

function Select-Layer3RunMode {
    param([string]$RequestedMode)
    if ($RequestedMode) { return $RequestedMode }
    if ($env:JACHIN_START_RUN_MODE) {
        $m = $env:JACHIN_START_RUN_MODE.Trim().ToLowerInvariant()
        if ($m -in @("1", "dev", "development")) { return "dev" }
        if ($m -in @("2", "packaged", "release", "prod", "production")) { return "packaged" }
    }
    if ($NoPause -or $env:CI -eq "true") {
        return "dev"
    }

    Write-Host ""
    Write-Host "请选择 Jachin L3 启动模式:" -ForegroundColor Cyan
    Write-Host "  1) 开发模式：源码 L3 + 加载本地 repo MCP/Skill，适合调试未发布能力" -ForegroundColor Yellow
    Write-Host "  2) 打包运行模式：Sidecar L3 + 仅核心/已安装能力，接近正式发布环境" -ForegroundColor Green
    Write-Host ""
    while ($true) {
        $choice = Read-Host "请输入 1 或 2"
        $c = if ($null -ne $choice) { $choice.Trim().ToLowerInvariant() } else { "" }
        if ($c -in @("1", "dev", "development")) { return "dev" }
        if ($c -in @("2", "packaged", "release", "prod", "production")) { return "packaged" }
        Write-Host "输入无效，请输入 1 或 2。" -ForegroundColor Red
    }
}

function Set-Layer3RunModeEnvironment {
    param([string]$Mode)
    if ($Mode -eq "dev") {
        $env:JACHIN_START_RUN_MODE = "dev"
        $env:JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES = "1"
        $env:JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES = "1"
        $env:JACHIN_DEV_HR_FIRST = "1"
        Remove-Item Env:\JACHIN_BUILD_WITH_BUSINESS_PACKAGES -ErrorAction SilentlyContinue
        Write-Host "[Layer3] 启动模式: 开发模式（加载 repo MCP/Skill 源码包）" -ForegroundColor Yellow
    } else {
        $env:JACHIN_START_RUN_MODE = "packaged"
        Remove-Item Env:\JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES -ErrorAction SilentlyContinue
        Remove-Item Env:\JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES -ErrorAction SilentlyContinue
        Remove-Item Env:\JACHIN_DEV_HR_FIRST -ErrorAction SilentlyContinue
        Remove-Item Env:\JACHIN_BUILD_WITH_BUSINESS_PACKAGES -ErrorAction SilentlyContinue
        Write-Host "[Layer3] 启动模式: 打包运行模式（仅核心 + 已安装缓存能力）" -ForegroundColor Green
    }
}

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
    # 默认不阻塞。仅当显式设置 JACHIN_PAUSE_ON_EXIT=1 时才等待回车。
    if ($env:JACHIN_PAUSE_ON_EXIT -ne "1") { return }
    $code = if ($script:Layer3ExitCode -ne 0) { $script:Layer3ExitCode } else { $LASTEXITCODE }
    # Ctrl+C / 用户中断（130 或 Windows STATUS_CONTROL_C_EXIT）不应被当成“卡住需回车”。
    if ($null -ne $code -and $code -in @(1, 130, 0xC000013A, 3221225786, -1073741510)) { return }
    if ($code -ne 0 -and $null -ne $code) {
        Write-Host "[Layer3] 异常退出，代码: $code" -ForegroundColor Red
    }
    Read-Host "按 Enter 关闭此窗口"
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

function Read-DotEnvFileForPackaged {
    param([string]$Path)
    $vars = @{}
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $vars }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction SilentlyContinue) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith("#")) { continue }
        if ($text.StartsWith("export ")) { $text = $text.Substring(7).Trim() }
        $idx = $text.IndexOf("=")
        if ($idx -le 0) { continue }
        $key = $text.Substring(0, $idx).Trim()
        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }
        $value = $text.Substring($idx + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $vars[$key] = $value
    }
    return $vars
}

function Get-LatestInputWriteTimeUtc {
    param([string[]]$Paths)
    $latest = [DateTime]::MinValue
    $exts = @(".rs", ".ts", ".tsx", ".js", ".mjs", ".py", ".json", ".toml", ".lock", ".yaml", ".yml", ".html", ".css", ".ps1")
    foreach ($raw in $Paths) {
        if (-not $raw) { continue }
        $path = if ([System.IO.Path]::IsPathRooted($raw)) { $raw } else { Join-Path $ProjectRoot $raw }
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $item = Get-Item -LiteralPath $path -Force
        if (-not $item.PSIsContainer) {
            if ($item.LastWriteTimeUtc -gt $latest) { $latest = $item.LastWriteTimeUtc }
            continue
        }
        Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $full = $_.FullName
                if ($full -match '\\(node_modules|target|dist|dist_jachin_desktop|\.git|__pycache__|output|logs)\\') { return $false }
                if ($full -match '\\l3_node\\packaged_lark_env_generated\.py$') { return $false }
                return $exts -contains $_.Extension.ToLowerInvariant()
            } |
            ForEach-Object {
                if ($_.LastWriteTimeUtc -gt $latest) { $latest = $_.LastWriteTimeUtc }
            }
    }
    return $latest
}

function Test-PackagedDistCurrent {
    param([string]$DistRoot)
    $mainExe = Join-Path $DistRoot "jachin-desktop.exe"
    $envPath = Join-Path $DistRoot ".env"
    $binDir = Join-Path $DistRoot "bin"
    $l3Exe = @(Get-ChildItem -LiteralPath $binDir -Filter "l3_node*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1)[0]
    if (-not (Test-Path -LiteralPath $DistRoot)) { return @{ Current = $false; Reason = "dist_jachin_desktop 不存在" } }
    if (-not (Test-Path -LiteralPath $mainExe)) { return @{ Current = $false; Reason = "缺少 jachin-desktop.exe" } }
    if (-not $l3Exe) { return @{ Current = $false; Reason = "缺少 bin\\l3_node*.exe" } }
    if (-not (Test-Path -LiteralPath $envPath)) { return @{ Current = $false; Reason = "缺少打包 .env" } }

    $desktopInputs = @(
        "clients\desktop\src",
        "clients\desktop\src-tauri\src",
        "clients\desktop\src-tauri\Cargo.toml",
        "clients\desktop\src-tauri\Cargo.lock",
        "clients\desktop\src-tauri\tauri.conf.json",
        "clients\desktop\package.json",
        "clients\desktop\package-lock.json",
        "scripts\build_full.ps1",
        ".env.example"
    )
    $l3Inputs = @(
        "l3_node",
        "core",
        "scripts\build_l3_sidecar.py",
        "scripts\bundle_l3_mcp_runtime.ps1",
        ".env.example"
    )
    $latestDesktopInput = Get-LatestInputWriteTimeUtc -Paths $desktopInputs
    $latestL3Input = Get-LatestInputWriteTimeUtc -Paths $l3Inputs
    $mainExeTime = (Get-Item -LiteralPath $mainExe).LastWriteTimeUtc
    $l3ExeTime = $l3Exe.LastWriteTimeUtc
    if ($latestDesktopInput -gt $mainExeTime) {
        return @{
            Current = $false
            Reason = "桌面源码/配置较新 latest_input=$($latestDesktopInput.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')) desktop_artifact=$($mainExeTime.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss'))"
        }
    }
    if ($latestL3Input -gt $l3ExeTime) {
        return @{
            Current = $false
            Reason = "L3 源码/配置较新 latest_input=$($latestL3Input.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')) l3_artifact=$($l3ExeTime.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss'))"
        }
    }
    return @{ Current = $true; Reason = "dist_jachin_desktop 已是最新" }
}

function Ensure-PackagedDist {
    param([string]$DistRoot)
    $state = Test-PackagedDistCurrent -DistRoot $DistRoot
    if ($ForcePackagedBuild) {
        $state = @{ Current = $false; Reason = "-ForcePackagedBuild" }
    }
    if ($state.Current) {
        Write-Host "[Layer3] 打包产物检查: $($state.Reason)" -ForegroundColor Green
        return
    }
    if ($NoPackagedBuild) {
        Write-Host "[Layer3] ERROR: 打包产物不是最新/不完整: $($state.Reason)" -ForegroundColor Red
        Write-Host "[Layer3] 已指定 -NoPackagedBuild，不自动打包。" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "[Layer3] 打包产物需要更新: $($state.Reason)" -ForegroundColor Yellow
    Write-Host "[Layer3] 打包前清理已有 L3/桌面实例，避免 exe / WebView / sidecar 文件锁..." -ForegroundColor Gray
    & (Join-Path $ScriptDir "kill_l3_processes.ps1") -NoPause -AlsoKillDesktopDev
    Write-Host "[Layer3] 开始生成最新 dist_jachin_desktop（build_full.ps1 -NoClean）..." -ForegroundColor Cyan
    $distEnvPath = Join-Path $DistRoot ".env"
    $envBackup = $null
    if (Test-Path -LiteralPath $distEnvPath) {
        $envBackup = Join-Path ([System.IO.Path]::GetTempPath()) ("jachin-packaged-env-" + [guid]::NewGuid().ToString("N") + ".env")
        Copy-Item -LiteralPath $distEnvPath -Destination $envBackup -Force
        Write-Host "[Layer3] 已备份打包 .env，自动打包后会恢复: $distEnvPath" -ForegroundColor Gray
    }
    $buildArgs = @("-NoClean")
    if ($ForcePackagedBuild) { $buildArgs += "-Force" }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "build_full.ps1") @buildArgs
    $buildExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    if ($envBackup -and (Test-Path -LiteralPath $envBackup)) {
        Copy-Item -LiteralPath $envBackup -Destination $distEnvPath -Force
        Remove-Item -LiteralPath $envBackup -Force -ErrorAction SilentlyContinue
        Write-Host "[Layer3] 已恢复打包 .env，避免使用开发目录 .env。" -ForegroundColor Green
    }
    if ($buildExitCode -ne 0) {
        Write-Host "[Layer3] ERROR: build_full.ps1 失败，退出码 $buildExitCode" -ForegroundColor Red
        exit $buildExitCode
    }

    $after = Test-PackagedDistCurrent -DistRoot $DistRoot
    if (-not $after.Current) {
        Write-Host "[Layer3] ERROR: 打包后产物仍不可用: $($after.Reason)" -ForegroundColor Red
        exit 1
    }
    Write-Host "[Layer3] 最新打包产物已就绪: $DistRoot" -ForegroundColor Green
}

function Start-PackagedDesktopRuntime {
    param([string]$DistRoot)
    $DistRoot = (Resolve-Path -LiteralPath $DistRoot).Path
    $mainExe = Join-Path $DistRoot "jachin-desktop.exe"
    $envPath = Join-Path $DistRoot ".env"
    $logsDir = Join-Path $DistRoot "logs"
    if (-not (Test-Path -LiteralPath $mainExe)) {
        Write-Host "[Layer3] ERROR: 未找到打包桌面程序: $mainExe" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path -LiteralPath $envPath)) {
        Write-Host "[Layer3] ERROR: 未找到打包 .env: $envPath" -ForegroundColor Red
        exit 1
    }
    $null = New-Item -ItemType Directory -Force -Path $logsDir

    Write-Host "[Layer3] 检查并清理已有 L3/开发桌面实例..." -ForegroundColor Gray
    & (Join-Path $ScriptDir "kill_l3_processes.ps1") -NoPause -AlsoKillDesktopDev

    $distEnv = Read-DotEnvFileForPackaged -Path $envPath
    $repoEnv = Read-DotEnvFileForPackaged -Path (Join-Path $ProjectRoot ".env")
    $skipKeys = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($k in @(
        "JACHIN_APP_ROOT",
        "JACHIN_LOG_DIR",
        "JACHIN_START_RUN_MODE",
        "JACHIN_SKIP_L3_SPAWN",
        "JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES",
        "JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES",
        "JACHIN_DEV_HR_FIRST",
        "JACHIN_BUILD_WITH_BUSINESS_PACKAGES",
        "JACHIN_DESKTOP_BUNDLE_ENV_FILE",
        "JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL"
    )) { [void]$skipKeys.Add($k) }
    foreach ($k in $repoEnv.Keys) { [void]$skipKeys.Add([string]$k) }
    foreach ($k in $distEnv.Keys) { [void]$skipKeys.Add([string]$k) }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $mainExe
    $psi.WorkingDirectory = $DistRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $false
    $evs = $psi.EnvironmentVariables
    foreach ($k in $skipKeys) {
        if ($evs.ContainsKey($k)) {
            try { $evs.Remove($k) } catch { }
        }
    }
    foreach ($k in $distEnv.Keys) {
        $evs[[string]$k] = [string]$distEnv[$k]
    }
    $evs["JACHIN_START_RUN_MODE"] = "packaged"
    $evs["JACHIN_APP_ROOT"] = $DistRoot
    $evs["JACHIN_LOG_DIR"] = $logsDir
    $evs["JACHIN_DESKTOP_BUNDLE_ENV_FILE"] = $envPath
    $evs["PYTHONUTF8"] = "1"
    $evs["PYTHONUNBUFFERED"] = "1"

    Write-Host "[Layer3] 启动打包模式桌面:" -ForegroundColor Green
    Write-Host "  exe  = $mainExe" -ForegroundColor Gray
    Write-Host "  cwd  = $DistRoot" -ForegroundColor Gray
    Write-Host "  env  = $envPath" -ForegroundColor Gray
    Write-Host "  logs = $logsDir" -ForegroundColor Gray
    Write-Host "[Layer3] 注意: 本次不会读取仓库根 .env，也不会进入 clients\\desktop 的 tauri dev。" -ForegroundColor Green
    $proc = [System.Diagnostics.Process]::Start($psi)
    if ($null -eq $proc) {
        Write-Host "[Layer3] ERROR: 打包桌面启动失败" -ForegroundColor Red
        exit 1
    }
    Write-Host "[Layer3] 打包桌面已启动 pid=$($proc.Id)" -ForegroundColor Green
}

if ($SourceOnly -and $DesktopOnly) {
    Write-Host "[Layer3] ERROR: use -SourceOnly OR -DesktopOnly, not both." -ForegroundColor Red
    exit 1
}

$SelectedRunMode = Select-Layer3RunMode -RequestedMode $RunMode
Set-Layer3RunModeEnvironment -Mode $SelectedRunMode

if ($SelectedRunMode -eq "packaged" -and -not $SourceOnly) {
    $PackagedDistRoot = Join-Path $ProjectRoot "dist_jachin_desktop"
    Ensure-PackagedDist -DistRoot $PackagedDistRoot
    Start-PackagedDesktopRuntime -DistRoot $PackagedDistRoot
    exit 0
}

if ($SelectedRunMode -eq "packaged" -and $SourceOnly) {
    Write-Host "[Layer3] ERROR: 打包运行模式不支持 -SourceOnly，因为 SourceOnly 会回到源码 L3/开发环境。" -ForegroundColor Red
    Write-Host "[Layer3] 请直接使用: .\scripts\start-layer3.ps1 -RunMode packaged" -ForegroundColor Yellow
    exit 1
}

$env:JACHIN_APP_ROOT = $ProjectRoot
# L2 白名单非空时放行本地已注册 MCP（Puppeteer / browser-use / K11）；与 l3_node tool_pool.expand_allowed_skills_with_local_mcp 一致
$env:JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL = "1"
$ErrorActionPreference = "Continue"

# 修正 ~/.jachin/mcp_servers.json 里过期的 hr-atomic-tools 路径（换仓库目录名后常见），避免拖死整轮 MCP 握手
# （repair_mcp_servers.py 以 utf-8-sig 读取，兼容带 BOM 的 JSON，与 L3 mcp_client 一致）
if (-not $SkipRepairMcp -and $SelectedRunMode -eq "dev") {
    try {
        & python (Join-Path $ScriptDir "repair_mcp_servers.py") --project-root $ProjectRoot
    } catch {
        Write-Host "[Layer3] repair_mcp_servers.py 跳过: $_" -ForegroundColor DarkGray
    }
} elseif ($SelectedRunMode -eq "packaged") {
    Write-Host "[Layer3] 打包运行模式跳过 HR MCP 路径修复（业务包应从 L1/L2 安装）。" -ForegroundColor DarkGray
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

function Stop-Layer3TrackedProcessTree {
    param(
        [object]$Process,
        [string]$Name
    )
    if (-not $Process) { return }
    $targetPid = 0
    try { $targetPid = [int]$Process.Id } catch { return }
    if ($targetPid -le 0) { return }
    try {
        $alive = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
        if (-not $alive -or $alive.HasExited) { return }
    } catch { }
    Write-Host "[Layer3] 结束残留 $Name 进程树 (pid=$targetPid)..." -ForegroundColor Gray
    try {
        & taskkill.exe /F /T /PID $targetPid *> $null
    } catch {
        try { Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue } catch { }
    }
}

function Resolve-NativeCommandPath {
    param([string]$CommandName)
    $cmds = @(Get-Command $CommandName -All -ErrorAction SilentlyContinue)
    if (-not $cmds.Count) { return $null }

    foreach ($cmd in ($cmds | Where-Object { $_.CommandType -eq "Application" })) {
        foreach ($p in @($cmd.Source, $cmd.Path, $cmd.Definition)) {
            if ($p -and (Test-Path -LiteralPath $p) -and ($p -match '\.(exe|cmd|bat|com)$')) { return $p }
        }
    }
    foreach ($cmd in $cmds) {
        foreach ($p in @($cmd.Source, $cmd.Path, $cmd.Definition)) {
            if ($p -and (Test-Path -LiteralPath $p) -and ($p -notmatch '\.ps1$')) { return $p }
        }
    }
    return $CommandName
}

function Invoke-InterruptibleNativeProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$Name
    )
    $resolved = Resolve-NativeCommandPath $FilePath
    if (-not $resolved) { throw "Command not found: $FilePath" }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    if ($resolved -match '\.(cmd|bat)$') {
        $quoted = '"' + $resolved + '"'
        $argLine = ($Arguments | ForEach-Object {
            $s = [string]$_
            if ($s -match '[\s"]') { '"' + ($s -replace '"', '\"') + '"' } else { $s }
        }) -join ' '
        $psi.FileName = "cmd.exe"
        $psi.Arguments = "/D /S /C `"$quoted $argLine`""
    } else {
        $psi.FileName = $resolved
        $psi.Arguments = ($Arguments -join ' ')
    }
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $false

    $proc = $null
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        if ($null -eq $proc) { throw "Failed to start $Name" }
        if ($Name -eq "tauri-dev") { $script:TauriDevChild = $proc }
        while (-not $proc.HasExited) {
            Start-Sleep -Milliseconds 500
        }
        return $proc.ExitCode
    } catch [System.Management.Automation.PipelineStoppedException] {
        Stop-Layer3TrackedProcessTree -Process $proc -Name $Name
        throw
    } catch {
        Stop-Layer3TrackedProcessTree -Process $proc -Name $Name
        throw
    } finally {
        if ($Name -eq "tauri-dev") { $script:TauriDevChild = $null }
    }
}

function Invoke-NpmScriptInterruptible {
    param(
        [string]$ScriptName,
        [string]$WorkingDirectory
    )
    return Invoke-InterruptibleNativeProcess -FilePath "npm" -Arguments @("run", $ScriptName) -WorkingDirectory $WorkingDirectory -Name "tauri-dev"
}

function Resolve-VoicePythonExe {
    $venvPy = Join-Path $ProjectRoot ".venv-voice\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) { return $venvPy }
    return Resolve-PythonExePath
}

function Get-JvsTtsMissingFiles {
    param([string]$ModelRoot)
    $missing = @()
    $ttsRoot = Join-Path $ModelRoot "tts"

    $kokoroRoot = Join-Path $ttsRoot "Kokoro-82M-v1.1-zh-ONNX"
    $kokoroModel = Join-Path $kokoroRoot "onnx\model.onnx"
    $kokoroTokenizer = Join-Path $kokoroRoot "tokenizer.json"
    $kokoroVoices = Join-Path $kokoroRoot "voices"
    if ((Test-Path -LiteralPath $kokoroModel) -and
        (Test-Path -LiteralPath $kokoroTokenizer) -and
        (Test-Path -LiteralPath $kokoroVoices) -and
        @(Get-ChildItem -LiteralPath $kokoroVoices -Filter "*.bin" -ErrorAction SilentlyContinue).Count -gt 0) {
        return @()
    }

    $manifest = Join-Path $ttsRoot "MOSS-TTS-Nano-100M-ONNX\browser_poc_manifest.json"
    $codecDir = Join-Path $ttsRoot "MOSS-Audio-Tokenizer-Nano-ONNX"
    $runtimeRoot = if ($env:JACHIN_MOSS_TTS_RUNTIME_DIR) { $env:JACHIN_MOSS_TTS_RUNTIME_DIR } else { "D:\model\MOSS-TTS-Nano" }
    $runtimeFile = Join-Path $runtimeRoot "onnx_tts_runtime.py"
    if (-not (Test-Path -LiteralPath $manifest)) { $missing += "MOSS manifest: $manifest" }
    if (-not (Test-Path -LiteralPath $codecDir)) { $missing += "MOSS codec dir: $codecDir" }
    if (-not (Test-Path -LiteralPath $runtimeFile)) { $missing += "MOSS runtime: $runtimeFile" }
    return $missing
}

function Write-JvsTtsNotReady {
    param(
        [object]$Health,
        [string]$ModelRoot,
        [string[]]$MissingFiles
    )
    Write-Host "[Layer3] JVS 已监听，但 TTS 尚未就绪；L3 文本/工具能力可继续使用，语音朗读会降级。" -ForegroundColor Yellow
    Write-Host "  model_root = $ModelRoot" -ForegroundColor Gray
    if ($Health) {
        Write-Host "  stt_ready=$($Health.stt_ready) tts_ready=$($Health.tts_ready) sv_ready=$($Health.sv_ready)" -ForegroundColor Gray
        if ($Health.tts_diagnostics -and $Health.tts_diagnostics.missing) {
            Write-Host "  health missing = $($Health.tts_diagnostics.missing -join ', ')" -ForegroundColor Gray
        }
        if ($Health.tts_load_error) {
            Write-Host "  tts_load_error = $($Health.tts_load_error)" -ForegroundColor Gray
        }
    }
    if ($MissingFiles -and $MissingFiles.Count -gt 0) {
        Write-Host "  缺失文件:" -ForegroundColor Yellow
        foreach ($m in $MissingFiles) {
            Write-Host "    - $m" -ForegroundColor Gray
        }
    }
}

function Start-JvsVoiceServer {
    param(
        # start-layer3 一键启动时强制重启 JVS，避免旧进程无 CORS 等修复仍占用 18982
        [switch]$Refresh
    )
    $baseUrl = if ($env:JACHIN_VOICE_SERVER_URL) { $env:JACHIN_VOICE_SERVER_URL.TrimEnd('/') } else { "http://127.0.0.1:18982" }
    $healthUrl = "$baseUrl/health"
    $modelRoot = if ($env:JACHIN_VOICE_MODEL_ROOT) { $env:JACHIN_VOICE_MODEL_ROOT } else { Join-Path $ProjectRoot "data\models\voice" }
    $missingTtsFiles = @(Get-JvsTtsMissingFiles -ModelRoot $modelRoot)

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
                Write-JvsTtsNotReady -Health $h -ModelRoot $modelRoot -MissingFiles $missingTtsFiles
                return
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
    $lastWaitLogAt = [DateTime]::MinValue
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $h = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
            if ($h.ok -and $h.tts_ready) {
                Write-Host "[Layer3] JVS 就绪: tts_voice=$($h.tts_voice) model_root=$($h.model_root)" -ForegroundColor Green
                return
            }
            if ($h.ok) {
                if (($h.tts_diagnostics -and $h.tts_diagnostics.missing -and $h.tts_diagnostics.missing.Count -gt 0) -or $missingTtsFiles.Count -gt 0) {
                    Write-JvsTtsNotReady -Health $h -ModelRoot $modelRoot -MissingFiles $missingTtsFiles
                    return
                }
                if (([DateTime]::UtcNow - $lastWaitLogAt).TotalSeconds -ge 10) {
                    Write-Host "[Layer3] JVS 监听中，等待 TTS 模型预热..." -ForegroundColor Gray
                    $lastWaitLogAt = [DateTime]::UtcNow
                }
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Write-JvsTtsNotReady -Health $h -ModelRoot $modelRoot -MissingFiles @(Get-JvsTtsMissingFiles -ModelRoot $modelRoot)
    Write-Host "[Layer3] WARN: JVS 120s 内 tts_ready 仍为 false；请检查 data\models\voice 与 voice_server 依赖。" -ForegroundColor Yellow
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

function Invoke-TauriDevWithRustIceRetry {
    param(
        [string]$DesktopDir,
        [string]$TauriDevScript
    )
    $code = Invoke-NpmScriptInterruptible -ScriptName $TauriDevScript -WorkingDirectory $DesktopDir
    if ($code -ne 101) {
        return $code
    }

    Write-Host "[Layer3] Tauri/Rust 以 101 退出，尝试修复 rustc ICE/损坏 rmeta 缓存..." -ForegroundColor Yellow
    Write-Host "[Layer3] 清理 tauri-plugin-notification 与 jachin-desktop 后重试一次。" -ForegroundColor Gray
    Push-Location (Join-Path $DesktopDir "src-tauri")
    try {
        cargo clean -p tauri-plugin-notification
        cargo clean -p jachin-desktop
    } finally {
        Pop-Location
    }
    return (Invoke-NpmScriptInterruptible -ScriptName $TauriDevScript -WorkingDirectory $DesktopDir)
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
                if ($SelectedRunMode -eq "dev") {
                    $modeEnv = '$env:JACHIN_START_RUN_MODE = ''dev''; $env:JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES = ''1''; $env:JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES = ''1''; $env:JACHIN_DEV_HR_FIRST = ''1''; Remove-Item Env:\JACHIN_BUILD_WITH_BUSINESS_PACKAGES -ErrorAction SilentlyContinue'
                } else {
                    $modeEnv = '$env:JACHIN_START_RUN_MODE = ''packaged''; Remove-Item Env:\JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES -ErrorAction SilentlyContinue; Remove-Item Env:\JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES -ErrorAction SilentlyContinue; Remove-Item Env:\JACHIN_DEV_HR_FIRST -ErrorAction SilentlyContinue; Remove-Item Env:\JACHIN_BUILD_WITH_BUSINESS_PACKAGES -ErrorAction SilentlyContinue'
                }
                $psCmd = (
                    '$Host.UI.RawUI.WindowTitle = ''Jachin L3 (source)''; Set-Location -LiteralPath ''{0}''; $env:JACHIN_APP_ROOT = ''{0}''; {1}; $env:JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL = ''1''; $env:PYTHONUTF8 = 1; {2}; & python -m l3_node {3}' `
                        -f $prEsc, $modeEnv, $setEnc, $pyMode
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
                    $evs["JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL"] = "1"
                    $evs["PYTHONUTF8"] = "1"
                    $evs["JACHIN_START_RUN_MODE"] = $SelectedRunMode
                    if ($SelectedRunMode -eq "dev") {
                        $evs["JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES"] = "1"
                        $evs["JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES"] = "1"
                        $evs["JACHIN_DEV_HR_FIRST"] = "1"
                    } else {
                        $evs.Remove("JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES")
                        $evs.Remove("JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES")
                        $evs.Remove("JACHIN_DEV_HR_FIRST")
                        $evs.Remove("JACHIN_BUILD_WITH_BUSINESS_PACKAGES")
                    }
                    $proc = [System.Diagnostics.Process]::Start($psi)
                    if ($null -ne $proc) {
                        $script:L3SourceChild = $proc
                        [void]$proc.Id
                    }
                } catch {
                    Write-Host "[Layer3] ProcessStartInfo 启动失败，回退 Start-Process: $_" -ForegroundColor Yellow
                    $script:L3SourceChild = Start-Process -FilePath $pyExe -WorkingDirectory $ProjectRoot -ArgumentList $argList -NoNewWindow -PassThru
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

        # Dev mode may use a stub to keep UI iteration fast. Packaged mode must
        # use a real sidecar, otherwise it cannot validate release behavior.
        if ($SelectedRunMode -eq "dev") {
            Write-Host "[Layer3] 开发模式：确保 Sidecar 路径存在（stub 按需）..." -ForegroundColor Gray
            & python (Join-Path $ScriptDir "create_l3_stub.py") --if-missing
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[ERROR] create_l3_stub.py 失败" -ForegroundColor Red
                exit 1
            }
        } else {
            Write-Host "[Layer3] 打包运行模式：跳过 stub，要求真实 Sidecar。" -ForegroundColor Gray
        }

        $BinDir = Join-Path $DesktopDir "src-tauri\bin"
        $L3Exe = Get-ChildItem -Path $BinDir -Filter "l3_node-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($SelectedRunMode -eq "packaged" -and $L3Exe -and $L3Exe.Length -lt 100KB) {
            Write-Host "[Layer3] 检测到 Sidecar 是 stub/占位文件，打包运行模式将重建真实 Sidecar: $($L3Exe.FullName)" -ForegroundColor Yellow
            Remove-Item -LiteralPath $L3Exe.FullName -Force -ErrorAction SilentlyContinue
            $L3Exe = $null
        }
        if (-not $L3Exe) {
            Write-Host ""
            Write-Host "[Layer3] L3 Sidecar missing, building (PyInstaller)..." -ForegroundColor Yellow
            & python (Join-Path $ScriptDir "build_l3_sidecar.py")
            if ($LASTEXITCODE -ne 0) {
                if ($SelectedRunMode -eq "packaged") {
                    $sidecarHint = Join-Path $ScriptDir "build_l3_sidecar.py"
                    Write-Host "[ERROR] 打包运行模式要求真实 Sidecar，构建失败。请先修复: python $sidecarHint" -ForegroundColor Red
                    exit 1
                }
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
        Write-Host "[$UtcNow] RunMode: $SelectedRunMode" -ForegroundColor Cyan
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
        Write-Host "[$UtcNow] Ctrl+C 会中断当前控制台；下次启动会自动清理 L3/英语背词服务残留，也可手动执行 scripts\\kill_l3_processes.ps1" -ForegroundColor Gray
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
            $tauriExitCode = Invoke-TauriDevWithRustIceRetry -DesktopDir $DesktopDir -TauriDevScript $tauriDevScript
            $global:LASTEXITCODE = $tauriExitCode
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
    if ($script:TauriDevChild) {
        try {
            if (-not $script:TauriDevChild.HasExited) {
                Stop-Layer3TrackedProcessTree -Process $script:TauriDevChild -Name "tauri-dev"
            }
        } catch { }
        $script:TauriDevChild = $null
    }
    # 同窗模式下 Ctrl+C 常先打断 npm；这里兜底回收残留的 python -m l3_node 子进程，避免“界面卡住无提示符”。
    if ($script:L3SourceChild) {
        try {
            if (-not $script:L3SourceChild.HasExited) {
                Stop-Layer3TrackedProcessTree -Process $script:L3SourceChild -Name "L3"
                Write-Host "[Layer3] 已结束残留 L3 子进程 (pid=$($script:L3SourceChild.Id))" -ForegroundColor Gray
            }
        } catch { }
        $script:L3SourceChild = $null
    }
    if ($SelectedRunMode -eq "dev" -and -not $SourceOnly) {
        try {
            Write-Host "[Layer3] 退出清理: 桌面/L3/JVS/英语背词/Vite 残留..." -ForegroundColor Gray
            & (Join-Path $ScriptDir "kill_l3_processes.ps1") -NoPause -AlsoKillDesktopDev
            [void](Stop-TcpPortListener -Port 31421)
        } catch {
            Write-Host "[Layer3] 退出清理跳过: $_" -ForegroundColor DarkGray
        }
    }
    Wait-Layer3PauseIfNeeded
}
