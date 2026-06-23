# OmniParser isolated venv (avoid Anaconda base PyTorch c10.dll WinError 1114)
# Usage (PowerShell, repo root):
#   .\scripts\setup_omniparser_venv.ps1
#   .\.venv-omniparser\Scripts\python.exe scripts\test_omniparser_local.py

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv-omniparser"
$Py = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"
$Tag = '[setup]'

if (-not (Test-Path $Py)) {
    Write-Host "$Tag Creating venv: $Venv"
    python -m venv $Venv
}

& $Pip install -U pip wheel

Write-Host "$Tag PyTorch 2.2 CPU (avoids c10.dll 1114 on some Win11 + new torch builds)"
& $Pip install torch==2.2.2+cpu torchvision==0.17.2+cpu --index-url https://download.pytorch.org/whl/cpu

Write-Host "$Tag NumPy 1.x + OpenCV 4.8 (pinned for torch 2.2)"
& $Pip install numpy==1.26.4 "opencv-python-headless==4.8.1.78"

Write-Host "$Tag OmniParser + Florence-2 dependencies"
& $Pip install -r (Join-Path $Root "model\OmniParser-v2.0\requirements.txt")
& $Pip install transformers==4.44.2 tokenizers==0.19.1 accelerate einops timm pillow supervision==0.18.0 ultralytics==8.3.70 easyocr

Write-Host "$Tag Holographic screen + calculator agent (pyautogui, openai)"
& $Pip install -r (Join-Path $Root "l3_client\local_mcps\holographic_screen_mcp\requirements.txt")
& $Pip install openai python-dotenv

Write-Host ""
Write-Host "Done. Run:"
Write-Host "  .\.venv-omniparser\Scripts\python.exe scripts\test_omniparser_local.py"
Write-Host "  .\.venv-omniparser\Scripts\python.exe scripts\test_calculator_agent.py"
Write-Host ""
& $Py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
