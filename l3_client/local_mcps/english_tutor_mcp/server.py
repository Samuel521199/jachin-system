from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from english_tutor import (
    english_correct_sentence,
    english_explain_word,
    english_make_examples,
    english_quiz_check_answer,
    english_quiz_generate,
    english_translate_cn_en,
)

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover - runtime dependency diagnostic
    raise SystemExit(f"FastMCP is required to run this stdio server: {exc}") from exc


mcp = FastMCP("english-tutor")


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool(name="english_correct_sentence")
def tool_correct_sentence(text: str, level: str = "beginner") -> str:
    """Correct common English grammar and expression issues with offline rules."""
    return _json(english_correct_sentence(text=text, level=level))


@mcp.tool(name="english_translate_cn_en")
def tool_translate_cn_en(text: str, direction: str = "auto") -> str:
    """Translate simple Chinese/English words and short phrases with an offline dictionary."""
    return _json(english_translate_cn_en(text=text, direction=direction))


@mcp.tool(name="english_explain_word")
def tool_explain_word(word: str) -> str:
    """Explain an English word with meaning, usage, and examples."""
    return _json(english_explain_word(word=word))


@mcp.tool(name="english_make_examples")
def tool_make_examples(topic_or_word: str, count: int = 3, level: str = "beginner") -> str:
    """Generate simple example sentences for a word or topic."""
    return _json(english_make_examples(topic_or_word=topic_or_word, count=count, level=level))


@mcp.tool(name="english_quiz_generate")
def tool_quiz_generate(topic: str = "daily English", count: int = 5, level: str = "beginner") -> str:
    """Generate an offline English quiz."""
    return _json(english_quiz_generate(topic=topic, count=count, level=level))


@mcp.tool(name="english_quiz_check_answer")
def tool_quiz_check_answer(question_id: str = "", question: str = "", answer: str = "") -> str:
    """Check a quiz answer against the offline answer key."""
    return _json(english_quiz_check_answer(question_id=question_id, question=question, answer=answer))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
