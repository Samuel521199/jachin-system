param(
    [string]$PythonExe = "python",
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Req = Join-Path $Root "voice_server\requirements.gpu-cu121.txt"

if (-not (Test-Path -LiteralPath $Req)) {
    throw "Missing GPU requirements: $Req"
}

Write-Host "[Voice GPU] Installing CUDA PyTorch runtime for Jachin voice models..." -ForegroundColor Cyan
& $PythonExe -m pip install --upgrade-strategy only-if-needed -r $Req
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE"
}

Write-Host "[Voice GPU] Checking CUDA availability..." -ForegroundColor Cyan
& $PythonExe -c "import json, torch; print(json.dumps({'torch': torch.__version__, 'cuda_available': torch.cuda.is_available(), 'cuda_version': torch.version.cuda, 'device_count': torch.cuda.device_count(), 'devices': [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]}, ensure_ascii=False, indent=2)); raise SystemExit(0 if torch.cuda.is_available() else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "CUDA PyTorch is not available after installation."
}

if ($Verify) {
    Write-Host "[Voice GPU] Running speaker verification GPU smoke..." -ForegroundColor Cyan
    & $PythonExe (Join-Path $Root "scripts\test_voice_sv_model.py") --repeat 10 --device cuda --require-gpu
    if ($LASTEXITCODE -ne 0) {
        throw "Voice SV GPU smoke failed with exit code $LASTEXITCODE"
    }
}

Write-Host "[Voice GPU] Ready. JVS will use CUDA when JACHIN_VOICE_TORCH_DEVICE=auto or cuda." -ForegroundColor Green
