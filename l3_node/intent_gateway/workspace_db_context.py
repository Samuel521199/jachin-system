"""
工作区「语义层 / 数据字典」与 Golden SQL 少样本（关键词 RAG-lite）。

约定文件（均在网关嗅探所用 workspace 根下，通常为 ~/.jachin/workspace）：
- db_semantics.md   自然语言业务指标 → SQL 片段/条件（由用户维护）
- golden_sql_examples.jsonl  每行 JSON：{"q","sql",可选 "tags":[]}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SEMANTICS_FILENAME = "db_semantics.md"
DEFAULT_GOLDEN_FILENAME = "golden_sql_examples.jsonl"


def _ws_root(workspace_dir: str) -> Path:
    p = (workspace_dir or "").strip()
    if not p:
        return Path.home() / ".jachin" / "workspace"
    return Path(p).expanduser().resolve()


def load_db_semantics_snippet(workspace_dir: str, *, max_chars: int) -> str:
    """读取 db_semantics.md，截断至 max_chars。"""
    cap = max(0, int(max_chars))
    if cap <= 0:
        return ""
    path = _ws_root(workspace_dir) / DEFAULT_SEMANTICS_FILENAME
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as e:
        logger.debug("[WorkspaceDBContext] 读取 db_semantics 失败: %s", e)
        return ""
    if not raw:
        return ""
    if len(raw) <= cap:
        return raw
    return raw[: max(0, cap - 12)].rstrip() + "\n…(截断)"


def _tokenize_for_match(text: str) -> set[str]:
    s = (text or "").lower()
    parts = re.split(r"[^\w\u4e00-\u9fff]+", s, flags=re.UNICODE)
    return {p for p in parts if len(p) >= 2}


def _score_example(user_q: str, obj: dict[str, Any]) -> float:
    u_toks = _tokenize_for_match(user_q)
    if not u_toks:
        return 0.0
    q = str(obj.get("q") or obj.get("question") or "")
    tags = obj.get("tags")
    blob = q
    if isinstance(tags, list):
        blob += " " + " ".join(str(x) for x in tags if x)
    e_toks = _tokenize_for_match(blob)
    if not e_toks:
        return 0.0
    inter = u_toks & e_toks
    return len(inter) / max(1, min(len(u_toks), len(e_toks)) + len(inter))


def load_golden_sql_fewshot(
    workspace_dir: str,
    user_query: str,
    *,
    max_chars: int,
    max_examples: int = 3,
) -> str:
    """
    从 golden_sql_examples.jsonl 按与用户问句的词重叠选 Top 条，格式化为 Few-Shot 文本。
    """
    cap = max(0, int(max_chars))
    if cap <= 0 or max_examples <= 0:
        return ""
    path = _ws_root(workspace_dir) / DEFAULT_GOLDEN_FILENAME
    if not path.is_file():
        return ""
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                o = json.loads(line)
                if isinstance(o, dict) and (o.get("sql") or o.get("query")):
                    rows.append(o)
            except json.JSONDecodeError:
                continue
    except OSError as e:
        logger.debug("[WorkspaceDBContext] 读取 golden_sql 失败: %s", e)
        return ""
    if not rows:
        return ""
    uq = (user_query or "").strip()
    scored = [(_score_example(uq, r), r) for r in rows]
    scored.sort(key=lambda x: -x[0])
    picked: list[dict[str, Any]] = []
    for sc, r in scored:
        if sc <= 0 and uq:
            continue
        picked.append(r)
        if len(picked) >= max_examples:
            break
    if not picked and rows:
        picked = rows[:max_examples]
    lines_out: list[str] = []
    used = 0
    for i, r in enumerate(picked, 1):
        q = str(r.get("q") or r.get("question") or "").strip()
        sql = str(r.get("sql") or r.get("query") or "").strip()
        chunk = f"例{i} 问：{q}\nSQL：{sql}\n"
        if used + len(chunk) > cap:
            chunk = chunk[: max(0, cap - used - 8)].rstrip() + "…\n"
        lines_out.append(chunk)
        used += len(chunk)
        if used >= cap:
            break
    return "\n".join(lines_out).strip()


def build_workspace_db_context_bundle(
    workspace_dir: str,
    user_input: str,
    *,
    semantics_max_chars: int,
    golden_max_chars: int,
    golden_max_examples: int,
) -> dict[str, str]:
    return {
        "db_semantics_snippet": load_db_semantics_snippet(
            workspace_dir, max_chars=semantics_max_chars
        ),
        "golden_sql_fewshot": load_golden_sql_fewshot(
            workspace_dir,
            user_input,
            max_chars=golden_max_chars,
            max_examples=golden_max_examples,
        ),
    }
