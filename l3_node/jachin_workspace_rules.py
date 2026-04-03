"""从 ~/.jachin/workspace/JACHIN.md（或 jachin.md、.jachin/rules.md）读取摘录，供 system prompt 注入。"""
from __future__ import annotations

import os
from pathlib import Path


def get_jachin_workspace_rules_snippet(*, max_chars: int = 12000) -> str:
    home = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    workspace = home / "workspace"
    candidates = [
        workspace / "JACHIN.md",
        workspace / "jachin.md",
        workspace / ".jachin" / "rules.md",
    ]
    for p in candidates:
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n\n…(JACHIN 规则已截断)"
                return f"【工作区规则 JACHIN.md / .jachin/rules.md 摘录】\n{text}\n"
        except OSError:
            continue
    return ""
