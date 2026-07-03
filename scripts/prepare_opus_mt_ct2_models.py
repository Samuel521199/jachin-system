"""Download Helsinki OPUS-MT models and convert them to CTranslate2 INT8 assets.

This prepares Jachin MODEL packages under ``models_repo``. The resulting model
directories can be published to L1 and installed by L3 with one click.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MODELS = {
    "en-zh": {
        "hf_id": "Helsinki-NLP/opus-mt-en-zh",
        "package_id": "com.jachin.model.opus-mt-en-zh-ct2-int8",
    },
    "zh-en": {
        "hf_id": "Helsinki-NLP/opus-mt-zh-en",
        "package_id": "com.jachin.model.opus-mt-zh-en-ct2-int8",
    },
}


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def ensure_command(name: str) -> None:
    if shutil.which(name):
        return
    raise SystemExit(
        f"Missing command: {name}\n"
        "Install dependencies first:\n"
        "  python -m pip install huggingface_hub==1.21.0 ctranslate2==4.8.0 transformers==5.12.1 torch>=2.6\n"
        "  python -m pip install sentencepiece==0.1.99"
    )


def download_snapshot(hf_id: str, cache_dir: Path) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        raise SystemExit(
            "Missing huggingface_hub. Install with:\n"
            "  python -m pip install -U huggingface_hub"
        ) from exc

    cache_dir.mkdir(parents=True, exist_ok=True)
    local = snapshot_download(
        repo_id=hf_id,
        local_dir=cache_dir / hf_id.replace("/", "__"),
        local_dir_use_symlinks=False,
        allow_patterns=[
            "config.json",
            "generation_config.json",
            "pytorch_model.bin",
            "source.spm",
            "target.spm",
            "tokenizer_config.json",
            "vocab.json",
        ],
    )
    return Path(local)


def prepare(direction: str, clean: bool) -> Path:
    spec = MODELS[direction]
    hf_id = spec["hf_id"]
    package_id = spec["package_id"]
    package_dir = ROOT / "models_repo" / package_id
    model_dir = package_dir / "model"
    src_cache = ROOT / "output" / "model_sources"

    if clean and model_dir.exists():
        shutil.rmtree(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    source_dir = download_snapshot(hf_id, src_cache)
    ensure_command("ct2-transformers-converter")
    run(
        [
            "ct2-transformers-converter",
            "--model",
            str(source_dir),
            "--output_dir",
            str(model_dir),
            "--quantization",
            "int8",
            "--force",
            "--copy_files",
            "source.spm",
            "target.spm",
            "vocab.json",
            "tokenizer_config.json",
        ]
    )

    required = ["config.json", "model.bin", "source.spm", "target.spm"]
    missing = [name for name in required if not (model_dir / name).is_file()]
    if missing:
        raise SystemExit(f"Conversion finished but required files are missing: {', '.join(missing)}")
    print(f"Prepared {package_id}: {model_dir}")
    return model_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", choices=["en-zh", "zh-en", "all"], default="all")
    parser.add_argument("--clean", action="store_true", help="Remove existing converted model before conversion")
    args = parser.parse_args()

    directions = ["en-zh", "zh-en"] if args.direction == "all" else [args.direction]
    for direction in directions:
        prepare(direction, args.clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
