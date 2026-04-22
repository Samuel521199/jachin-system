# Install Memory Nexus deps from pypi.org when your pip index (e.g. Tsinghua) fails with SSLError on fastembed.
# Run from repo root:  powershell -ExecutionPolicy Bypass -File l3_client/local_mcps/jachin_memory_nexus/install_deps_official_pypi.ps1
$ErrorActionPreference = "Stop"
# onnxruntime before fastembed; Windows DLL: scripts/repair-onnxruntime-windows.ps1
python -m pip install "mcp>=1.0.0" "numpy>=1.24.0,<2" "onnxruntime>=1.17.3,<1.24" "fastembed>=0.4.0" `
    -i https://pypi.org/simple `
    --trusted-host pypi.org
# transformers 5.x needs hub>=1.3; fastembed allows hub<2,>=0.20 — align after fastembed
python -m pip install "huggingface-hub>=1.3.0,<2.0" "tokenizers>=0.22.0,<=0.23.0" `
    -i https://pypi.org/simple `
    --trusted-host pypi.org
Write-Host "Memory Nexus deps OK."
