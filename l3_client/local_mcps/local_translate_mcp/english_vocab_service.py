from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from english_example_pack import example_pack_status, lookup_example_pack
from english_vocab_dictionary import dictionary_size, lookup_word, normalize_word
from local_translate import (
    local_translate_batch_texts,
    local_translate_model_status,
    local_translate_text,
    local_translate_warmup,
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


SERVICE_MODEL = "english_vocab_local_service_v1"
_STARTED_AT = time.time()
_WARMED: set[str] = set()
_CACHE_LOCK = threading.RLock()
_COMPLETION_CACHE_VERSION = 14
_QUALITY_LOCK = threading.RLock()
_EXAMPLE_GENERATE_LOCK = threading.Lock()
_AI_REFRESH_LOCK = threading.Lock()
_AI_REFRESH_INFLIGHT: set[str] = set()
_EXAMPLE_WARMUP_STARTED = False
_LOW_QUALITY_THRESHOLD = _env_float("JACHIN_ENGLISH_VOCAB_LOW_QUALITY_THRESHOLD", 0.9)
_SERVICE_REGEN_MAX = max(0, _env_int("JACHIN_ENGLISH_VOCAB_SERVICE_REGEN_MAX", 1))
_QUALITY_LOG_ENABLED = str(os.environ.get("JACHIN_ENGLISH_VOCAB_QUALITY_LOG") or "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
_QUALITY_METRICS: dict[str, int] = {
    "lookup_total": 0,
    "lookup_fallback_total": 0,
    "generate_total": 0,
    "generate_fallback_total": 0,
    "low_score_regen_count": 0,
    "low_score_detected_total": 0,
    "last_event_ms": 0,
}


def _add_example_generator_path() -> None:
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    candidates = [
        repo_root / "l3_client" / "local_mcps" / "english_example_generator_mcp",
        _home() / "l3_mcp_cache" / "com.jachin.mcp.english-example-generator",
        _home() / "local_mcps" / "english_example_generator_mcp",
        _home() / "mcp" / "english_example_generator_mcp",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)


def _example_generator_cli_path() -> Path | None:
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    candidates = [
        repo_root
        / "l3_client"
        / "local_mcps"
        / "english_example_generator_mcp"
        / "example_generator_cli.py",
        _home()
        / "l3_mcp_cache"
        / "com.jachin.mcp.english-example-generator"
        / "example_generator_cli.py",
        _home() / "local_mcps" / "english_example_generator_mcp" / "example_generator_cli.py",
        _home() / "mcp" / "english_example_generator_mcp" / "example_generator_cli.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _home() -> Path:
    raw = os.environ.get("JACHIN_HOME")
    if raw:
        return Path(raw)
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".") / ".jachin"


_DOTENV_CACHE: dict[str, str] | None = None


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    except Exception:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            values[key] = value
    return values


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _merged_env_values() -> dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None:
        return dict(_DOTENV_CACHE)
    merged: dict[str, str] = {}
    candidates: list[Path] = []
    bundle_env = os.environ.get("JACHIN_DESKTOP_BUNDLE_ENV_FILE")
    if bundle_env:
        candidates.append(Path(bundle_env))
    app_root = os.environ.get("JACHIN_APP_ROOT")
    if app_root:
        candidates.append(Path(app_root) / ".env")
    candidates.append(_repo_root() / ".env")
    for path in candidates:
        merged.update(_read_env_file(path))
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    _DOTENV_CACHE = dict(merged)
    return merged


def _first_env(keys: list[str], default: str = "") -> str:
    env = _merged_env_values()
    for key in keys:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return default


def _json_response(ok: bool, payload: dict[str, Any] | None = None, **extra: Any) -> bytes:
    body = {"ok": ok}
    if payload:
        body.update(payload)
    body.update(extra)
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _service_log(event: str, **payload: Any) -> None:
    row = {
        "event": event,
        "service": SERVICE_MODEL,
        "ts_ms": int(time.time() * 1000),
        **payload,
    }
    text = json.dumps(row, ensure_ascii=False)
    print("[EnglishVocabService] " + text, flush=True)
    path = Path(os.environ.get("JACHIN_ENGLISH_VOCAB_SERVICE_LOG") or _home() / "logs" / "english_vocab_service.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        pass
    if event.startswith(("lookup_", "generate_", "background_ai_example_")):
        _example_chain_log(event, layer="python_service", **payload)


def _example_chain_log(stage: str, **payload: Any) -> None:
    row = {
        "stage": stage,
        "service": SERVICE_MODEL,
        "ts_ms": int(time.time() * 1000),
        **payload,
    }
    text = json.dumps(row, ensure_ascii=False)
    path = Path(
        os.environ.get("JACHIN_ENGLISH_EXAMPLE_CHAIN_LOG")
        or _home() / "logs" / "english_example_chain.jsonl"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        return


def _quality_total(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict):
        return None
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        return None
    raw = quality.get("total")
    try:
        value = float(raw)
    except Exception:
        return None
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def _clean_translated_meaning(text: str) -> str:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return ""
    if len(clean) % 2 == 0:
        half = len(clean) // 2
        if half >= 1 and clean[:half] == clean[half:]:
            clean = clean[:half]
    parts = re.split(r"([；;，,、\s]+)", clean)
    if len(parts) > 1:
        seen: set[str] = set()
        out: list[str] = []
        for part in parts:
            key = part.strip()
            if not key or re.fullmatch(r"[；;，,、\s]+", part):
                out.append(part)
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(part)
        clean = "".join(out).strip("；;，,、 ")
    return clean


def _clean_meaning_candidate(raw_word: str, text: str) -> str:
    raw = (raw_word or "").strip().lower()
    clean = str(text or "").strip()
    if raw and clean.lower().startswith(raw):
        rest = clean[len(raw) :].lstrip("：: -")
        if rest:
            return f"{raw}: {_clean_translated_meaning(rest)}"
    return _clean_translated_meaning(clean)


def _is_fallback_source(source: str) -> bool:
    text = str(source or "").strip().lower()
    if not text:
        return False
    if text.startswith("trusted_semantic_"):
        return False
    return "fallback" in text or text.startswith("local_scene")


def _quality_log(event: str, **payload: Any) -> None:
    if not _QUALITY_LOG_ENABLED:
        return
    try:
        row = {
            "event": event,
            "service": SERVICE_MODEL,
            "ts_ms": int(time.time() * 1000),
            **payload,
        }
        print("[EnglishVocabQuality] " + json.dumps(row, ensure_ascii=False), flush=True)
    except Exception:
        return


def _quality_metrics_add(**increments: int) -> None:
    with _QUALITY_LOCK:
        for key, value in increments.items():
            if key not in _QUALITY_METRICS:
                continue
            _QUALITY_METRICS[key] = int(_QUALITY_METRICS.get(key, 0)) + int(value)
        _QUALITY_METRICS["last_event_ms"] = int(time.time() * 1000)


def _quality_metrics_snapshot() -> dict[str, Any]:
    with _QUALITY_LOCK:
        metrics = dict(_QUALITY_METRICS)
    lookup_total = max(0, int(metrics.get("lookup_total", 0)))
    lookup_fallback_total = max(0, int(metrics.get("lookup_fallback_total", 0)))
    generate_total = max(0, int(metrics.get("generate_total", 0)))
    generate_fallback_total = max(0, int(metrics.get("generate_fallback_total", 0)))
    lookup_fallback_rate = round(lookup_fallback_total / lookup_total, 4) if lookup_total else 0.0
    generate_fallback_rate = round(generate_fallback_total / generate_total, 4) if generate_total else 0.0
    return {
        **metrics,
        "lookup_fallback_rate": lookup_fallback_rate,
        "generate_fallback_rate": generate_fallback_rate,
        "low_quality_threshold": _LOW_QUALITY_THRESHOLD,
        "service_regen_max": _SERVICE_REGEN_MAX,
    }


def _record_lookup_quality(result: dict[str, Any], word: str, book_id: str) -> None:
    source = str(result.get("source") or "")
    fallback = _is_fallback_source(source)
    _quality_metrics_add(
        lookup_total=1,
        lookup_fallback_total=1 if fallback else 0,
    )
    _quality_log(
        "lookup_result",
        word=word,
        book_id=book_id,
        source=source,
        fallback=fallback,
        quality_total=_quality_total(result),
    )


def _finalize_lookup(result: dict[str, Any], word: str, book_id: str) -> dict[str, Any]:
    if isinstance(result, dict) and result.get("ok"):
        _record_lookup_quality(result, word=word, book_id=book_id)
    return result


def _completion_cache_path() -> Path:
    return _home() / "data" / "english_vocab" / "completion_cache.json"


def _completion_cache_key(book_id: str, word: str) -> str:
    return f"{book_id.strip() or 'daily_life_ngsl'}:{normalize_word(word)}"


def _sentence_cache_key(sentence: str) -> str:
    return "sentence:" + " ".join((sentence or "").strip().lower().split())


def _read_completion_cache() -> dict[str, Any]:
    path = _completion_cache_path()
    if not path.is_file():
        return {"version": _COMPLETION_CACHE_VERSION, "items": {}, "sentences": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": _COMPLETION_CACHE_VERSION, "items": {}, "sentences": {}}
    if not isinstance(raw, dict):
        return {"version": _COMPLETION_CACHE_VERSION, "items": {}, "sentences": {}}
    if int(raw.get("version") or 0) != _COMPLETION_CACHE_VERSION:
        return {"version": _COMPLETION_CACHE_VERSION, "items": {}, "sentences": raw.get("sentences") if isinstance(raw.get("sentences"), dict) else {}}
    raw.setdefault("version", _COMPLETION_CACHE_VERSION)
    raw.setdefault("items", {})
    raw.setdefault("sentences", {})
    if not isinstance(raw.get("items"), dict):
        raw["items"] = {}
    if not isinstance(raw.get("sentences"), dict):
        raw["sentences"] = {}
    return raw


def _write_completion_cache(cache: dict[str, Any]) -> None:
    path = _completion_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _is_good_meaning(text: str) -> bool:
    clean = (text or "").strip()
    if not clean or not _contains_cjk(clean):
        return False
    if "??" in clean:
        return False
    cjk_count = sum(1 for ch in clean if "\u4e00" <= ch <= "\u9fff")
    return cjk_count >= 2


_SEMANTIC_PLACE_WORDS = {
    "airport",
    "bank",
    "beach",
    "building",
    "campus",
    "city",
    "classroom",
    "country",
    "hotel",
    "hospital",
    "kitchen",
    "lab",
    "laboratory",
    "library",
    "market",
    "museum",
    "office",
    "park",
    "restaurant",
    "river",
    "road",
    "room",
    "school",
    "station",
    "store",
    "street",
    "village",
}

_SEMANTIC_ACTION_WORDS = {
    "call",
    "clean",
    "cook",
    "drive",
    "learn",
    "listen",
    "read",
    "run",
    "shop",
    "study",
    "talk",
    "travel",
    "wait",
    "walk",
    "work",
    "write",
}
_SEMANTIC_FOOD_WORDS = {
    "breakfast",
    "bread",
    "chicken",
    "coffee",
    "dinner",
    "egg",
    "fruit",
    "lunch",
    "meal",
    "milk",
    "oil",
    "rice",
    "tea",
    "vegetable",
    "water",
}
_SEMANTIC_OBJECT_WORDS = {
    "bag",
    "book",
    "charger",
    "computer",
    "key",
    "keys",
    "laptop",
    "medicine",
    "package",
    "phone",
    "receipt",
    "ticket",
    "umbrella",
    "wallet",
}


def _semantic_category_for_quality(word: str) -> str:
    clean = normalize_word(word)
    if clean in _SEMANTIC_ACTION_WORDS:
        return "action"
    if clean in _SEMANTIC_PLACE_WORDS:
        return "place"
    if clean in _SEMANTIC_FOOD_WORDS:
        return "food"
    if clean in _SEMANTIC_OBJECT_WORDS:
        return "portable_object"
    return "generic"


def _looks_semantically_bad_example(example: str, word: str) -> bool:
    text = " ".join((example or "").strip().lower().split())
    clean = normalize_word(word)
    if not text or not clean:
        return False
    escaped = re.escape(clean)
    category = _semantic_category_for_quality(clean)
    if category == "place" and (
        re.search(rf"\b(left|put|packed|grabbed|bought|cooked|ate|drank)\b[^.?!]*\b{escaped}\b", text)
        or re.search(rf"\b{escaped}\b[^.?!]*\b(on|in) the kitchen table\b", text)
        or f"discount on {clean}" in text
        or f"{clean} was on sale" in text
    ):
        return True
    if category != "food" and (
        re.search(rf"\b(cooked|ate|drank|ordered)\b[^.?!]*\b{escaped}\b", text)
        or re.search(rf"\b{escaped}\b[^.?!]*\b(after dinner|for breakfast)\b", text)
    ):
        return True
    if category != "portable_object" and re.search(
        rf"\b(grabbed|packed|left|put)\b[^.?!]*\b{escaped}\b[^.?!]*\b(on the table|in the bag|before getting on the bus)\b",
        text,
    ):
        return True
    if category in {"action", "abstract"} and (
        re.search(rf"\b(saw|noticed|enjoyed)\b\s+the\s+{escaped}\b", text)
        or re.search(rf"\btalked about\s+the\s+{escaped}\b", text)
        or re.search(rf"\bthe\s+{escaped}\b\s+on my way home\b", text)
    ):
        return True
    return False


def _looks_like_placeholder_example(example: str, word: str = "") -> bool:
    text = " ".join((example or "").strip().lower().split())
    if not text:
        return True
    if _looks_semantically_bad_example(text, word):
        return True
    bad_fragments = [
        "the morning we",
        "came up in a normal conversation",
        "we met near the",
        "while walking home",
        "want to remember",
        "want to learn",
        "learn the word",
        "remember the word",
        "clear example for",
        "people discussed the",
        "the article mentioned the",
        "she noticed the",
        "we included the",
        "plays an important role in daily life",
        "had a discount on",
        "was on sale",
        "on the kitchen table",
        "before getting on the bus",
        "packed the ",
        "we should ",
        "this option feels ",
        "put the ",
        "will be refreshed",
        "useful in everyday conversation",
        "while preparing dinner",
        "while preparing for the day",
        "while making a simple plan",
        "during their weekend errands",
        "at home last night",
    ]
    if any(fragment in text for fragment in bad_fragments):
        return True
    clean_word = normalize_word(word)
    if clean_word and clean_word not in re_words(text):
        return True
    return False


def _looks_like_low_diversity_example(example: str, word: str = "") -> bool:
    text = " ".join((example or "").strip().lower().split())
    clean_word = normalize_word(word)
    if not text or not clean_word:
        return False
    words = re_words(text)
    if clean_word not in words:
        return True
    token_count = len(re.findall(r"[a-z]+(?:'[a-z]+)?", text))
    detail_markers = [
        "after",
        "although",
        "because",
        "before",
        "beside",
        "during",
        "from",
        "if",
        "inside",
        "near",
        "outside",
        "when",
        "while",
        "with",
        "without",
    ]
    generic_verbs = ["explained", "mentioned", "reviewed", "discussed", "used"]
    starts_generic = text.startswith(("the manager ", "the team ", "people ", "we "))
    has_detail = any(marker in text for marker in detail_markers)
    if token_count <= 6 and not has_detail:
        return True
    if starts_generic and any(verb in text for verb in generic_verbs) and not has_detail:
        return True
    return False


def re_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+(?:'[a-z]+)?", (text or "").lower()))


def _is_complete_card(card: dict[str, Any] | None, require_example: bool = True) -> bool:
    if not isinstance(card, dict):
        return False
    meaning = str(card.get("meaning_cn") or "").strip()
    example = str(card.get("example") or "").strip()
    example_cn = str(card.get("example_cn") or "").strip()
    word = str(card.get("base_word") or card.get("word") or "").strip()
    if not _is_good_meaning(meaning):
        return False
    if not require_example:
        return True
    return (
        bool(example)
        and not _looks_like_placeholder_example(example, word)
        and not _looks_like_low_diversity_example(example, word)
        and not _is_fallback_source(str(card.get("source") or ""))
        and not _is_fallback_source(str(card.get("model") or ""))
        and bool(example_cn)
        and "??" not in example_cn
        and not (bool(word) and re.search(rf"\b{re.escape(word.lower())}\b", example_cn.lower()))
        and _contains_cjk(example_cn)
    )


def _generated_example_is_acceptable(generated: dict[str, Any], word: str) -> bool:
    if not generated.get("ok"):
        return False
    source = str(generated.get("source") or "")
    model = str(generated.get("model_id") or generated.get("model") or "")
    example = str(generated.get("example") or "").strip()
    if _is_fallback_source(source) or _is_fallback_source(model):
        return False
    return bool(example) and not _looks_like_placeholder_example(example, word)


def _cache_get_card(book_id: str, word: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        cache = _read_completion_cache()
        item = cache.get("items", {}).get(_completion_cache_key(book_id, word))
    return item if isinstance(item, dict) else None


def _cache_delete_card(book_id: str, word: str) -> None:
    key = _completion_cache_key(book_id, word)
    with _CACHE_LOCK:
        cache = _read_completion_cache()
        items = cache.setdefault("items", {})
        if key in items:
            items.pop(key, None)
            _write_completion_cache(cache)


def _card_is_remote_completion(card: dict[str, Any] | None) -> bool:
    if not isinstance(card, dict):
        return False
    source = str(card.get("source") or "").strip().lower()
    model = str(card.get("model") or card.get("model_id") or "").strip().lower()
    return (
        "dashscope" in source
        or "qwen-turbo" in model
        or "model_reviewed" in source
        or "llm_reviewed" in source
        or source in {"qwen_turbo", "remote_qwen_turbo"}
    )


def _is_complete_remote_card(card: dict[str, Any] | None) -> bool:
    if not isinstance(card, dict):
        return False
    meaning = str(card.get("meaning_cn") or "").strip()
    example = str(card.get("example") or "").strip()
    example_cn = str(card.get("example_cn") or "").strip()
    word = str(card.get("base_word") or card.get("word") or "").strip()
    return (
        _card_is_remote_completion(card)
        and _is_good_meaning(meaning)
        and bool(example)
        and not _looks_like_placeholder_example(example, word)
        and not _looks_like_low_diversity_example(example, word)
        and bool(word)
        and word.lower() in re_words(example)
        and bool(example_cn)
        and "??" not in example_cn
        and "�" not in example_cn
        and "锛" not in example_cn
        and "鈥" not in example_cn
        and "€" not in example_cn
        and "鎴" not in example_cn
        and "鍗" not in example_cn
        and not re.search(rf"\b{re.escape(word.lower())}\b", example_cn.lower())
        and _contains_cjk(example_cn)
    )


def _cache_put_card(book_id: str, word: str, card: dict[str, Any]) -> None:
    # The completion cache is what the foreground card trusts for instant
    # display. Keep it final-grade only; local GGUF/template outputs may be used
    # as background material, but must never pollute the user-facing cache.
    if not _is_complete_remote_card(card):
        return
    key = _completion_cache_key(book_id, word)
    payload = {
        **card,
        "base_word": str(card.get("base_word") or normalize_word(word)),
        "cached_at": int(time.time() * 1000),
        "cache_version": _COMPLETION_CACHE_VERSION,
    }
    with _CACHE_LOCK:
        cache = _read_completion_cache()
        cache.setdefault("items", {})[key] = payload
        _write_completion_cache(cache)


def _cache_get_sentence(sentence: str) -> str:
    key = _sentence_cache_key(sentence)
    with _CACHE_LOCK:
        cache = _read_completion_cache()
        value = cache.get("sentences", {}).get(key)
    return str(value or "").strip()


def _cache_put_sentence(sentence: str, translation: str) -> None:
    clean = (sentence or "").strip()
    translated = (translation or "").strip()
    if not clean or not translated or not _contains_cjk(translated):
        return
    key = _sentence_cache_key(clean)
    with _CACHE_LOCK:
        cache = _read_completion_cache()
        cache.setdefault("sentences", {})[key] = translated
        _write_completion_cache(cache)


def _translate_sentence(text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    cached = _cache_get_sentence(clean)
    if cached:
        return cached
    result = local_translate_text(clean, direction="en-zh")
    if result.get("ok"):
        translated = str(result.get("translation") or "").strip()
        _cache_put_sentence(clean, translated)
        return translated
    return ""


def _pack_generator_is_ai_reviewed(generator: str) -> bool:
    text = str(generator or "").strip().lower()
    return any(marker in text for marker in ["dashscope_reviewed", "model_reviewed", "llm_reviewed"])


def _schedule_ai_example_refresh(
    word: str,
    book_id: str,
    meaning_cn: str,
    base_word: str,
    current_card: dict[str, Any],
) -> None:
    enabled = str(os.environ.get("JACHIN_ENGLISH_BACKGROUND_AI_EXAMPLES") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    if not enabled:
        return
    clean = normalize_word(base_word or word)
    if not clean:
        return
    key = _completion_cache_key(book_id, clean)
    with _AI_REFRESH_LOCK:
        if key in _AI_REFRESH_INFLIGHT:
            return
        _AI_REFRESH_INFLIGHT.add(key)

    def worker() -> None:
        acquired = False
        try:
            acquired = _EXAMPLE_GENERATE_LOCK.acquire(blocking=False)
            if not acquired:
                _service_log("background_ai_example_refresh_skipped_busy", word=clean, book_id=book_id)
                return
            started = time.time()
            generated = _generate_example_once(clean, book_id, meaning_cn)
            if not _generated_example_is_acceptable(generated, clean):
                _service_log(
                    "background_ai_example_refresh_failed",
                    word=clean,
                    book_id=book_id,
                    error=str(generated.get("error") or "not acceptable"),
                    source=str(generated.get("source") or ""),
                    elapsed_ms=int((time.time() - started) * 1000),
                )
                return
            example = str(generated.get("example") or "").strip()
            example_cn = str(generated.get("example_cn") or "").strip() or _translate_sentence(example)
            if not example or not example_cn:
                _service_log(
                    "background_ai_example_refresh_failed",
                    word=clean,
                    book_id=book_id,
                    error="missing example translation",
                    elapsed_ms=int((time.time() - started) * 1000),
                )
                return
            upgraded = {
                **current_card,
                "word": str(current_card.get("word") or clean),
                "base_word": clean,
                "example": example,
                "example_cn": example_cn,
                "source": str(generated.get("source") or "background_ai_example"),
                "model": str(generated.get("model_id") or generated.get("model") or SERVICE_MODEL),
                "quality": generated.get("quality") if isinstance(generated.get("quality"), dict) else current_card.get("quality"),
            }
            if _is_complete_card(upgraded):
                _cache_put_card(book_id, clean, upgraded)
                _service_log(
                    "background_ai_example_refresh_done",
                    word=clean,
                    book_id=book_id,
                    source=str(upgraded.get("source") or ""),
                    elapsed_ms=int((time.time() - started) * 1000),
                )
        except Exception as exc:
            _service_log("background_ai_example_refresh_exception", word=clean, book_id=book_id, error=str(exc))
        finally:
            if acquired:
                try:
                    _EXAMPLE_GENERATE_LOCK.release()
                except RuntimeError:
                    pass
            with _AI_REFRESH_LOCK:
                _AI_REFRESH_INFLIGHT.discard(key)

    threading.Thread(target=worker, name=f"english-ai-example-refresh-{clean}", daemon=True).start()


def _translate_sentences(texts: list[str]) -> list[str]:
    clean = [str(x).strip() for x in texts if str(x).strip()]
    if not clean:
        return []
    result = local_translate_batch_texts(clean, direction="en-zh")
    if result.get("ok"):
        return [str(x).strip() for x in result.get("translations") or []]
    return [_translate_sentence(item) for item in clean]


def _fallback_meaning(
    raw_word: str,
    base_word: str,
    definition: dict[str, Any] | None = None,
    cached_card: dict[str, Any] | None = None,
) -> str:
    raw = (raw_word or "").strip().lower()
    base = (base_word or "").strip().lower() or raw
    candidates = [
        str((definition or {}).get("meaning_cn") or "").strip(),
        str((cached_card or {}).get("meaning_cn") or "").strip(),
    ]
    for item in candidates:
        cleaned = _clean_meaning_candidate(raw, item)
        if _is_good_meaning(cleaned):
            return cleaned

    for probe in [base, raw]:
        if not probe:
            continue
        translated = local_translate_text(probe, direction="en-zh")
        if translated.get("ok"):
            text = _clean_translated_meaning(str(translated.get("translation") or "").strip())
            if _is_good_meaning(text):
                if raw:
                    return f"{raw}：{text}"
                return text

    if raw.endswith("er") and len(raw) > 3:
        return f"{raw}：常见于比较语境，表示“更……”。"
    return f"{raw or base}：请结合上下文理解该词含义。"


def _fallback_example(word: str) -> str:
    clean = normalize_word(word) or "word"
    if clean.endswith(("ize", "ise", "ify", "ate", "en", "yze")):
        templates = [
            "Leaders hope to {word} current conditions over time.",
            "We need to {word} the process before the final review.",
            "Teams plan to {word} the situation step by step.",
            "Experts try to {word} weak areas before launch.",
        ]
    elif clean.endswith(("ous", "ful", "able", "ible", "ive", "al", "ic", "less", "ent", "ant", "ary", "ory", "ish")):
        templates = [
            "The new plan seems {word} compared with last year's approach.",
            "This solution is more {word} than the previous one.",
            "Their proposal looks {word} but still practical.",
            "The final result felt {word} to most users.",
        ]
    else:
        templates = [
            "The {word} has become a common topic in recent discussions.",
            "Many people rely on the {word} in everyday life.",
            "The report highlights how the {word} affects local communities.",
            "We discussed the {word} while planning next month.",
        ]
    idx = sum(ord(ch) for ch in clean) % len(templates)
    return templates[idx].format(word=clean)


def _example_model_status() -> dict[str, Any]:
    _add_example_generator_path()
    try:
        from example_generator import english_example_model_status

        return english_example_model_status()
    except Exception as exc:
        return {"ok": True, "model_installed": False, "runtime_ready": False, "runtime_error": str(exc)}


def _generate_example_once(word: str, book_id: str, meaning_cn: str) -> dict[str, Any]:
    cli = _example_generator_cli_path()
    # Keep the generator in-process by default so the GGUF model stays warm.
    # Set JACHIN_ENGLISH_EXAMPLE_USE_CLI=1 only when debugging isolation issues.
    use_cli = str(os.environ.get("JACHIN_ENGLISH_EXAMPLE_USE_CLI") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if use_cli and cli and cli.is_file():
        env = os.environ.copy()
        py_path = env.get("PYTHONPATH", "").strip()
        addon = str(cli.parent)
        env["PYTHONPATH"] = addon if not py_path else os.pathsep.join([addon, py_path])
        cmd = [
            sys.executable,
            str(cli),
            "generate",
            "--word",
            str(word or ""),
            "--book-id",
            str(book_id or "daily_life_ngsl"),
            "--meaning-cn",
            str(meaning_cn or ""),
        ]
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=max(2, _env_int("JACHIN_ENGLISH_EXAMPLE_CLI_TIMEOUT_SEC", 8)),
                check=False,
            )
            if proc.returncode == 0:
                payload = json.loads((proc.stdout or "").strip() or "{}")
                if payload.get("ok") and str(payload.get("example") or "").strip():
                    return payload
                return {"ok": False, "error": str(payload.get("error") or "example generator returned not ok")}
            stderr = (proc.stderr or "").strip()
            return {"ok": False, "error": f"example generator exited {proc.returncode}: {stderr[:280]}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "example generator timed out"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    _add_example_generator_path()
    try:
        from example_generator import english_generate_example_card

        result = english_generate_example_card(word=word, book_id=book_id, meaning_cn=meaning_cn)
        if result.get("ok") and str(result.get("example") or "").strip():
            return result
        return {"ok": False, "error": str(result.get("error") or "example generator returned not ok")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _trusted_example_fallback(word: str, book_id: str, meaning_cn: str) -> dict[str, Any]:
    """Return only curated/semantic examples; never generic fill-in templates."""
    _add_example_generator_path()
    try:
        from example_generator import _example_quality_score, _template_draft

        example, example_cn, template_id = _template_draft(normalize_word(word) or word, book_id, meaning_cn)
        template_id = str(template_id or "")
        trusted = template_id.startswith(("specific_", "scene_", "semantic_"))
        quality = _example_quality_score(example, normalize_word(word) or word, meaning_cn, book_id)
        if (
            trusted
            and _generated_example_is_acceptable({"ok": True, "example": example}, normalize_word(word) or word)
            and not _looks_like_low_diversity_example(example, normalize_word(word) or word)
            and float(quality.get("total") or 0.0) >= 0.82
        ):
            return {
                "ok": True,
                "example": example,
                "example_cn": example_cn,
                "model_id": "trusted_semantic_example_bank_v1",
                "source": "trusted_semantic_fallback",
                "template_id": template_id,
                "quality": quality,
            }
    except Exception as exc:
        return {"ok": False, "error": f"trusted fallback failed: {exc}"}
    return {"ok": False, "error": "no trusted semantic example"}


def _pick_better_generate_result(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_ok = bool(current.get("ok"))
    candidate_ok = bool(candidate.get("ok"))
    if candidate_ok and not current_ok:
        return candidate
    if current_ok and not candidate_ok:
        return current
    if not current_ok and not candidate_ok:
        return current

    current_fallback = _is_fallback_source(str(current.get("source") or ""))
    candidate_fallback = _is_fallback_source(str(candidate.get("source") or ""))
    if current_fallback != candidate_fallback:
        return candidate if not candidate_fallback else current

    current_score = _quality_total(current)
    candidate_score = _quality_total(candidate)
    if candidate_score is None and current_score is None:
        return current
    if candidate_score is None:
        return current
    if current_score is None:
        return candidate
    return candidate if candidate_score > current_score else current


def _generate_example(word: str, book_id: str, meaning_cn: str) -> dict[str, Any]:
    service_model_first = str(os.environ.get("JACHIN_ENGLISH_EXAMPLE_SERVICE_MODEL_FIRST") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    packed = lookup_example_pack(word, book_id)
    if packed and packed.get("ok") and not service_model_first:
        _quality_log(
            "generate_example_pack_hit",
            word=normalize_word(word),
            book_id=book_id,
            source=str(packed.get("source") or ""),
            quality_total=_quality_total(packed),
        )
        packed["service_regen_attempts"] = 0
        return packed

    if not service_model_first:
        trusted_first = _trusted_example_fallback(word, book_id, meaning_cn)
        if trusted_first.get("ok"):
            _quality_log(
                "generate_trusted_first",
                word=normalize_word(word),
                book_id=book_id,
                source=str(trusted_first.get("source") or ""),
                quality_total=_quality_total(trusted_first),
            )
            trusted_first["service_regen_attempts"] = 0
            return trusted_first

    if not _EXAMPLE_GENERATE_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "example generator is busy"}
    try:
        result = _generate_example_once(word, book_id, meaning_cn)
    finally:
        _EXAMPLE_GENERATE_LOCK.release()
    regen_count = 0
    low_score_detected = 0
    score = _quality_total(result)
    source = str(result.get("source") or "")
    fallback = _is_fallback_source(source)

    while (
        result.get("ok")
        and not fallback
        and score is not None
        and score < _LOW_QUALITY_THRESHOLD
        and regen_count < _SERVICE_REGEN_MAX
    ):
        low_score_detected = 1
        regen_count += 1
        retried = _generate_example_once(word, book_id, meaning_cn)
        result = _pick_better_generate_result(result, retried)
        score = _quality_total(result)
        source = str(result.get("source") or "")
        fallback = _is_fallback_source(source)
        if score is not None and score >= _LOW_QUALITY_THRESHOLD:
            break

    _quality_metrics_add(
        generate_total=1,
        generate_fallback_total=1 if (result.get("ok") and fallback) else 0,
        low_score_regen_count=regen_count,
        low_score_detected_total=low_score_detected,
    )
    _quality_log(
        "generate_result",
        word=normalize_word(word),
        book_id=book_id,
        source=source,
        fallback=fallback,
        quality_total=score,
        service_regen_attempts=regen_count,
    )

    if result.get("ok"):
        enriched = dict(result)
        enriched["service_regen_attempts"] = regen_count
        return enriched
    if packed and packed.get("ok"):
        _quality_log(
            "generate_example_pack_fallback_after_model",
            word=normalize_word(word),
            book_id=book_id,
            source=str(packed.get("source") or ""),
            quality_total=_quality_total(packed),
            original_error=str(result.get("error") or ""),
        )
        packed["service_regen_attempts"] = regen_count
        return packed
    trusted = _trusted_example_fallback(word, book_id, meaning_cn)
    if trusted.get("ok"):
        _quality_log(
            "generate_trusted_fallback",
            word=normalize_word(word),
            book_id=book_id,
            source=str(trusted.get("source") or ""),
            quality_total=_quality_total(trusted),
            original_error=str(result.get("error") or ""),
        )
        trusted["service_regen_attempts"] = regen_count
        return trusted
    return result


def _status() -> dict[str, Any]:
    with _CACHE_LOCK:
        cache = _read_completion_cache()
    return {
        "service": SERVICE_MODEL,
        "uptime_ms": int((time.time() - _STARTED_AT) * 1000),
        "warmed": sorted(_WARMED),
        "dictionary_size": dictionary_size(),
        "completion_cache": {
            "path": str(_completion_cache_path()),
            "cards": len(cache.get("items") or {}),
            "sentences": len(cache.get("sentences") or {}),
        },
        "example_pack": example_pack_status(),
        "translate": local_translate_model_status(),
        "example": _example_model_status(),
        "quality_metrics": _quality_metrics_snapshot(),
    }


def _warmup(payload: dict[str, Any]) -> dict[str, Any]:
    direction = str(payload.get("direction") or "en-zh").strip() or "en-zh"
    result = local_translate_warmup(direction=direction)
    if result.get("ok"):
        for item in result.get("warmed") or []:
            _WARMED.add(str(item))
    _schedule_example_model_warmup()
    return {"ok": True, "warmup": result, "status": _status()}


def _schedule_example_model_warmup() -> None:
    global _EXAMPLE_WARMUP_STARTED
    enabled = str(os.environ.get("JACHIN_ENGLISH_EXAMPLE_WARMUP") or "0").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    if not enabled or _EXAMPLE_WARMUP_STARTED:
        return
    _EXAMPLE_WARMUP_STARTED = True

    def worker() -> None:
        started = time.time()
        try:
            warmup_words = [
                ("bedroom", "\u5367\u5ba4\uff1b\u7761\u623f"),
                ("kitchen", "\u53a8\u623f"),
                ("family", "\u5bb6\u5ead\uff1b\u5bb6\u4eba"),
                ("school", "\u5b66\u6821"),
                ("station", "\u8f66\u7ad9\uff1b\u7ad9\u70b9"),
                ("airport", "\u673a\u573a"),
                ("morning", "早晨；上午"),
                ("child", "孩子；儿童"),
                ("office", "办公室；办事处"),
                ("hotel", "旅馆；酒店"),
                ("bus", "公共汽车"),
                ("walk", "走路；步行；散步"),
            ]
            generated = {"ok": False, "error": "no warmup words"}
            ok_count = 0
            for word, meaning in warmup_words:
                generated = _generate_example_once(word, "daily_life_ngsl", meaning)
                if generated.get("ok"):
                    ok_count += 1
            _service_log(
                "example_model_warmup_done" if ok_count else "example_model_warmup_failed",
                word=",".join(word for word, _meaning in warmup_words),
                source=str(generated.get("source") or ""),
                model=str(generated.get("model_id") or generated.get("model") or ""),
                error=str(generated.get("error") or ""),
                ok_count=ok_count,
                elapsed_ms=int((time.time() - started) * 1000),
            )
            if ok_count:
                _WARMED.add("example")
        except Exception as exc:
            _service_log(
                "example_model_warmup_exception",
                word="morning",
                error=str(exc),
                elapsed_ms=int((time.time() - started) * 1000),
            )

    threading.Thread(target=worker, name="english-example-model-warmup", daemon=True).start()


def _translate(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    direction = str(payload.get("direction") or "auto").strip() or "auto"
    result = local_translate_text(text, direction=direction)
    if not result.get("ok"):
        return result
    return result


def _translate_batch(payload: dict[str, Any]) -> dict[str, Any]:
    texts = payload.get("texts")
    if not isinstance(texts, list):
        return {"ok": False, "error": "texts must be a list"}
    direction = str(payload.get("direction") or "auto").strip() or "auto"
    result = local_translate_batch_texts([str(x) for x in texts], direction=direction)
    if not result.get("ok"):
        return result
    return result


def _card_response(card: dict[str, Any], source: str | None = None, model: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "word": str(card.get("word") or card.get("base_word") or "").strip(),
        "phonetic": str(card.get("phonetic") or "-").strip() or "-",
        "part_of_speech": str(card.get("part_of_speech") or "-").strip() or "-",
        "meaning_cn": str(card.get("meaning_cn") or "").strip(),
        "example": str(card.get("example") or "").strip(),
        "example_cn": str(card.get("example_cn") or "").strip(),
        "source": source or str(card.get("source") or "english_vocab_completion_cache"),
        "model": model or str(card.get("model") or SERVICE_MODEL),
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?", "", clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r"```$", "", clean).strip()
    try:
        value = json.loads(clean)
    except Exception:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(clean[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model returned non-object JSON")
    return value


def _scene_label(book_id: str) -> str:
    return {
        "daily_life_ngsl": "daily spoken life",
        "workplace_business": "workplace communication",
        "computer_science": "software engineering and computer usage",
        "ielts_academic": "IELTS academic writing",
        "toefl_academic": "TOEFL campus study",
    }.get(book_id, "natural modern English")


def _dashscope_final_card(word: str, book_id: str, definition: dict[str, Any] | None) -> dict[str, Any]:
    started = time.time()
    active_region = _first_env(["JACHIN_ACTIVE_REGION", "QWEN_REGION"], "CN").strip().upper()
    if active_region == "SEA":
        api_key = _first_env(["DASHSCOPE_API_KEY_SEA", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"])
        api_base = _first_env(
            ["JACHIN_ENGLISH_VOCAB_API_BASE", "DASHSCOPE_API_BASE_SEA", "DASHSCOPE_API_BASE"],
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
    else:
        api_key = _first_env(["DASHSCOPE_API_KEY_CN", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"])
        api_base = _first_env(
            ["JACHIN_ENGLISH_VOCAB_API_BASE", "DASHSCOPE_API_BASE_CN", "DASHSCOPE_API_BASE"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    if not api_key:
        return {"ok": False, "error": "DashScope API key not found", "source": "remote_qwen_turbo_missing_key", "model": SERVICE_MODEL}
    model = _first_env(["JACHIN_ENGLISH_VOCAB_MODEL"], "qwen-turbo").removeprefix("dashscope/") or "qwen-turbo"
    timeout_ms = max(1500, min(12000, _env_int("JACHIN_ENGLISH_VOCAB_REMOTE_TIMEOUT_MS", 4500)))
    meaning_hint = str((definition or {}).get("meaning_cn") or "").strip()
    part_hint = str((definition or {}).get("part_of_speech") or "").strip()
    phonetic_hint = str((definition or {}).get("phonetic") or "").strip()
    prompt = (
        "Return strict compact JSON only with keys: word, phonetic, part_of_speech, meaning_cn, example, example_cn.\n"
        f"Target word: {word}\n"
        f"Chinese meaning hint: {meaning_hint}\n"
        f"Part of speech hint: {part_hint}\n"
        f"Phonetic hint: {phonetic_hint}\n"
        f"Scene: {_scene_label(book_id)}\n"
        "Generate one natural, scene-appropriate English sentence, 6-16 words, using the exact target word. "
        "No memorization wording, no generic template wording, no strange collocation. "
        "Translate the whole sentence to Simplified Chinese."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise English vocabulary tutor. Return JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.35,
        "max_tokens": 180,
    }
    url = f"{api_base.rstrip('/')}/chat/completions"
    _service_log(
        "remote_qwen_http_send",
        word=word,
        book_id=book_id,
        model=model,
        timeout_ms=timeout_ms,
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        _service_log(
            "remote_qwen_http_error",
            word=word,
            book_id=book_id,
            model=model,
            error=str(exc),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return {"ok": False, "error": f"DashScope request failed: {exc}", "source": "remote_qwen_turbo_error", "model": model}
    _service_log(
        "remote_qwen_http_status",
        word=word,
        book_id=book_id,
        model=model,
        status=status,
        elapsed_ms=int((time.time() - started) * 1000),
    )
    if status < 200 or status >= 300:
        return {"ok": False, "error": f"DashScope HTTP {status}", "source": "remote_qwen_turbo_error", "model": model}
    data = json.loads(raw)
    content = str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    parsed = _extract_json_object(content)
    base_word = normalize_word(word)
    card = {
        "ok": True,
        "word": str(parsed.get("word") or base_word or word).strip().lower(),
        "base_word": base_word or word.lower(),
        "phonetic": str(parsed.get("phonetic") or phonetic_hint or "-").strip() or "-",
        "part_of_speech": str(parsed.get("part_of_speech") or part_hint or "-").strip() or "-",
        "meaning_cn": str(parsed.get("meaning_cn") or meaning_hint).strip(),
        "example": str(parsed.get("example") or "").strip(),
        "example_cn": str(parsed.get("example_cn") or "").strip(),
        "source": "dashscope_qwen_turbo",
        "model": model,
    }
    if not _is_complete_remote_card(card):
        _service_log(
            "remote_qwen_card_rejected",
            word=word,
            book_id=book_id,
            model=model,
            example=card["example"],
            example_cn=card["example_cn"],
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return {"ok": False, "error": "qwen-turbo returned incomplete card", "source": "remote_qwen_turbo_rejected", "model": model}
    _cache_put_card(book_id, base_word or word, card)
    _service_log(
        "remote_qwen_card_returned",
        word=word,
        book_id=book_id,
        model=model,
        example=card["example"],
        example_cn=card["example_cn"],
        elapsed_ms=int((time.time() - started) * 1000),
    )
    return _card_response(card, source="dashscope_qwen_turbo", model=model)


def _card_is_foreground_quality_cache(card: dict[str, Any]) -> bool:
    return _is_complete_remote_card(card)


def _complete_example(word: str, book_id: str, meaning_cn: str, existing_example: str = "") -> tuple[str, str, str, str]:
    example = (existing_example or "").strip()
    model = SERVICE_MODEL
    source = "local_dictionary"
    if _looks_like_placeholder_example(example, word) or _looks_like_low_diversity_example(example, word):
        generated = _generate_example(word, book_id, meaning_cn)
        if _generated_example_is_acceptable(generated, word):
            example = str(generated.get("example") or "").strip()
            model = str(generated.get("model_id") or SERVICE_MODEL)
            source = str(generated.get("source") or "local_gguf")
    example_cn = str(locals().get("generated", {}).get("example_cn") or "").strip() if example else ""
    if example and not example_cn:
        example_cn = _translate_sentence(example)
    return example, example_cn, source, model


def _lookup(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.time()
    raw_word = str(payload.get("word") or "").strip()
    if not raw_word:
        return {"ok": False, "error": "word is empty"}
    book_id = str(payload.get("book_id") or "daily_life_ngsl").strip() or "daily_life_ngsl"
    context = str(payload.get("context_sentence") or "").strip()
    require_final_example = bool(payload.get("require_final_example")) and not context
    cache_only = bool(payload.get("cache_only")) and not context
    base_word = normalize_word(raw_word)
    _service_log(
        "lookup_start",
        word=raw_word,
        base_word=base_word,
        book_id=book_id,
        has_context=bool(context),
        require_final_example=require_final_example,
        cache_only=cache_only,
    )

    cached_card = _cache_get_card(book_id, base_word)
    if cached_card and (_is_complete_card(cached_card) or _card_is_foreground_quality_cache(cached_card)):
        if require_final_example and not _card_is_foreground_quality_cache(cached_card):
            _service_log(
                "lookup_cache_rejected_not_foreground_quality",
                word=raw_word,
                base_word=base_word,
                book_id=book_id,
                source=str(cached_card.get("source") or ""),
                model=str(cached_card.get("model") or ""),
                example=str(cached_card.get("example") or ""),
                elapsed_ms=int((time.time() - started) * 1000),
            )
            _cache_delete_card(book_id, base_word)
        else:
            _service_log(
                "lookup_cache_hit",
                word=raw_word,
                base_word=base_word,
                book_id=book_id,
                has_context=bool(context),
                source=str(cached_card.get("source") or ""),
                model=str(cached_card.get("model") or ""),
                example=str(cached_card.get("example") or ""),
                example_cn=str(cached_card.get("example_cn") or ""),
                elapsed_ms=int((time.time() - started) * 1000),
            )
            if context:
                scoped = {**cached_card, "example": context, "example_cn": _cache_get_sentence(context) or _translate_sentence(context)}
                if _is_complete_card(scoped):
                    return _finalize_lookup(
                        _card_response(scoped, source="english_vocab_completion_cache_context"),
                        word=base_word,
                        book_id=book_id,
                    )
            else:
                return _finalize_lookup(
                    _card_response(cached_card, source="english_vocab_completion_cache"),
                    word=base_word,
                    book_id=book_id,
                )
    elif cached_card:
        _service_log(
            "lookup_cache_incomplete",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            elapsed_ms=int((time.time() - started) * 1000),
        )
    if cache_only:
        _service_log(
            "lookup_cache_only_miss",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return {
            "ok": False,
            "error": "final example cache miss",
            "word": base_word or raw_word.lower(),
            "source": "final_example_cache_miss",
            "model": SERVICE_MODEL,
        }
    if require_final_example:
        definition = lookup_word(raw_word)
        result = _dashscope_final_card(base_word or raw_word.lower(), book_id, definition)
        if result.get("ok"):
            return _finalize_lookup(result, word=base_word, book_id=book_id)
        _service_log(
            "lookup_final_example_remote_failed",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            error=str(result.get("error") or ""),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return {
            "ok": False,
            "error": str(result.get("error") or "final example generation failed"),
            "word": base_word or raw_word.lower(),
            "source": str(result.get("source") or "remote_qwen_turbo_failed"),
            "model": str(result.get("model") or SERVICE_MODEL),
        }

    definition = lookup_word(raw_word)

    if not definition or not str(definition.get("meaning_cn") or "").strip():
        _service_log(
            "lookup_dictionary_miss",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            elapsed_ms=int((time.time() - started) * 1000),
        )
        meaning = _fallback_meaning(raw_word, base_word, definition, cached_card)
        if context:
            example_cn = _cache_get_sentence(context) or _translate_sentence(context)
            result = {
                "ok": True,
                "word": base_word or raw_word.lower(),
                "phonetic": str((definition or {}).get("phonetic") or "-").strip() or "-",
                "part_of_speech": str((definition or {}).get("part_of_speech") or "-").strip() or "-",
                "meaning_cn": meaning,
                "example": context,
                "example_cn": example_cn,
                "source": "local_translate_context",
                "model": SERVICE_MODEL,
            }
            _service_log(
                "lookup_return_context_missing_definition",
                word=raw_word,
                base_word=base_word,
                book_id=book_id,
                elapsed_ms=int((time.time() - started) * 1000),
            )
            return _finalize_lookup(result, word=base_word, book_id=book_id)
        if cached_card and _is_complete_card(cached_card, require_example=False):
            if context:
                example_cn = _cache_get_sentence(context) or _translate_sentence(context)
                scoped = {**cached_card, "example": context, "example_cn": example_cn}
                if _is_complete_card(scoped):
                    return _finalize_lookup(
                        _card_response(scoped, source="english_vocab_completion_cache_context"),
                        word=base_word,
                        book_id=book_id,
                    )
            example, example_cn, source, model = _complete_example(base_word, book_id, meaning)
            completed = {
                **cached_card,
                "word": raw_word.lower(),
                "base_word": base_word,
                "meaning_cn": meaning,
                "example": example,
                "example_cn": example_cn,
                "source": source,
                "model": model,
            }
            if _is_complete_card(completed):
                _cache_put_card(book_id, base_word, completed)
                return _finalize_lookup(
                    _card_response(completed, source="english_vocab_completion_cache_completed"),
                    word=base_word,
                    book_id=book_id,
                )

        generated = _generate_example(base_word, book_id, meaning)
        if not _generated_example_is_acceptable(generated, base_word):
            _service_log(
                "lookup_generate_failed",
                word=raw_word,
                base_word=base_word,
                book_id=book_id,
                error=str(generated.get("error") or "unknown"),
                elapsed_ms=int((time.time() - started) * 1000),
            )
            return {
                "ok": False,
                "error": "example generation is not ready",
                "word": base_word or raw_word.lower(),
                "meaning_cn": meaning,
                "source": "example_not_ready",
                "model": SERVICE_MODEL,
            }
        example = str(generated.get("example") or "").strip()
        example_cn = str(generated.get("example_cn") or "").strip() or _translate_sentence(example)
        if not example_cn:
            return {
                "ok": False,
                "error": "example translation is not ready",
                "word": base_word or raw_word.lower(),
                "meaning_cn": meaning,
                "source": "example_translation_not_ready",
                "model": SERVICE_MODEL,
            }
        incomplete = {
            "ok": True,
            "word": base_word or raw_word.lower(),
            "phonetic": "-",
            "part_of_speech": "-",
            "meaning_cn": meaning,
            "example": example,
            "example_cn": example_cn,
            "source": str(generated.get("source") or "local_scene_fallback_missing_definition"),
            "model": str(generated.get("model_id") or SERVICE_MODEL),
        }
        if _is_complete_card(incomplete):
            _cache_put_card(book_id, base_word, incomplete)
        _service_log(
            "lookup_return_generated_missing_definition",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            source=str(incomplete.get("source") or ""),
            elapsed_ms=int((time.time() - started) * 1000),
        )
        return _finalize_lookup(incomplete, word=base_word, book_id=book_id)

    _service_log(
        "lookup_dictionary_hit",
        word=raw_word,
        base_word=base_word,
        book_id=book_id,
        source=str(definition.get("source") or "local_dictionary"),
        elapsed_ms=int((time.time() - started) * 1000),
    )
    definition_word = str(definition.get("base_word") or base_word)
    meaning_for_generation = str(definition.get("meaning_cn") or "").strip()
    generated_example_cn = ""
    generated_source = ""
    generated_model = ""
    pack_generator = ""
    packed_example = None if context or require_final_example else lookup_example_pack(definition_word, book_id)
    if packed_example and packed_example.get("ok"):
        example = str(packed_example.get("example") or "").strip()
        generated_example_cn = str(packed_example.get("example_cn") or "").strip()
        generated_source = "english_example_pack"
        generated_model = str(packed_example.get("model_id") or "english_example_pack_v1")
        pack_generator = str(packed_example.get("pack_generator") or "")
        _quality_log(
            "lookup_example_pack_hit",
            word=definition_word,
            book_id=book_id,
            scene=str(packed_example.get("scene") or ""),
            pack_generator=pack_generator,
            quality_total=packed_example.get("quality"),
        )
        _example_chain_log(
            "example_pack_hit",
            layer="python_service",
            word=definition_word,
            book_id=book_id,
            source="english_example_pack",
            model=generated_model,
            pack_generator=pack_generator,
            scene=str(packed_example.get("scene") or ""),
            example=example,
            example_cn=generated_example_cn,
            require_final_example=require_final_example,
        )
    else:
        example = context or str(definition.get("example") or "").strip()
    if not context and (
        require_final_example
        or (not packed_example and (not example or _looks_like_low_diversity_example(example, definition_word)))
    ):
        generated = _generate_example(
            definition_word,
            book_id,
            meaning_for_generation,
        )
        if _generated_example_is_acceptable(generated, definition_word):
            example = str(generated.get("example") or "").strip()
            generated_example_cn = str(generated.get("example_cn") or "").strip()
            generated_source = str(generated.get("source") or "local_fast_template")
            generated_model = str(generated.get("model_id") or SERVICE_MODEL)
    if not example:
        return {
            "ok": False,
            "error": "example generation is not ready",
            "word": str(definition.get("word") or raw_word.lower()),
            "meaning_cn": str(definition.get("meaning_cn") or "").strip(),
            "source": "example_not_ready",
            "model": SERVICE_MODEL,
        }
    if context:
        # Token-click lookup must return the word meaning immediately. Translating
        # the whole context sentence is optional, but the local OPUS model is fast
        # after warmup and makes the token popover feel complete.
        example_cn = _cache_get_sentence(context) or _translate_sentence(context)
        source = "local_dictionary_context"
    else:
        example_cn = generated_example_cn or str(definition.get("example_cn") or "").strip()
        source = generated_source or "local_dictionary"
    if example and not example_cn and not context:
        example_cn = _translate_sentence(example)
    if not example_cn and example and not context:
        return {
            "ok": False,
            "error": "example translation is not ready",
            "word": str(definition.get("word") or raw_word.lower()),
            "meaning_cn": str(definition.get("meaning_cn") or "").strip(),
            "source": "example_translation_not_ready",
            "model": SERVICE_MODEL,
        }

    result = {
        "ok": True,
        "word": str(definition.get("word") or raw_word.lower()),
        "phonetic": str(definition.get("phonetic") or "-"),
        "part_of_speech": str(definition.get("part_of_speech") or "-"),
        "meaning_cn": str(definition.get("meaning_cn") or "").strip(),
        "example": example,
        "example_cn": example_cn,
        "source": source,
        "model": str(generated_model or definition.get("model") or SERVICE_MODEL),
    }
    cache_card = {
        **result,
        "base_word": str(definition.get("base_word") or base_word),
    }
    temporary_pack = bool(
        not context
        and source == "english_example_pack"
        and pack_generator
        and not _pack_generator_is_ai_reviewed(pack_generator)
    )
    if temporary_pack and require_final_example:
        # Foreground card policy:
        #   1. return high-quality completion cache immediately;
        #   2. otherwise let Rust call qwen-turbo as the real-time fallback;
        #   3. keep local GGUF only for background/batch enrichment.
        # This avoids blocking the UI on 0.5B generation and prevents weak local
        # examples from being shown as final answers.
        _service_log(
            "lookup_final_example_defer_to_qwen_turbo",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            pack_example=example,
            pack_generator=pack_generator,
            elapsed_ms=int((time.time() - started) * 1000),
        )

    if temporary_pack:
        result["refresh_hint"] = "background_ai_refresh"
        if require_final_example:
            _schedule_ai_example_refresh(
                definition_word,
                book_id,
                meaning_for_generation,
                str(definition.get("base_word") or base_word),
                cache_card,
            )
            return {
                "ok": False,
                "error": "final example is still preparing",
                "word": str(definition.get("word") or raw_word.lower()),
                "phonetic": str(definition.get("phonetic") or "-"),
                "part_of_speech": str(definition.get("part_of_speech") or "-"),
                "meaning_cn": str(definition.get("meaning_cn") or "").strip(),
                "source": "final_example_not_ready",
                "model": SERVICE_MODEL,
                "refresh_hint": "background_ai_refresh",
            }
    if temporary_pack:
        _schedule_ai_example_refresh(
            definition_word,
            book_id,
            meaning_for_generation,
            str(definition.get("base_word") or base_word),
            cache_card,
        )
        _service_log(
            "lookup_temporary_pack_background_refresh_queued",
            word=raw_word,
            base_word=base_word,
            book_id=book_id,
            pack_generator=pack_generator,
            elapsed_ms=int((time.time() - started) * 1000),
        )
    if not context and not temporary_pack and _is_complete_card(cache_card):
        _cache_put_card(book_id, base_word, cache_card)
    _service_log(
        "lookup_return_dictionary",
        word=raw_word,
        base_word=base_word,
        book_id=book_id,
        source=source,
        model=str(result.get("model") or ""),
        example=str(result.get("example") or ""),
        example_cn=str(result.get("example_cn") or ""),
        refresh_hint=str(result.get("refresh_hint") or ""),
        elapsed_ms=int((time.time() - started) * 1000),
    )
    return _finalize_lookup(result, word=base_word, book_id=book_id)


def _cache_card(payload: dict[str, Any]) -> dict[str, Any]:
    word = str(payload.get("word") or "").strip()
    if not word:
        return {"ok": False, "error": "word is empty"}
    book_id = str(payload.get("book_id") or "daily_life_ngsl").strip() or "daily_life_ngsl"
    card = {
        "word": word.lower(),
        "base_word": str(payload.get("base_word") or normalize_word(word)),
        "phonetic": str(payload.get("phonetic") or "-").strip() or "-",
        "part_of_speech": str(payload.get("part_of_speech") or "-").strip() or "-",
        "meaning_cn": str(payload.get("meaning_cn") or "").strip(),
        "example": str(payload.get("example") or "").strip(),
        "example_cn": str(payload.get("example_cn") or "").strip(),
        "source": str(payload.get("source") or "external_completion").strip() or "external_completion",
        "model": str(payload.get("model") or SERVICE_MODEL).strip() or SERVICE_MODEL,
    }
    if not (_is_complete_card(card) or _is_complete_remote_card(card)):
        return {"ok": False, "error": "card is incomplete"}
    _cache_put_card(book_id, card["base_word"], card)
    return {
        "ok": True,
        "word": card["word"],
        "base_word": card["base_word"],
        "cache_path": str(_completion_cache_path()),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "JachinEnglishVocabService/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("JACHIN_ENGLISH_VOCAB_SERVICE_VERBOSE") == "1":
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._send(200, _json_response(True, _status()))
            return
        self._send(404, _json_response(False, error="not found"))

    def do_POST(self) -> None:
        started = time.time()
        path = self.path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(raw or "{}")
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")

            _service_log(
                "http_request_start",
                path=path,
                word=str(payload.get("word") or ""),
                book_id=str(payload.get("book_id") or ""),
            )
            if path == "/warmup":
                result = _warmup(payload)
            elif path == "/translate":
                result = _translate(payload)
            elif path == "/translate-batch":
                result = _translate_batch(payload)
            elif path == "/lookup":
                result = _lookup(payload)
            elif path == "/cache-card":
                result = _cache_card(payload)
            elif path == "/example":
                result = _generate_example(
                    str(payload.get("word") or ""),
                    str(payload.get("book_id") or "daily_life_ngsl"),
                    str(payload.get("meaning_cn") or ""),
                )
            else:
                self._send(404, _json_response(False, error="not found"))
                return
            _service_log(
                "http_request_end",
                path=path,
                ok=bool(result.get("ok")),
                source=str(result.get("source") or ""),
                elapsed_ms=int((time.time() - started) * 1000),
            )
            self._send(200 if result.get("ok") else 422, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:
            _service_log(
                "http_request_error",
                path=path,
                error=str(exc),
                elapsed_ms=int((time.time() - started) * 1000),
            )
            self._send(500, _json_response(False, error=str(exc)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("JACHIN_ENGLISH_VOCAB_SERVICE_PORT") or "18987"))
    parser.add_argument("--warmup", action="store_true")
    args = parser.parse_args()

    if args.warmup:
        try:
            _warmup({"direction": "en-zh"})
        except Exception:
            # The HTTP service should still come up so Rust can report a precise error later.
            pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"ok": True, "service": SERVICE_MODEL, "url": f"http://{args.host}:{args.port}"}, ensure_ascii=False), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
