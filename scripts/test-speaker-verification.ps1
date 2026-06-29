# =============================================================================
# 声纹识主测试（一键）
# 场景：你和同事先后说话，比较 原始STT vs 主人轨STT
# =============================================================================
param(
    [string]$BaseUrl = "",
    [int]$Rounds = 3,
    [double]$Record = 10,
    [string]$Profile = "",
    [string]$SaveDir = ""
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
$TestScript = Join-Path $ScriptDir "test_speaker_verification.py"

function Resolve-VoiceBaseUrl {
    param([string]$CliBaseUrl)
    if ($CliBaseUrl) { return $CliBaseUrl.TrimEnd("/") }
    if ($env:JACHIN_VOICE_SERVER_URL) { return $env:JACHIN_VOICE_SERVER_URL.TrimEnd("/") }
    return "http://127.0.0.1:18982"
}

function Show-SvBackendInfo {
    param([string]$ResolvedBaseUrl)
    $healthUrl = "$ResolvedBaseUrl/health"
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 5
        $svModel = "$($health.sv_model)"
        $svReady = "$($health.sv_ready)"
        $svLoadError = "$($health.sv_load_error)"
        $modeText = if ($svModel -match "cam\+\+|campplus") {
            "CAM 模型（CAM++）"
        } elseif ($svModel -match "mvp|spectral|template") {
            "模板/谱统计匹配（MVP 回退）"
        } else {
            "未知实现（请看 sv_model 字段）"
        }
        Write-Host "[SV] BaseURL: $ResolvedBaseUrl"
        Write-Host "[SV] Backend: $svModel"
        Write-Host "[SV] Mode: $modeText"
        Write-Host "[SV] Ready: $svReady"
        if ($svLoadError -and $svLoadError -ne "") {
            Write-Host "[SV] LoadError: $svLoadError" -ForegroundColor Yellow
        }
        Write-Host ""
    }
    catch {
        Write-Host "[SV] 无法读取 $healthUrl，脚本将继续执行。" -ForegroundColor Yellow
        Write-Host "[SV] 无法自动判断 CAM 还是模板匹配（请先确认 JVS 已启动）。" -ForegroundColor Yellow
        Write-Host ""
    }
}

function Ensure-VoiceDeps {
    & $PythonExe -c "import sounddevice, soundfile, numpy" 2>$null
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "[INFO] 正在安装依赖 sounddevice + soundfile + numpy ..." -ForegroundColor Yellow
    & $PythonExe -m pip install sounddevice soundfile numpy
    return ($LASTEXITCODE -eq 0)
}

Write-Host ""
Write-Host "=== Speaker Verification Test ===" -ForegroundColor Cyan
Write-Host "Python: $PythonExe"
Write-Host "Script: $TestScript"
Write-Host ""
$ResolvedBaseUrl = Resolve-VoiceBaseUrl -CliBaseUrl $BaseUrl
Show-SvBackendInfo -ResolvedBaseUrl $ResolvedBaseUrl

if (-not (Ensure-VoiceDeps)) {
    Write-Host "[ERROR] 依赖安装失败，请手动执行 pip install sounddevice soundfile numpy" -ForegroundColor Red
    Read-Host "按 Enter 退出"
    exit 1
}

$argsList = @()
if ($BaseUrl) { $argsList += @("--base-url", $BaseUrl) }
$argsList += @("--rounds", "$Rounds", "--record", "$Record")
if ($Profile) { $argsList += @("--profile", $Profile) }
if ($SaveDir) { $argsList += @("--save-dir", $SaveDir) }

& $PythonExe $TestScript @argsList
$exitCode = $LASTEXITCODE

Write-Host ""
if ($exitCode -ne 0) {
    Write-Host "测试脚本异常退出，code=$exitCode" -ForegroundColor Yellow
}
Read-Host "按 Enter 退出"
exit $exitCode
