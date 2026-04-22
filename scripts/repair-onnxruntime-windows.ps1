# Repair onnxruntime native DLL load failures on Windows (Memory Nexus / FastEmbed).
# Typical symptom: ImportError: DLL load failed while importing onnxruntime_pybind11_state
#
# Run from repo root (same Python as L3):
#   powershell -ExecutionPolicy Bypass -File .\scripts\repair-onnxruntime-windows.ps1
#
# WinError 5 on tokenizers*.pyd: exit ALL Python using this Anaconda (L3, Jupyter, VSCode terminals,
#   `conda run`, Cursor REPL). Then retry. If still blocked: reboot or run PowerShell as Administrator.
#
# If pip shows ProxyError / Cannot connect to proxy: this script clears HTTP(S)_PROXY for the session
# and uses pypi.org (same idea as install_deps_official_pypi.ps1).
#
# Steps: remove GPU/directml wheels that conflict with CPU build, reinstall CPU onnxruntime, fastembed,
# then align huggingface-hub/tokenizers (transformers 5.x vs fastembed resolver conflict).
param(
    [switch]$UseTuna
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# pip honors env proxy; broken corporate/proxy.ini causes ProxyError even with --proxy ""
foreach ($k in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")) {
    if (Test-Path "Env:$k") {
        Write-Host "[repair-onnxruntime] Clearing env $k for this session" -ForegroundColor DarkGray
        Remove-Item "Env:$k" -ErrorAction SilentlyContinue
    }
}

if ($UseTuna) {
    $PipIndex = @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn")
    Write-Host "[repair-onnxruntime] Using Tsinghua index (ensure proxy is fixed or use default without -UseTuna)" -ForegroundColor Yellow
} else {
    $PipIndex = @("-i", "https://pypi.org/simple", "--trusted-host", "pypi.org")
    Write-Host "[repair-onnxruntime] Using pypi.org (avoids broken proxy + mirror SSL issues)" -ForegroundColor Gray
}

Write-Host "[repair-onnxruntime] Using python:" -ForegroundColor Cyan
Get-Command python -ErrorAction Stop | Format-List Source

Write-Host "[repair-onnxruntime] Uninstalling possibly conflicting onnxruntime packages..." -ForegroundColor Gray
python -m pip uninstall -y onnxruntime-gpu onnxruntime-directml onnxruntime-training 2>$null
python -m pip uninstall -y onnxruntime 2>$null

# Memory Nexus / FastEmbed: use standard CPU onnxruntime (DirectML optional elsewhere; mixed installs often break DLL load)
Write-Host "[repair-onnxruntime] Installing onnxruntime CPU + fastembed..." -ForegroundColor Gray
python -m pip install @PipIndex --upgrade --no-cache-dir "onnxruntime==1.19.2" "fastembed>=0.4.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[repair-onnxruntime] pip install failed. Check: pip config list ; remove bad proxy in pip.ini or env." -ForegroundColor Red
    exit 1
}

Write-Host "[repair-onnxruntime] Aligning huggingface-hub + tokenizers (transformers 5.x vs fastembed)..." -ForegroundColor Gray
python -m pip install @PipIndex "huggingface-hub>=1.3.0,<2.0" "tokenizers>=0.22.0,<=0.23.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[repair-onnxruntime] HF stack align failed (check other pins in this env)." -ForegroundColor Yellow
}

Write-Host "[repair-onnxruntime] Verifying onnxruntime + fastembed TextEmbedding import..." -ForegroundColor Gray
python -c "import onnxruntime as ort; print('onnxruntime', ort.__version__)"
if ($LASTEXITCODE -ne 0) { exit 1 }
python -c "from fastembed.text.text_embedding import TextEmbedding; print('fastembed TextEmbedding import OK')"
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "[repair-onnxruntime] OK. If L3 still fails, install VC++ x64 redist: https://aka.ms/vs/17/release/vc_redist.x64.exe" -ForegroundColor Green
