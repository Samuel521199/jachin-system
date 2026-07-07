from __future__ import annotations

import os
import re
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_IDS = {
    "en-zh": "com.jachin.model.opus-mt-en-zh-ct2-int8",
    "zh-en": "com.jachin.model.opus-mt-zh-en-ct2-int8",
}


def _home() -> Path:
    raw = os.environ.get("JACHIN_HOME")
    if raw:
        return Path(raw)
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".") / ".jachin"


def _model_dir(direction: str) -> Path:
    installed = _home() / "models" / MODEL_IDS[direction] / "model"
    if installed.is_dir():
        return installed
    repo_root = Path(__file__).resolve().parents[3]
    dev_model = repo_root / "models_repo" / MODEL_IDS[direction] / "model"
    if dev_model.is_dir():
        return dev_model
    return installed


def _is_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _direction(text: str, direction: str) -> str:
    raw = (direction or "auto").strip().lower().replace("_", "-")
    if raw in MODEL_IDS:
        return raw
    return "zh-en" if _is_chinese(text) else "en-zh"


def _required_files(direction: str) -> list[Path]:
    base = _model_dir(direction)
    return [base / "config.json", base / "model.bin", base / "source.spm", base / "target.spm"]


def _model_ready(direction: str) -> tuple[bool, list[str]]:
    missing = [str(p) for p in _required_files(direction) if not p.is_file()]
    return not missing, missing


def _cleanup_translation(text: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return clean

    parts = [p.strip() for p in re.split(r"(?<=[。！？.!?])\s+", clean) if p.strip()]
    if len(parts) >= 2 and len(set(parts)) == 1:
        return parts[0]
    return clean


@lru_cache(maxsize=2)
def _load(direction: str):
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
            import ctranslate2
        import sentencepiece as spm
    except Exception as exc:
        raise RuntimeError(
            "Local translation dependencies are missing. Install ctranslate2 and sentencepiece in the L3 runtime."
        ) from exc

    ready, missing = _model_ready(direction)
    if not ready:
        raise RuntimeError(
            "Local translation model is not installed. Open Capability Install Center and download the model asset. "
            f"Missing: {', '.join(missing)}"
        )

    base = _model_dir(direction)
    source = spm.SentencePieceProcessor()
    target = spm.SentencePieceProcessor()
    source.load(str(base / "source.spm"))
    target.load(str(base / "target.spm"))
    requested_device = (os.environ.get("JACHIN_LOCAL_TRANSLATE_DEVICE") or "cpu").strip().lower()
    compute_type = (os.environ.get("JACHIN_LOCAL_TRANSLATE_COMPUTE_TYPE") or "default").strip()
    device = requested_device
    if requested_device == "auto":
        try:
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    try:
        kwargs: dict[str, Any] = {"device": device}
        if compute_type and compute_type != "default":
            kwargs["compute_type"] = compute_type
        translator = ctranslate2.Translator(str(base), **kwargs)
        return source, target, translator, device, kwargs.get("compute_type", "default")
    except Exception:
        if device == "cpu":
            raise
        translator = ctranslate2.Translator(str(base), device="cpu")
        return source, target, translator, "cpu", "default"


def local_translate_model_status() -> dict[str, Any]:
    models = {}
    for direction, model_id in MODEL_IDS.items():
        ready, missing = _model_ready(direction)
        models[direction] = {
            "model_id": model_id,
            "installed": ready,
            "path": str(_model_dir(direction)),
            "missing": missing,
        }
    return {"ok": True, "models": models}


def local_translate_warmup(direction: str = "all") -> dict[str, Any]:
    directions = list(MODEL_IDS) if direction == "all" else [_direction("", direction)]
    warmed = []
    for item in directions:
        _source, _target, _translator, device, compute_type = _load(item)
        warmed.append({"direction": item, "device": device, "compute_type": compute_type})
    return {"ok": True, "warmed": warmed}


def local_translate_text(text: str, direction: str = "auto") -> dict[str, Any]:
    clean = (text or "").strip()
    if not clean:
        return {"ok": False, "error": "text is empty"}
    resolved = _direction(clean, direction)
    source_sp, target_sp, translator, device, compute_type = _load(resolved)
    tokens = source_sp.encode(clean, out_type=str) + ["</s>"]
    result = translator.translate_batch([tokens], beam_size=4, max_decoding_length=160)
    pieces = result[0].hypotheses[0]
    translated = _cleanup_translation(target_sp.decode(pieces))
    return {
        "ok": True,
        "direction": resolved,
        "source": clean,
        "translation": translated,
        "model_id": MODEL_IDS[resolved],
        "device": device,
        "compute_type": compute_type,
    }


def local_translate_batch_texts(texts: list[str], direction: str = "auto") -> dict[str, Any]:
    if not isinstance(texts, list) or len(texts) == 0:
        return {"ok": False, "error": "texts is empty"}

    clean_items = [str(x).strip() for x in texts]
    if any(not item for item in clean_items):
        return {"ok": False, "error": "texts contains empty item"}

    seed = next((item for item in clean_items if item), "")
    resolved = _direction(seed, direction)
    source_sp, target_sp, translator, device, compute_type = _load(resolved)

    encoded_batch: list[list[str]] = []
    for item in clean_items:
        encoded_batch.append(source_sp.encode(item, out_type=str) + ["</s>"])

    results = translator.translate_batch(encoded_batch, beam_size=4, max_decoding_length=160)
    translations: list[str] = []
    for result in results:
        pieces = result.hypotheses[0] if result.hypotheses else []
        translated = _cleanup_translation(target_sp.decode(pieces))
        translations.append(translated)

    return {
        "ok": True,
        "direction": resolved,
        "translations": translations,
        "model_id": MODEL_IDS[resolved],
        "device": device,
        "compute_type": compute_type,
    }
