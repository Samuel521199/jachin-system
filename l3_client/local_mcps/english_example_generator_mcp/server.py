from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from example_generator import english_example_model_status, english_generate_example_card

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"FastMCP is required to run this stdio server: {exc}") from exc


mcp = FastMCP("english-example-generator")


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool(name="english_generate_example_card")
def tool_generate_example_card(word: str, book_id: str = "daily_life_ngsl", meaning_cn: str = "") -> str:
    """Generate a short scene-aware English example sentence with a local small GGUF model."""
    return _json(english_generate_example_card(word=word, book_id=book_id, meaning_cn=meaning_cn))


@mcp.tool(name="english_example_model_status")
def tool_model_status() -> str:
    """Check whether the local example generation model and runtime are ready."""
    return _json(english_example_model_status())


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
