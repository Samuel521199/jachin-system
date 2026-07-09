from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9 ._-]{0,80}")


@dataclass(frozen=True)
class HotwordSnapshot:
    words: dict[str, int]
    sources: list[str]

    @property
    def count(self) -> int:
        return len(self.words)


def _clean_word(raw: Any) -> str:
    word = str(raw or "").strip()
    word = re.sub(r"\s+", " ", word)
    if not word or not _WORD_RE.fullmatch(word):
        return ""
    return word[:80]


def _add_word(out: dict[str, int], raw: Any, weight: int) -> None:
    word = _clean_word(raw)
    if not word:
        return
    out[word] = max(out.get(word, 0), max(1, min(100, int(weight or 1))))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_router_hotwords() -> tuple[dict[str, int], str | None]:
    try:
        from l3_node.voice_entity_correction import export_hotwords

        words = export_hotwords()
        return {str(k): int(v) for k, v in words.items()}, "l3_node.voice_entity_correction"
    except Exception:
        return {}, None


def _iter_json_word_items(data: Any) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    if isinstance(data, dict):
        section_names = {"apps", "contacts", "projects", "hotwords"}
        for key, value in data.items():
            if key in section_names and isinstance(value, dict | list | tuple):
                items.extend(_iter_json_word_items(value))
                continue
            if isinstance(value, int | float | str):
                try:
                    items.append((str(key), int(value)))
                except ValueError:
                    items.append((str(key), 20))
            elif isinstance(value, list | tuple):
                items.append((str(key), 20))
                for alias in value:
                    items.append((str(alias), 10))
            elif isinstance(value, dict):
                canonical = value.get("canonical") or key
                weight = int(value.get("weight") or 20)
                items.append((str(canonical), weight))
                for alias in value.get("aliases") or []:
                    items.append((str(alias), max(5, weight - 10)))
                for alias in value.get("phonetic_aliases") or []:
                    items.append((str(alias), max(5, weight - 12)))
    elif isinstance(data, list | tuple):
        for item in data:
            if isinstance(item, str):
                items.append((item, 20))
            elif isinstance(item, dict):
                word = item.get("word") or item.get("canonical") or item.get("name")
                weight = int(item.get("weight") or 20)
                items.append((str(word or ""), weight))
                for alias in item.get("aliases") or []:
                    items.append((str(alias), max(5, weight - 10)))
    return items


def _load_json_hotwords(path: Path) -> tuple[dict[str, int], str | None]:
    if not path.is_file():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, None
    out: dict[str, int] = {}
    for word, weight in _iter_json_word_items(data):
        _add_word(out, word, weight)
    return out, str(path)


def _load_text_hotwords(path: Path) -> tuple[dict[str, int], str | None]:
    if not path.is_file():
        return {}, None
    out: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return {}, None
    for line in lines:
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if ":" in item:
            word, weight = item.rsplit(":", 1)
            try:
                value = int(float(weight.strip()))
            except ValueError:
                value = 20
        else:
            word, value = item, 20
        _add_word(out, word.strip(), value)
    return out, str(path)


def _load_env_hotwords() -> tuple[dict[str, int], str | None]:
    raw = os.getenv("JACHIN_STT_HOTWORDS", "").strip()
    if not raw:
        return {}, None
    out: dict[str, int] = {}
    for part in re.split(r"[,;\n]+", raw):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            word, weight = item.rsplit(":", 1)
            try:
                value = int(weight)
            except ValueError:
                value = 20
            _add_word(out, word, value)
        else:
            _add_word(out, item, 20)
    return out, "env:JACHIN_STT_HOTWORDS"


class SttHotwordProvider:
    def __init__(self, extra_paths: list[Path] | None = None) -> None:
        root = _repo_root()
        default_paths = [
            root / "data" / "voice" / "sherpa_hotwords.txt",
            root / "data" / "voice" / "domain_lexicon.json",
            root / "data" / "voice" / "stt_hotwords.json",
            root / "config" / "voice_domain_lexicon.json",
        ]
        self.paths = [*(extra_paths or []), *default_paths]

    def snapshot(self) -> HotwordSnapshot:
        words: dict[str, int] = {}
        sources: list[str] = []

        router_words, router_source = _load_router_hotwords()
        for word, weight in router_words.items():
            _add_word(words, word, weight)
        if router_source and router_words:
            sources.append(router_source)

        for path in self.paths:
            if path.suffix.lower() == ".txt":
                path_words, source = _load_text_hotwords(path)
            else:
                path_words, source = _load_json_hotwords(path)
            for word, weight in path_words.items():
                _add_word(words, word, weight)
            if source and path_words:
                sources.append(source)

        env_words, env_source = _load_env_hotwords()
        for word, weight in env_words.items():
            _add_word(words, word, weight)
        if env_source and env_words:
            sources.append(env_source)

        return HotwordSnapshot(words=words, sources=sources)
