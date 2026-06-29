# =============================================================================
# JVS 语音模块测试（STT + TTS）
# 前置：python voice_server\main.py  或 Tauri 自动拉起 JVS
# =============================================================================
param(
    [string]$BaseUrl = "",
    [switch]$SkipHealth
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$ErrorActionPreference = "Continue"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv-voice\Scripts\python.exe"
$PythonExe = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }

if ($BaseUrl) {
    $env:JACHIN_VOICE_SERVER_URL = $BaseUrl
}

$TestScript = Join-Path $ScriptDir "test_jvs_voice.py"

function Ensure-MicDeps {
    & $PythonExe -c "import sounddevice, soundfile" 2>$null
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "[INFO] 正在安装麦克风依赖 sounddevice + soundfile …" -ForegroundColor Yellow
    & $PythonExe -m pip install sounddevice soundfile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] 自动安装失败，请手动: pip install sounddevice soundfile" -ForegroundColor Yellow
        return $false
    }
    return $true
}

Write-Host ""
Write-Host "=== JVS 语音测试 ===" -ForegroundColor Cyan
Write-Host "Python: $PythonExe"
Write-Host "脚本:   $TestScript"
Write-Host ""

Ensure-MicDeps | Out-Null

if (-not $SkipHealth) {
    & $PythonExe $TestScript health
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[提示] 请先启动 voice_server:" -ForegroundColor Yellow
        Write-Host "  python voice_server\main.py" -ForegroundColor Gray
        Write-Host "或: .\scripts\start-layer3.ps1（桌面会自动尝试拉起 JVS）" -ForegroundColor Gray
        Write-Host ""
        $cont = Read-Host "JVS 未就绪，仍进入交互菜单? (y/N)"
        if ($cont -notmatch '^[yY]') {
            Read-Host "按 Enter 退出"
            exit 1
        }
    }
}

Write-Host ""
Write-Host "[提示] 选项 1 使用麦克风录音；失败时请检查系统麦克风权限" -ForegroundColor DarkGray
Write-Host ""

$JvsBase = if ($env:JACHIN_VOICE_SERVER_URL) { $env:JACHIN_VOICE_SERVER_URL } else { "http://127.0.0.1:18982" }
& $PythonExe $TestScript --base-url $JvsBase
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -ne 0) {
    Write-Host "脚本异常结束，退出码: $exitCode" -ForegroundColor Yellow
}
Read-Host "按 Enter 退出"
exit $exitCode
