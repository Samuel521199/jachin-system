# =============================================================================
# 唤醒句常驻监听联调（独立于桌面 UI）
#
# 默认唤醒句：「快来听我说话」
# 只有听到唤醒句后才采集指令、请求 L3 并 TTS；其它话一律忽略。
#
# 前置：JVS (18982) + L3 (18991) 已启动
#   .\scripts\start-layer3.ps1
#   或另开终端: python -m l3_node --gateway
#
# 用法:
#   .\scripts\test-voice-wake-listen.ps1
#   .\scripts\test-voice-wake-listen.ps1 -WakeWord "快来听我说话" -Verbose
#   .\scripts\test-voice-wake-listen.ps1 -DesktopExe "D:\...\jachin-desktop.exe"
# =============================================================================
param(
    [string]$WakeWord = "快来听我说话",
    [string]$JvsBaseUrl = "",
    [string]$L3BaseUrl = "",
    [string]$DesktopExe = "",
    [switch]$NoTts,
    [switch]$Verbose,
    [switch]$NoPause
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv-voice\Scripts\python.exe"
$PythonExe = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
$TestScript = Join-Path $ScriptDir "test_voice_wake_listen.py"

if ($JvsBaseUrl) { $env:JACHIN_VOICE_SERVER_URL = $JvsBaseUrl.TrimEnd("/") }
if ($L3BaseUrl) { $env:JACHIN_L3_HTTP_BASE = $L3BaseUrl.TrimEnd("/") }

function Ensure-Deps {
    & $PythonExe -c "import sounddevice, soundfile, numpy" 2>$null
    if ($LASTEXITCODE -eq 0) { return }
    Write-Host "[INFO] 安装依赖 sounddevice soundfile numpy …" -ForegroundColor Yellow
    & $PythonExe -m pip install sounddevice soundfile numpy
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] pip install 失败" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "=== 唤醒句监听联调 ===" -ForegroundColor Cyan
Write-Host "Python:    $PythonExe"
Write-Host "唤醒句:    $WakeWord"
Write-Host "JVS:       $(if ($env:JACHIN_VOICE_SERVER_URL) { $env:JACHIN_VOICE_SERVER_URL } else { 'http://127.0.0.1:18982' })"
Write-Host "L3 HTTP:   $(if ($env:JACHIN_L3_HTTP_BASE) { $env:JACHIN_L3_HTTP_BASE } else { 'http://127.0.0.1:18991' })"
Write-Host ""

Ensure-Deps

$pyArgs = @(
    $TestScript
    "--wake-word"
    $WakeWord
)
if ($DesktopExe) {
    $pyArgs += @("--desktop-exe", $DesktopExe)
}
if ($NoTts) { $pyArgs += "--no-tts" }
if ($Verbose) { $pyArgs += "--verbose" }

& $PythonExe @pyArgs
$exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }

if (-not $NoPause -and $env:CI -ne "true") {
    Write-Host ""
    Read-Host "按 Enter 退出"
}

exit $exitCode
