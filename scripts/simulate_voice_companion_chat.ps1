param(
  [string]$DesktopExe = "",
  [switch]$ManualAssistant,
  [switch]$TextMode,
  [double]$RecordSeconds = 5,
  [string]$JvsBaseUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir

$VenvPython = Join-Path $ProjectRoot ".venv-voice\Scripts\python.exe"
$PythonExe = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { "python" }
$TestJvsScript = Join-Path $ScriptDir "test_jvs_voice.py"

if ($JvsBaseUrl) {
  $env:JACHIN_VOICE_SERVER_URL = $JvsBaseUrl.TrimEnd("/")
}
$JvsBase = if ($env:JACHIN_VOICE_SERVER_URL) { $env:JACHIN_VOICE_SERVER_URL.TrimEnd("/") } else { "http://127.0.0.1:18982" }

function Resolve-DesktopExe {
  param([string]$Candidate)
  if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
    return (Resolve-Path -LiteralPath $Candidate).Path
  }

  $proc = Get-Process -Name "jachin-desktop" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($proc -and $proc.Path -and (Test-Path -LiteralPath $proc.Path)) {
    return $proc.Path
  }

  $fallback = Join-Path $ProjectRoot "clients\desktop\src-tauri\target\debug\jachin-desktop.exe"
  $fallback = [System.IO.Path]::GetFullPath($fallback)
  if (Test-Path -LiteralPath $fallback) {
    return $fallback
  }

  throw "找不到 jachin-desktop.exe。请先启动桌面端，或传 -DesktopExe 指定路径。"
}

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

function Test-JvsHealth {
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & $PythonExe $TestJvsScript --base-url $JvsBase health 2>&1 | Out-Host
  $ok = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = $prevEap
  return $ok
}

function Invoke-JvsSttFromMic {
  param([double]$Seconds)
  $sec = [Math]::Max(0.5, [Math]::Min($Seconds, 120.0))
  Write-Host "[录音] ${sec}s — 请说话…" -ForegroundColor Cyan
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $lines = & $PythonExe $TestJvsScript --base-url $JvsBase stt --record $sec --print-text 2>&1
  $ErrorActionPreference = $prevEap
  if ($LASTEXITCODE -ne 0) {
    if ($lines) {
      Write-Host ($lines | Out-String).Trim() -ForegroundColor Yellow
    }
    return ""
  }
  $text = ($lines | Out-String).Trim()
  if ($text -match '^\[错误\]') {
    Write-Host $text -ForegroundColor Yellow
    return ""
  }
  return $text
}

function Send-VoiceSim {
  param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Role,
    [Parameter(Mandatory = $true)][string]$Text,
    [string]$State = ""
  )
  if (-not $State) {
    $State = if ($Role -eq "assistant") { "speaking" } else { "listening" }
  }
  $args = @("--jachin-voice-sim", $Role, $Text, $State)
  Start-Process -FilePath $Exe -ArgumentList $args -WindowStyle Hidden | Out-Null
}

function Send-UserUtterance {
  param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Text
  )
  $t = $Text.Trim()
  if (-not $t) { return $false }
  Write-Host "[陪伴] 注入用户话: $t" -ForegroundColor Green
  Send-VoiceSim -Exe $Exe -Role "user" -Text $t -State "listening"
  if ($ManualAssistant) {
    Start-Sleep -Milliseconds 180
    Write-Host "已注入用户语音。使用 /a 手动输入助手回复。" -ForegroundColor DarkCyan
  } else {
    Write-Host "[陪伴] 已发送，等待 L3 回复与 JVS 语音播报（约 10～30 秒）…" -ForegroundColor DarkCyan
  }
}

$exe = Resolve-DesktopExe -Candidate $DesktopExe
$useTextMode = $TextMode

Write-Host ""
Write-Host "=== Jachin 语音陪伴联调（麦克风 → STT → HUD + Orb）===" -ForegroundColor Cyan
Write-Host "Desktop EXE: $exe"
Write-Host "JVS STT:     $JvsBase"
Write-Host "Python:      $PythonExe"
Write-Host "录音时长:    ${RecordSeconds}s（/record 秒数 可改）"
Write-Host ""
if ($useTextMode) {
  Write-Host "当前模式: 文本输入（-TextMode）" -ForegroundColor DarkCyan
} else {
  Write-Host "当前模式: 麦克风录音 → JVS STT → 陪伴链路（与大聊天解耦，走 HUD + voice-companion）" -ForegroundColor DarkCyan
}
Write-Host ""
Write-Host "命令："
Write-Host "  Enter / /mic     开始录音并识别（默认）"
Write-Host "  /text            切换为键盘输入模式"
Write-Host "  /mic             切换为麦克风模式"
Write-Host "  /record 8        设置录音秒数（默认 $RecordSeconds）"
Write-Host "  /a 你的文本      手动发一条 assistant 回复"
Write-Host "  /state thinking  仅切换陪伴态 Orb 状态"
Write-Host "  /exit            退出"
Write-Host ""
  Write-Host "说明：识别后的文字经 --jachin-voice-sim 注入陪伴链路；L3 回复由 JVS TTS 朗读。"
  Write-Host "若听不到声音：先在桌面右下角 Orb 上点一次，再重新录音。" -ForegroundColor DarkGray
  Write-Host ""

Ensure-MicDeps | Out-Null
if (-not (Test-JvsHealth)) {
  Write-Host "[WARN] JVS 未就绪，麦克风 STT 将失败。" -ForegroundColor Yellow
  Write-Host "请先: .\scripts\start-layer3.ps1  或  python voice_server\main.py" -ForegroundColor Gray
  Write-Host ""
}

while ($true) {
  if ($useTextMode) {
    $line = Read-Host "You (文本)"
    if ($null -eq $line) { continue }
    $line = $line.Trim()
    if (-not $line) { continue }
  } else {
    $line = Read-Host "按 Enter 开始录音（或输入命令）"
    if ($null -eq $line) { continue }
    $line = $line.Trim()
    if (-not $line) {
      $recognized = Invoke-JvsSttFromMic -Seconds $RecordSeconds
      if (-not $recognized) {
        Write-Host "[跳过] 未识别到有效文本。" -ForegroundColor Yellow
        continue
      }
      Write-Host "[STT] $recognized" -ForegroundColor Green
      Send-UserUtterance -Exe $exe -Text $recognized
      continue
    }
  }

  if ($line -eq "/exit") { break }

  if ($line -eq "/text") {
    $useTextMode = $true
    Write-Host "已切换为文本输入模式。" -ForegroundColor DarkCyan
    continue
  }

  if ($line -eq "/mic") {
    $useTextMode = $false
    Write-Host "已切换为麦克风录音模式。" -ForegroundColor DarkCyan
    continue
  }

  if ($line.StartsWith("/record ", [System.StringComparison]::OrdinalIgnoreCase)) {
    $raw = $line.Substring(8).Trim()
    try {
      $RecordSeconds = [double]$raw
      if ($RecordSeconds -lt 0.5 -or $RecordSeconds -gt 120) {
        Write-Host "录音秒数须在 0.5～120 之间。" -ForegroundColor Yellow
        continue
      }
      Write-Host "录音时长已设为 ${RecordSeconds}s" -ForegroundColor DarkCyan
    } catch {
      Write-Host "无效数字: $raw" -ForegroundColor Yellow
    }
    continue
  }

  if ($line.StartsWith("/state ", [System.StringComparison]::OrdinalIgnoreCase)) {
    $state = $line.Substring(7).Trim().ToLowerInvariant()
    if ($state -notin @("idle", "listening", "thinking", "speaking")) {
      Write-Host "无效状态：$state" -ForegroundColor Yellow
      continue
    }
    Send-VoiceSim -Exe $exe -Role "assistant" -Text "[state:$state]" -State $state
    continue
  }

  if ($line.StartsWith("/a ", [System.StringComparison]::OrdinalIgnoreCase)) {
    $assistant = $line.Substring(3).Trim()
    if ($assistant) {
      Send-VoiceSim -Exe $exe -Role "assistant" -Text $assistant -State "speaking"
    }
    continue
  }

  if ($useTextMode) {
    Send-UserUtterance -Exe $exe -Text $line
  } else {
    Write-Host "未知命令。直接 Enter 录音，或 /text /mic /record /a /state /exit" -ForegroundColor Yellow
  }
}

Write-Host "已退出语音陪伴联调。" -ForegroundColor Green
