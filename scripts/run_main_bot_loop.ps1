# Tongits bot launcher (PowerShell) - uses .venv-omniparser only
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BotArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv-omniparser\Scripts\python.exe"
$Script = Join-Path $PSScriptRoot "main_bot_loop.py"

if (-not (Test-Path $Py)) {
    Write-Host "[launcher] ERROR: .venv-omniparser not found at $Py"
    Write-Host "[launcher] Run: .\scripts\setup_omniparser_venv.ps1"
    exit 1
}

Write-Host "[launcher] venv: $Py"
& $Py $Script @BotArgs
