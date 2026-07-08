from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


PACK_VERSION = 1
PACK_FILE = "english_example_pack.json"
_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")


def normalize_pack_word(raw: str) -> str:
    return re.sub(r"^[^A-Za-z']+|[^A-Za-z']+$", "", raw or "").lower()


def _pack_path() -> Path:
    return Path(__file__).resolve().with_name(PACK_FILE)


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _valid_item(item: dict[str, Any], word: str) -> bool:
    example = str(item.get("example") or "").strip()
    example_cn = str(item.get("example_cn") or "").strip()
    if not example or not example_cn:
        return False
    clean = normalize_pack_word(word)
    if clean and clean not in _words(example):
        return False
    if str(item.get("review_status") or "") != "approved":
        return False
    try:
        return float(item.get("quality_score") or 0.0) >= 0.82
    except Exception:
        return False


@lru_cache(maxsize=1)
def load_example_pack() -> dict[str, Any]:
    path = _pack_path()
    if not path.is_file():
        return {"version": PACK_VERSION, "items": {}, "counts": {"words": 0, "examples": 0}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"version": PACK_VERSION, "items": {}, "counts": {"words": 0, "examples": 0}}
    if not isinstance(raw, dict):
        return {"version": PACK_VERSION, "items": {}, "counts": {"words": 0, "examples": 0}}
    items = raw.get("items")
    if not isinstance(items, dict):
        raw["items"] = {}
    raw.setdefault("version", PACK_VERSION)
    raw.setdefault("counts", {"words": len(raw["items"]), "examples": 0})
    return raw


def example_pack_status() -> dict[str, Any]:
    pack = load_example_pack()
    items = pack.get("items") if isinstance(pack.get("items"), dict) else {}
    examples = sum(len(v) for v in items.values() if isinstance(v, list))
    return {
        "ok": True,
        "path": str(_pack_path()),
        "installed": _pack_path().is_file(),
        "version": pack.get("version"),
        "words": len(items),
        "examples": examples,
        "source": (pack.get("source") or {}) if isinstance(pack.get("source"), dict) else {},
    }


def lookup_example_pack(word: str, book_id: str = "daily_life_ngsl") -> dict[str, Any] | None:
    clean = normalize_pack_word(word)
    if not clean:
        return None
    pack = load_example_pack()
    items = pack.get("items") if isinstance(pack.get("items"), dict) else {}
    variants = items.get(clean)
    if not isinstance(variants, list) or not variants:
        return None
    book = (book_id or "").strip()
    preferred = [item for item in variants if isinstance(item, dict) and str(item.get("book_id") or "") == book]
    fallback = [item for item in variants if isinstance(item, dict) and str(item.get("book_id") or "") != book]
    candidates = preferred + fallback
    approved = [item for item in candidates if _valid_item(item, clean)]
    if not approved:
        return None
    seed = sum(ord(ch) for ch in f"{book}:{clean}") % len(approved)
    item = dict(approved[seed])
    return {
        "ok": True,
        "word": clean,
        "book_id": str(item.get("book_id") or book),
        "example": str(item.get("example") or "").strip(),
        "example_cn": str(item.get("example_cn") or "").strip(),
        "model_id": str(item.get("model_id") or "english_example_pack_v1"),
        "source": "english_example_pack",
        "pack_generator": str(item.get("generator") or ""),
        "pack_source": str(item.get("source") or ""),
        "template_id": str(item.get("id") or ""),
        "quality": {
            "total": float(item.get("quality_score") or 0.0),
            "grammar": float(item.get("grammar_score") or item.get("quality_score") or 0.0),
            "naturalness": float(item.get("naturalness_score") or item.get("quality_score") or 0.0),
            "semantic": float(item.get("semantic_score") or item.get("quality_score") or 0.0),
        },
        "level": str(item.get("level") or ""),
        "scene": str(item.get("scene") or ""),
        "review_status": str(item.get("review_status") or ""),
    }
