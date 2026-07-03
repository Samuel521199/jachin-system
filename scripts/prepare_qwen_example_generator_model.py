"""Download the small GGUF model used for local English example generation."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_ID = "bartowski/Qwen2.5-0.5B-Instruct-GGUF"
FILENAME = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
PACKAGE_ID = "com.jachin.model.qwen2-5-0-5b-instruct-gguf-q4-k-m"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="Remove existing model file before downloading")
    args = parser.parse_args()

    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:
        raise SystemExit("Missing huggingface_hub. Install with: python -m pip install huggingface_hub") from exc

    package_dir = ROOT / "models_repo" / PACKAGE_ID
    model_dir = package_dir / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / FILENAME
    if args.clean and target.exists():
        target.unlink()
    if target.is_file() and target.stat().st_size > 1024 * 1024:
        print(f"Already prepared: {target}")
        return 0

    downloaded = Path(
        hf_hub_download(
            repo_id=REPO_ID,
            filename=FILENAME,
            local_dir=ROOT / "output" / "model_sources" / REPO_ID.replace("/", "__"),
        )
    )
    shutil.copy2(downloaded, target)
    if not target.is_file() or target.stat().st_size < 1024 * 1024:
        raise SystemExit(f"Downloaded model looks invalid: {target}")
    print(f"Prepared {PACKAGE_ID}: {target} ({target.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
