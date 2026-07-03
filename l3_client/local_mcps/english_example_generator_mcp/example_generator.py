from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_ID = "com.jachin.model.qwen2-5-0-5b-instruct-gguf-q4-k-m"
MODEL_FILE = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"


def _home() -> Path:
    raw = os.environ.get("JACHIN_HOME")
    if raw:
        return Path(raw)
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".") / ".jachin"


def _model_path() -> Path:
    installed = _home() / "models" / MODEL_ID / "model" / MODEL_FILE
    if installed.is_file():
        return installed
    repo_root = Path(__file__).resolve().parents[3]
    dev_model = repo_root / "models_repo" / MODEL_ID / "model" / MODEL_FILE
    if dev_model.is_file():
        return dev_model
    return installed


def _cache_path() -> Path:
    return _home() / "data" / "english_vocab" / "example_generator_cache.json"


def _cache_key(word: str, book_id: str | None) -> str:
    return f"{book_id or 'daily_life_ngsl'}:{word.strip().lower()}"


def _read_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _scene(book_id: str | None) -> str:
    if book_id == "daily_life_ngsl":
        return "daily spoken life: meals, shopping, travel, home, weather, health"
    if book_id == "workplace":
        return "workplace communication: meetings, reports, feedback, deadlines, collaboration"
    if book_id == "computer_science":
        return "software engineering: coding, debugging, deployment, systems, data"
    if book_id == "ielts_academic":
        return "IELTS academic writing: society, education, environment, economy"
    if book_id == "toefl_academic":
        return "TOEFL campus and lecture scenarios: classes, research, labs, assignments"
    return "natural modern English"


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _clean_sentence(text: str, word: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip().strip('"'))
    if not clean:
        raise ValueError("model returned an empty example")
    if clean[-1] not in ".!?":
        clean += "."
    return clean


def _hash_pick(items: list[str], seed: str) -> str:
    if not items:
        return ""
    total = sum(ord(ch) for ch in seed)
    return items[total % len(items)]


def _scene_fallback_example(word: str, book_id: str | None) -> str:
    clean_word = (word or "").strip().lower() or "topic"
    specific = {
        "bread": "She bought fresh bread from the bakery.",
        "breakfast": "He made breakfast before the early meeting.",
        "lunch": "They had lunch near the office.",
        "dinner": "The family cooked dinner together tonight.",
        "message": "I sent her a message after the meeting.",
        "budget": "We adjusted the budget before approving the plan.",
        "borrow": "Can I borrow your charger for an hour?",
        "deadline": "The deadline moved to Friday afternoon.",
        "feedback": "Her feedback helped us improve the design.",
        "agenda": "Please share the agenda before the meeting.",
        "neighbor": "Our neighbor helped carry the heavy boxes upstairs.",
    }
    if clean_word in specific:
        return specific[clean_word]

    if book_id == "workplace":
        patterns = [
            "The team discussed {word} during the morning meeting.",
            "She added {word} to the project update.",
            "We reviewed {word} before sending the report.",
            "His notes explained {word} clearly for everyone.",
        ]
    elif book_id == "computer_science":
        patterns = [
            "The engineer checked {word} before deploying the service.",
            "This module uses {word} to keep the system stable.",
            "The log showed {word} during the debugging session.",
            "We optimized {word} after reviewing the code.",
        ]
    elif book_id == "ielts_academic":
        patterns = [
            "The essay explains how {word} affects modern society.",
            "Researchers often discuss {word} in public policy debates.",
            "This paragraph connects {word} with long-term change.",
            "The evidence shows why {word} matters today.",
        ]
    elif book_id == "toefl_academic":
        patterns = [
            "The professor mentioned {word} during the lecture.",
            "Students compared {word} in their class discussion.",
            "The reading passage described {word} in detail.",
            "She wrote about {word} in her assignment.",
        ]
    else:
        patterns = [
            "She noticed {word} while preparing for the day.",
            "We talked about {word} at home last night.",
            "He used {word} while making a simple plan.",
            "They found {word} during their weekend errands.",
        ]
    return _hash_pick(patterns, clean_word).format(word=clean_word)


def _looks_like_template_fallback(example: str, word: str) -> bool:
    text = re.sub(r"\s+", " ", (example or "").strip().lower())
    clean_word = (word or "").strip().lower()
    generic_fragments = [
        "while preparing for the day",
        "at home last night",
        "while making a simple plan",
        "during their weekend errands",
        "came up in a normal conversation",
        "want to remember",
        "learn the word",
    ]
    if any(fragment in text for fragment in generic_fragments):
        return True
    return text in {
        _scene_fallback_example(clean_word, "daily_life_ngsl").lower(),
        _scene_fallback_example(clean_word, "workplace").lower(),
        _scene_fallback_example(clean_word, "computer_science").lower(),
        _scene_fallback_example(clean_word, "ielts_academic").lower(),
        _scene_fallback_example(clean_word, "toefl_academic").lower(),
    }


def english_example_model_status() -> dict[str, Any]:
    model_path = _model_path()
    try:
        import llama_cpp  # noqa: F401

        runtime_ready = True
        runtime_error = None
    except Exception as exc:
        runtime_ready = False
        runtime_error = str(exc)
    return {
        "ok": True,
        "model_id": MODEL_ID,
        "model_path": str(model_path),
        "model_installed": model_path.is_file(),
        "runtime_ready": runtime_ready,
        "runtime_error": runtime_error,
    }


@lru_cache(maxsize=1)
def _llm():
    from llama_cpp import Llama

    model_path = _model_path()
    if not model_path.is_file():
        raise RuntimeError(f"Local example model is not installed: {model_path}")
    return Llama(
        model_path=str(model_path),
        n_ctx=768,
        n_threads=max(2, min(8, os.cpu_count() or 4)),
        n_gpu_layers=0,
        verbose=False,
    )


def english_generate_example_card(word: str, book_id: str = "daily_life_ngsl", meaning_cn: str = "") -> dict[str, Any]:
    clean_word = (word or "").strip().lower()
    if not clean_word:
        return {"ok": False, "error": "word is empty"}
    key = _cache_key(clean_word, book_id)
    cache = _read_cache()
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("example"):
        cached_model = str(cached.get("model_id") or "")
        cached_example = str(cached.get("example") or "")
        if cached_model == "local_scene_generator" or _looks_like_template_fallback(cached_example, clean_word):
            cache.pop(key, None)
            _write_cache(cache)
        else:
            return {
                "ok": True,
                "word": clean_word,
                "book_id": book_id,
                "example": str(cached.get("example")),
                "model_id": str(cached.get("model_id") or MODEL_ID),
                "source": "local_gguf_cache",
            }

    status = english_example_model_status()
    if not status.get("model_installed") or not status.get("runtime_ready"):
        return {
            "ok": False,
            "word": clean_word,
            "book_id": book_id,
            "error": "local example model is not ready",
            "model_id": MODEL_ID,
            "source": "local_gguf_unavailable",
            "runtime_ready": status.get("runtime_ready"),
            "runtime_error": status.get("runtime_error"),
        }

    prompt = (
        "<|im_start|>system\n"
        "You create concise English vocabulary cards. Return strict JSON only.\n"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Word: {clean_word}\n"
        f"Chinese meaning hint: {meaning_cn}\n"
        f"Scene: {_scene(book_id)}\n"
        "Generate one natural English example sentence, 6-14 words, using the exact word. "
        "Do not write memory-learning sentences. "
        "Return JSON: {\"example\":\"...\"}\n"
        "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    result = _llm()(
        prompt,
        max_tokens=64,
        temperature=0.35,
        top_p=0.85,
        repeat_penalty=1.12,
        stop=["<|im_end|>", "\n\n"],
    )
    raw = result["choices"][0]["text"]
    try:
        parsed = _extract_json(raw)
        example = _clean_sentence(str(parsed.get("example") or ""), clean_word)
    except Exception:
        example = _clean_sentence(raw, clean_word)
    cache[key] = {
        "example": example,
        "model_id": MODEL_ID,
    }
    _write_cache(cache)
    return {
        "ok": True,
        "word": clean_word,
        "book_id": book_id,
        "example": example,
        "model_id": MODEL_ID,
        "source": "local_gguf",
    }
