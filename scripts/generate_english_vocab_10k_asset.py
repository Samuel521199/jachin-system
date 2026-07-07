from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORD_LIST = ROOT / "l3_client" / "local_mcps" / "local_translate_mcp" / "english_vocab_10k_words.txt"
DEFAULT_DEFINITIONS = ROOT / "l3_client" / "local_mcps" / "english_tutor_mcp" / "word_definitions.json"
DEFAULT_WORDBOOK_TS = ROOT / "clients" / "desktop" / "src" / "components" / "EnglishVocab" / "wordBookData.ts"
DEFAULT_OUT = ROOT / "l3_client" / "local_mcps" / "local_translate_mcp" / "english_vocab_10k.json"


def _clean_word(raw: str) -> str:
    return re.sub(r"[^a-z'-]", "", (raw or "").strip().lower())


def _read_word_list(path: Path) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = _clean_word(line)
        if not word or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def _read_definitions(path: Path) -> dict[str, dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    result: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        word = _clean_word(str(key))
        if not word or not isinstance(value, dict):
            continue
        meaning_cn = str(value.get("meaning_cn") or "").strip()
        part_of_speech = str(value.get("part_of_speech") or "-").strip() or "-"
        phonetic = str(value.get("phonetic") or "-").strip() or "-"
        if meaning_cn:
            result[word] = {
                "meaning_cn": meaning_cn,
                "part_of_speech": part_of_speech,
                "phonetic": phonetic,
                "source": "word_definitions",
            }
    return result


def _extract_wordbook_words(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    words: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"['\"]([A-Za-z][A-Za-z'-]*)['\"]", text):
        word = _clean_word(match.group(1))
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    return words


def _merge_words(primary: list[str], extra: list[str], definitions: dict[str, Any]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for word in [*primary, *extra, *definitions.keys()]:
        clean = _clean_word(word)
        if clean and clean not in seen:
            seen.add(clean)
            merged.append(clean)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the bundled 10k English vocabulary asset.")
    parser.add_argument("--word-list", type=Path, default=DEFAULT_WORD_LIST)
    parser.add_argument("--definitions", type=Path, default=DEFAULT_DEFINITIONS)
    parser.add_argument("--wordbook-ts", type=Path, default=DEFAULT_WORDBOOK_TS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    words = _read_word_list(args.word_list)
    definitions = _read_definitions(args.definitions)
    wordbook_words = _extract_wordbook_words(args.wordbook_ts)
    merged_words = _merge_words(words, wordbook_words, definitions)

    payload = {
        "version": 1,
        "asset": "english_vocab_10k",
        "source": {
            "word_list": "first20hours/google-10000-english google-10000-english-no-swears.txt",
            "definitions": str(args.definitions.relative_to(ROOT)).replace("\\", "/"),
            "wordbook": str(args.wordbook_ts.relative_to(ROOT)).replace("\\", "/"),
        },
        "counts": {
            "words": len(merged_words),
            "definitions": len(definitions),
            "wordbook_words": len(wordbook_words),
        },
        "words": merged_words,
        "definitions": definitions,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
