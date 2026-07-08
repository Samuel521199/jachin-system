param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "[English GPU] Installing CUDA-enabled llama-cpp-python runtime..."

& $Python -m pip install `
  --force-reinstall `
  --no-cache-dir `
  --prefer-binary `
  --only-binary=:all: `
  "llama-cpp-python==0.2.90" `
  --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cu124"

& $Python -m pip install `
  --prefer-binary `
  --only-binary=:all: `
  "nvidia-cuda-runtime-cu12" `
  "nvidia-cublas-cu12"

Write-Host "[English GPU] Verifying GPU offload support..."

@'
import os
import site
import sysconfig
from pathlib import Path

for root in dict.fromkeys([sysconfig.get_paths().get("purelib", ""), sysconfig.get_paths().get("platlib", ""), *site.getsitepackages()]):
    if not root:
        continue
    base = Path(root) / "nvidia"
    for rel in ("cuda_runtime/bin", "cublas/bin", "cuda_nvrtc/bin"):
        path = base.joinpath(*rel.split("/"))
        if path.is_dir():
            os.add_dll_directory(str(path))
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")

import llama_cpp
from llama_cpp import llama_cpp as low
print("llama_cpp", getattr(llama_cpp, "__version__", "unknown"))
print("supports_gpu_offload", low.llama_supports_gpu_offload())
raise SystemExit(0 if low.llama_supports_gpu_offload() else 2)
'@ | & $Python -

Write-Host "[English GPU] OK. Set JACHIN_EXAMPLE_LLM_GPU_LAYERS=-1 to force full GPU offload, or leave unset for auto mode."
