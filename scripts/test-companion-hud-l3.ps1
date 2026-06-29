# =============================================================================
# 陪伴态 HUD + L3 联调预检（不含麦克风 JVS 端到端）
# 用法: .\scripts\test-companion-hud-l3.ps1
#       .\scripts\test-companion-hud-l3.ps1 -SkipJvsCheck
#       .\scripts\test-companion-hud-l3.ps1 -RunSimulate   # 预检通过后直接开模拟器
# =============================================================================
param(
    [switch]$SkipJvsCheck,
    [switch]$RunSimulate,
    [switch]$NoPause
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Test-PortListening {
    param([int]$Port)
    try {
        $lines = netstat -ano 2>$null
        if (-not $lines) { return $false }
        $m = $lines | Select-String ":$Port\s" | Select-String "LISTENING"
        return [bool]$m
    } catch {
        return $false
    }
}

function Test-HttpHealth {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return @{ Ok = $true; Body = $r.Content }
    } catch {
        try {
            $raw = & curl.exe -s -m 3 $Url 2>$null
            if ($raw) {
                return @{ Ok = $true; Body = $raw }
            }
        } catch { }
        return @{ Ok = $false; Error = $_.Exception.Message }
    }
}

Write-Host ""
Write-Host "=== Jachin 陪伴 HUD + L3 联调预检 ===" -ForegroundColor Cyan
Write-Host "项目目录: $ProjectRoot"
Write-Host ""

$allOk = $true

$desktop = Get-Process -Name "jachin-desktop" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($desktop) {
    Write-Host "[OK] 桌面进程: jachin-desktop (PID $($desktop.Id))" -ForegroundColor Green
} else {
    Write-Host "[FAIL] 未检测到 jachin-desktop" -ForegroundColor Red
    Write-Host "       请先运行: .\scripts\start-layer3.ps1" -ForegroundColor Yellow
    $allOk = $false
}

$l3Listen = Test-PortListening -Port 18981
if ($l3Listen) {
    Write-Host "[OK] L3 Sensory 端口 18981 正在监听" -ForegroundColor Green
} else {
    Write-Host "[FAIL] 18981 未监听 — L3 未启动，HUD 收不到模型回复" -ForegroundColor Red
    Write-Host "       运行: .\scripts\start-layer3.ps1" -ForegroundColor Yellow
    $allOk = $false
}

if (-not $SkipJvsCheck) {
    $jvsListen = Test-PortListening -Port 18982
    $jvsHealth = Test-HttpHealth -Url "http://127.0.0.1:18982/health"
    if ($jvsListen -and $jvsHealth.Ok) {
        Write-Host "[OK] JVS 18982 健康" -ForegroundColor Green
        Write-Host "     $($jvsHealth.Body)" -ForegroundColor DarkGray
    } elseif ($jvsListen) {
        Write-Host "[WARN] 18982 在监听但 /health 异常: $($jvsHealth.Error)" -ForegroundColor Yellow
    } else {
        Write-Host "[INFO] JVS 18982 未运行（仅测 HUD+L3 可忽略）" -ForegroundColor DarkGray
        Write-Host "       启动: python voice_server\main.py" -ForegroundColor DarkGray
        Write-Host "       或确认桌面未设 JACHIN_SKIP_VOICE_SPAWN=1" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "=== 下一步 ===" -ForegroundColor Cyan
if ($allOk) {
    Write-Host "[就绪] 可运行模拟器:" -ForegroundColor Green
    Write-Host "  .\scripts\simulate_voice_companion_chat.ps1" -ForegroundColor White
    Write-Host "输入中文后应看到: HUD 弹出 + 用户气泡 + 助手流式回复" -ForegroundColor DarkGray
} else {
    Write-Host "[未就绪] 请先补齐 FAIL 项，再跑 simulate" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "HUD: Ctrl+Alt+H 或托盘「打开 HUD 临时交互」" -ForegroundColor DarkGray
Write-Host ""

if ($RunSimulate -and $allOk) {
    Write-Host "正在启动 simulate_voice_companion_chat.ps1 ..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "simulate_voice_companion_chat.ps1")
    $simExit = $LASTEXITCODE
    if (-not $NoPause) {
        Read-Host "按 Enter 退出"
    }
    exit $simExit
}

if (-not $NoPause) {
    if (-not $allOk) {
        Write-Host "预检未通过（退出码 1）" -ForegroundColor Yellow
    }
    Read-Host "按 Enter 退出"
}

if (-not $allOk) { exit 1 }
exit 0
