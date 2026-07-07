from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_TRANSLATE_DIR = ROOT / "l3_client" / "local_mcps" / "local_translate_mcp"


DEFAULT_EN_TEXTS = [
    "I grabbed the bus before it left the station.",
    "She grabbed a slice of bread before the meeting.",
    "The engineer checked the cache before deployment.",
    "Oil prices changed after the morning report.",
    "The team discussed the project deadline.",
]

DEFAULT_ZH_TEXTS = [
    "我在公交车离站前赶上了车。",
    "工程师在部署前检查了缓存。",
]


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _import_local_translate() -> Any:
    sys.path.insert(0, str(LOCAL_TRANSLATE_DIR))
    try:
        import local_translate  # type: ignore

        return local_translate
    except Exception as exc:
        raise RuntimeError(
            f"无法导入 local_translate.py。请确认路径存在：{LOCAL_TRANSLATE_DIR}\n{exc}"
        ) from exc


def _run_one(local_translate: Any, text: str, direction: str) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        result = local_translate.local_translate_text(text, direction=direction)
        result["elapsed_ms"] = _elapsed_ms(start)
        return result
    except Exception as exc:
        return {
            "ok": False,
            "direction": direction,
            "source": text,
            "elapsed_ms": _elapsed_ms(start),
            "error": str(exc),
        }


def _run_batch(local_translate: Any, texts: list[str], direction: str) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        result = local_translate.local_translate_batch_texts(texts, direction=direction)
        result["elapsed_ms"] = _elapsed_ms(start)
        return result
    except Exception as exc:
        return {
            "ok": False,
            "direction": direction,
            "texts": texts,
            "elapsed_ms": _elapsed_ms(start),
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Jachin local OPUS-MT CTranslate2 translation models.")
    parser.add_argument("--text", action="append", default=[], help="English or Chinese text to translate. Can be repeated.")
    parser.add_argument("--direction", default="auto", choices=["auto", "en-zh", "zh-en"], help="Translation direction.")
    parser.add_argument("--device", default=None, choices=["cpu", "cuda", "auto"], help="Override JACHIN_LOCAL_TRANSLATE_DEVICE.")
    parser.add_argument("--compute-type", default=None, help="Override JACHIN_LOCAL_TRANSLATE_COMPUTE_TYPE, e.g. int8, float16, default.")
    parser.add_argument("--jachin-home", default=None, help="Override JACHIN_HOME for packaged-mode model lookup.")
    parser.add_argument("--skip-warmup", action="store_true", help="Skip warmup timing.")
    parser.add_argument("--json", action="store_true", help="Only print JSON result.")
    args = parser.parse_args()

    if args.jachin_home:
        os.environ["JACHIN_HOME"] = args.jachin_home
    if args.device:
        os.environ["JACHIN_LOCAL_TRANSLATE_DEVICE"] = args.device
    if args.compute_type:
        os.environ["JACHIN_LOCAL_TRANSLATE_COMPUTE_TYPE"] = args.compute_type

    local_translate = _import_local_translate()
    texts = args.text or [*DEFAULT_EN_TEXTS, *DEFAULT_ZH_TEXTS]

    report: dict[str, Any] = {
        "ok": True,
        "repo_root": str(ROOT),
        "local_translate_dir": str(LOCAL_TRANSLATE_DIR),
        "env": {
            "JACHIN_HOME": os.environ.get("JACHIN_HOME", ""),
            "JACHIN_LOCAL_TRANSLATE_DEVICE": os.environ.get("JACHIN_LOCAL_TRANSLATE_DEVICE", ""),
            "JACHIN_LOCAL_TRANSLATE_COMPUTE_TYPE": os.environ.get("JACHIN_LOCAL_TRANSLATE_COMPUTE_TYPE", ""),
        },
        "status": None,
        "warmup": None,
        "single_results": [],
        "batch_results": [],
    }

    try:
        report["status"] = local_translate.local_translate_model_status()
    except Exception as exc:
        report["ok"] = False
        report["status"] = {"ok": False, "error": str(exc)}

    if not args.skip_warmup:
        start = time.perf_counter()
        try:
            warmup = local_translate.local_translate_warmup("all" if args.direction == "auto" else args.direction)
            warmup["elapsed_ms"] = _elapsed_ms(start)
            report["warmup"] = warmup
        except Exception as exc:
            report["ok"] = False
            report["warmup"] = {"ok": False, "elapsed_ms": _elapsed_ms(start), "error": str(exc)}

    for text in texts:
        result = _run_one(local_translate, text, args.direction)
        if not result.get("ok"):
            report["ok"] = False
        report["single_results"].append(result)

    en_texts = [text for text in texts if not any("\u4e00" <= ch <= "\u9fff" for ch in text)]
    zh_texts = [text for text in texts if any("\u4e00" <= ch <= "\u9fff" for ch in text)]
    if en_texts:
        result = _run_batch(local_translate, en_texts, "en-zh")
        if not result.get("ok"):
            report["ok"] = False
        report["batch_results"].append(result)
    if zh_texts:
        result = _run_batch(local_translate, zh_texts, "zh-en")
        if not result.get("ok"):
            report["ok"] = False
        report["batch_results"].append(result)

    if args.json:
        print(_json(report))
        return 0 if report["ok"] else 1

    _print_section("Model status")
    print(_json(report["status"]))
    _print_section("Warmup")
    print(_json(report["warmup"]))
    _print_section("Single translation")
    for item in report["single_results"]:
        print(_json(item))
    _print_section("Batch translation")
    for item in report["batch_results"]:
        print(_json(item))
    _print_section("Summary")
    print("PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
