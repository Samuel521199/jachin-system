from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_MCP = ROOT / "l3_client" / "local_mcps" / "english_example_generator_mcp"


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    return {
        "min_s": round(min(values), 3),
        "avg_s": round(statistics.mean(values), 3),
        "p50_s": round(statistics.median(values), 3),
        "max_s": round(max(values), 3),
    }


def local_status() -> dict[str, Any]:
    sys.path.insert(0, str(EXAMPLE_MCP))
    from example_generator import english_example_model_status

    return english_example_model_status()


def bench_local(words: list[str], book_id: str, meaning_cn: str, use_temp_home: bool) -> list[dict[str, Any]]:
    sys.path.insert(0, str(EXAMPLE_MCP))
    from example_generator import english_generate_example_card

    old_home = os.environ.get("JACHIN_HOME")
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if use_temp_home:
        temp_dir = tempfile.TemporaryDirectory(prefix="jachin_english_bench_")
        os.environ["JACHIN_HOME"] = temp_dir.name
    try:
        results: list[dict[str, Any]] = []
        for word in words:
            started = time.perf_counter()
            try:
                card = english_generate_example_card(word=word, book_id=book_id, meaning_cn=meaning_cn)
                ok = bool(card.get("ok"))
                err = ""
            except Exception as exc:
                card = {}
                ok = False
                err = str(exc)
            elapsed = time.perf_counter() - started
            results.append(
                {
                    "word": word,
                    "ok": ok,
                    "elapsed_s": round(elapsed, 3),
                    "source": str(card.get("source") or ""),
                    "model_id": str(card.get("model_id") or ""),
                    "quality": card.get("quality"),
                    "example": str(card.get("example") or ""),
                    "example_cn": str(card.get("example_cn") or ""),
                    "error": err or str(card.get("error") or ""),
                }
            )
        return results
    finally:
        if old_home is None:
            os.environ.pop("JACHIN_HOME", None)
        else:
            os.environ["JACHIN_HOME"] = old_home
        if temp_dir is not None:
            temp_dir.cleanup()


def bench_local_cold_then_cache(words: list[str], book_id: str, meaning_cn: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sys.path.insert(0, str(EXAMPLE_MCP))
    from example_generator import english_generate_example_card

    old_home = os.environ.get("JACHIN_HOME")
    with tempfile.TemporaryDirectory(prefix="jachin_english_bench_") as temp_home:
        os.environ["JACHIN_HOME"] = temp_home
        try:
            cold: list[dict[str, Any]] = []
            cached: list[dict[str, Any]] = []
            for target in (cold, cached):
                for word in words:
                    started = time.perf_counter()
                    try:
                        card = english_generate_example_card(word=word, book_id=book_id, meaning_cn=meaning_cn)
                        ok = bool(card.get("ok"))
                        err = ""
                    except Exception as exc:
                        card = {}
                        ok = False
                        err = str(exc)
                    elapsed = time.perf_counter() - started
                    target.append(
                        {
                            "word": word,
                            "ok": ok,
                            "elapsed_s": round(elapsed, 3),
                            "source": str(card.get("source") or ""),
                            "model_id": str(card.get("model_id") or ""),
                            "quality": card.get("quality"),
                            "example": str(card.get("example") or ""),
                            "example_cn": str(card.get("example_cn") or ""),
                            "error": err or str(card.get("error") or ""),
                        }
                    )
            return cold, cached
        finally:
            if old_home is None:
                os.environ.pop("JACHIN_HOME", None)
            else:
                os.environ["JACHIN_HOME"] = old_home


def dashscope_key() -> str:
    for key in (
        "DASHSCOPE_API_KEY_CN",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "QWEN_AI_API_KEY",
    ):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def bench_dashscope(words: list[str], book_id: str, meaning_cn: str, model: str) -> list[dict[str, Any]]:
    api_key = dashscope_key()
    if not api_key:
        return [{"word": word, "ok": False, "elapsed_s": 0, "error": "no DashScope api key"} for word in words]
    api_base = (
        os.environ.get("JACHIN_ENGLISH_VOCAB_API_BASE")
        or os.environ.get("DASHSCOPE_API_BASE_CN")
        or os.environ.get("DASHSCOPE_API_BASE")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    scene = {
        "daily_life_ngsl": "daily spoken life",
        "workplace_business": "workplace communication",
        "computer_science": "software engineering",
        "ielts_academic": "IELTS academic writing",
        "toefl_academic": "TOEFL campus study",
    }.get(book_id, "natural modern English")
    results: list[dict[str, Any]] = []
    for word in words:
        prompt = (
            "Return strict compact JSON only with keys: word, meaning_cn, example, example_cn.\n"
            f"Target word: {word}\n"
            f"Chinese meaning hint: {meaning_cn}\n"
            f"Scene: {scene}\n"
            "Generate one natural, scene-appropriate English sentence, 6-16 words, using the exact target word. "
            "No memorization wording, no template wording. Translate the sentence to Simplified Chinese."
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise English vocabulary tutor. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.45,
            "max_tokens": 180,
        }
        req = urllib.request.Request(
            f"{api_base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            elapsed = time.perf_counter() - started
            content = data["choices"][0]["message"]["content"]
            results.append({"word": word, "ok": True, "elapsed_s": round(elapsed, 3), "model": model, "content": content})
        except Exception as exc:
            elapsed = time.perf_counter() - started
            results.append({"word": word, "ok": False, "elapsed_s": round(elapsed, 3), "model": model, "error": str(exc)})
    return results


def print_section(title: str, rows: list[dict[str, Any]]) -> None:
    times = [float(x["elapsed_s"]) for x in rows if x.get("ok")]
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps({"summary": quantiles(times), "rows": rows}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--words", default="office,hotel,bus,walk,parent")
    parser.add_argument("--book-id", default="daily_life_ngsl")
    parser.add_argument("--meaning-cn", default="")
    parser.add_argument("--dashscope-model", default=os.environ.get("JACHIN_ENGLISH_VOCAB_MODEL") or "qwen-turbo")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    words = [w.strip().lower() for w in args.words.split(",") if w.strip()]
    print(json.dumps({"local_status": local_status()}, ensure_ascii=False, indent=2))
    local_cold, local_cache = bench_local_cold_then_cache(words, args.book_id, args.meaning_cn)
    print_section("Local GGUF cold/no-cache example generation", local_cold)
    print_section("Local GGUF same-cache second pass", local_cache)
    print_section("Local current-home cached/warm example generation", bench_local(words, args.book_id, args.meaning_cn, False))
    print_section(f"DashScope large/remote model ({args.dashscope_model})", bench_dashscope(words, args.book_id, args.meaning_cn, args.dashscope_model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
