from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_translate import (
    local_translate_model_status,
    local_translate_text,
    local_translate_warmup,
)
from english_example_pack import example_pack_status

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"FastMCP is required to run this stdio server: {exc}") from exc


mcp = FastMCP("local-translate")


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool(name="local_translate_text")
def tool_translate_text(text: str, direction: str = "auto") -> str:
    """Translate Chinese/English text locally with installed OPUS-MT CTranslate2 models."""
    return _json(local_translate_text(text=text, direction=direction))


@mcp.tool(name="local_translate_model_status")
def tool_model_status() -> str:
    """Check local translation model installation status."""
    return _json(local_translate_model_status())


@mcp.tool(name="local_translate_warmup")
def tool_warmup(direction: str = "all") -> str:
    """Warm up local translation models."""
    return _json(local_translate_warmup(direction=direction))


@mcp.tool(name="english_example_pack_status")
def tool_example_pack_status() -> str:
    """Check bundled English example pack availability and counts."""
    return _json(example_pack_status())


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
