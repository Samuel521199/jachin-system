from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "l3_client" / "local_mcps" / "english_tutor_mcp" / "word_definitions.json"
TARGET = ROOT / "clients" / "desktop" / "src" / "components" / "EnglishVocab" / "wordBookDefinitions.ts"


def main() -> int:
    definitions = json.loads(SOURCE.read_text(encoding="utf-8"))
    payload = json.dumps(definitions, ensure_ascii=False, indent=2, sort_keys=True)
    TARGET.write_text(
        "\n".join(
            [
                "// Generated from l3_client/local_mcps/english_tutor_mcp/word_definitions.json.",
                "// Do not edit by hand. Run: python scripts/generate_english_wordbook_definitions.py",
                "",
                "export type LocalWordDefinition = {",
                "  meaning_cn: string;",
                "  part_of_speech: string;",
                "};",
                "",
                f"export const localWordDefinitions: Record<string, LocalWordDefinition> = {payload};",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"generated {TARGET} entries={len(definitions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
