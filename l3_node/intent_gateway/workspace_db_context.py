"""
工作区「语义层 / 数据字典」与 Golden SQL 少样本（关键词 RAG-lite）。

语义层只提供证据；认知内核裁决见 docs/07_memory_first_main_agent_and_voice_app_agents.md

约定文件（均在网关嗅探所用 workspace 根下，通常为 ~/.jachin/workspace）：
- db_semantics.yaml  **L4 结构化业务语义**（域 → 词条 → SQL 片段），解析后写入 bundle.extra["semantic_layer"]
- db_semantics.md   自然语言业务指标 → SQL 片段/条件（由用户维护；注入 [ENVIRONMENT_REPORT] 摘要）
- golden_sql_examples.jsonl  每行 JSON：{"q","sql",可选 "tags":[]}
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SEMANTICS_FILENAME = "db_semantics.md"
DEFAULT_SEMANTICS_YAML = "db_semantics.yaml"
DEFAULT_GOLDEN_FILENAME = "golden_sql_examples.jsonl"

# 内置默认业务语义（与仓库 config/db_semantics.yaml 对齐）；工作区/项目 YAML 缺失或为空时启用，并与加载结果深度合并。
DEFAULT_SEMANTIC_LAYER_FALLBACK: dict[str, Any] = {
    "inventory_domain": {
        "缺货": "WHERE count = 0 OR count IS NULL",
        "低库存": "WHERE count < 10",
        "最贵": "ORDER BY price DESC LIMIT 1",
    }
}

# 注入 System Prompt 时置于 YAML 正文之上，约束模型优先查字典、禁止对字典已定义阈值向用户二次确认。
SEMANTIC_ABSOLUTE_LAW_FOR_PROMPT = """【绝对语义法则】：下方附带本系统的《业务语义字典 (Semantic Dictionary)》（来自 db_semantics.yaml 及/或系统内置默认）。
当你收到用户的自然语言指令（如「低库存」「最贵」「缺货」「活跃用户」等）时，你必须 **首先且强制** 在字典中查找对应的 SQL/逻辑映射条目。
只有在字典中 **完全找不到** 相关定义时，你才被允许向统帅（用户）提问澄清。
若字典中有明确定义（例如「低库存」对应 `WHERE count < 10`），你必须 **直接应用该规则** 并结合 Probe 得到的真实表名列名编写查询或调用 read_query，**绝对禁止** 要求统帅再次确认该阈值或同义业务含义。"""


def _default_semantic_layer_copy() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SEMANTIC_LAYER_FALLBACK)


def default_semantic_layer() -> dict[str, Any]:
    """无 YAML / 解析失败 / 网关兜底时返回的内置业务语义（与 config/db_semantics.yaml 示例一致）。"""
    return _default_semantic_layer_copy()


def merge_semantic_layer_with_fallback(loaded: dict[str, Any] | None) -> dict[str, Any]:
    """
    将已加载的 YAML 映射与内置默认合并：同域同键以 **loaded** 覆盖；loaded 未写的域/键保留默认。
    """
    base = _default_semantic_layer_copy()
    ov = loaded if isinstance(loaded, dict) else {}
    out: dict[str, Any] = copy.deepcopy(base)
    for k, v in ov.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged_inner = dict(out[k])
            merged_inner.update(v)
            out[k] = merged_inner
        else:
            out[k] = copy.deepcopy(v) if isinstance(v, dict) else v
    return out


def _ws_root(workspace_dir: str) -> Path:
    p = (workspace_dir or "").strip()
    if not p:
        return Path.home() / ".jachin" / "workspace"
    return Path(p).expanduser().resolve()


def _project_config_semantics_yaml() -> Path | None:
    """仓库内 config/db_semantics.yaml（JACHIN_APP_ROOT 或从本模块向上推导项目根）。"""
    roots: list[Path] = []
    ar = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
    if ar:
        roots.append(Path(ar).expanduser().resolve())
    try:
        here = Path(__file__).resolve()
        cur: Path | None = here.parent
        for _ in range(10):
            if cur is None or cur == cur.parent:
                break
            if (cur / "l3_node").is_dir() and (cur / "config" / DEFAULT_SEMANTICS_YAML).is_file():
                roots.append(cur)
                break
            cur = cur.parent
    except Exception:
        pass
    for r in roots:
        cand = r / "config" / DEFAULT_SEMANTICS_YAML
        if cand.is_file():
            return cand
    return None


def load_db_semantics_yaml(workspace_dir: str) -> dict[str, Any]:
    """
    读取 L4 业务语义 YAML。优先级：
    1) <workspace>/db_semantics.yaml（绝对路径由 _ws_root(workspace_dir) 解析）
    2) 项目 config/db_semantics.yaml（JACHIN_APP_ROOT 或从 l3_node 向上解析仓库根）

    任一路径成功解析到非空 dict 后，与 **DEFAULT_SEMANTIC_LAYER_FALLBACK** 合并（文件覆盖默认同键）。
    若无任何有效文件、解析失败或 PyYAML 未安装，返回 **仅内置默认**（永不静默为空 dict）。
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "[WorkspaceDBContext] PyYAML 未安装，无法读取 db_semantics.yaml，使用内置默认语义层（路径仍记录 workspace=%s）",
            _ws_root(workspace_dir),
        )
        return _default_semantic_layer_copy()

    paths: list[Path] = []
    ws = _ws_root(workspace_dir)
    paths.append(ws / DEFAULT_SEMANTICS_YAML)
    pc = _project_config_semantics_yaml()
    if pc is not None:
        paths.append(pc)

    seen_resolved: set[str] = set()
    loaded_nonempty: dict[str, Any] | None = None
    for path in paths:
        if not path.is_file():
            continue
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen_resolved:
            continue
        seen_resolved.add(key)
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            data = yaml.safe_load(raw)
        except Exception as e:
            logger.warning("[WorkspaceDBContext] 读取/解析 %s 失败: %s", path, e)
            continue
        if not isinstance(data, dict):
            logger.warning("[WorkspaceDBContext] %s 根类型非 mapping，已跳过", path)
            continue
        if data:
            loaded_nonempty = data
            logger.info("[WorkspaceDBContext] 已加载 db_semantics.yaml: %s", path)
            break
        logger.debug("[WorkspaceDBContext] %s 为空 mapping，尝试下一候选路径", path)

    if loaded_nonempty is None:
        logger.info(
            "[WorkspaceDBContext] 未找到有效 db_semantics.yaml（已查 workspace=%s），使用内置默认语义层",
            ws,
        )
        return _default_semantic_layer_copy()

    return merge_semantic_layer_with_fallback(loaded_nonempty)


def format_db_semantics_layer_for_prompt(
    data: dict[str, Any] | None,
    *,
    include_absolute_law: bool = True,
) -> str:
    """将 semantic_layer 格式化为可注入 system 的块；空 dict 返回空串（调用方应保证 load 已带默认）。"""
    if not isinstance(data, dict) or not data:
        return ""
    try:
        import yaml  # type: ignore[import-untyped]

        body = yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    except Exception:
        body = json.dumps(data, ensure_ascii=False, indent=2)
    law = (SEMANTIC_ABSOLUTE_LAW_FOR_PROMPT.strip() + "\n\n") if include_absolute_law else ""
    return f"\n{law}《业务语义字典》正文（db_semantics.yaml 与内置默认合并结果）：\n```yaml\n{body}\n```\n"


def format_semantic_layer_excerpt_for_environment_report(data: dict[str, Any] | None, *, max_chars: int = 900) -> str:
    """写入 [ENVIRONMENT_REPORT] 的短摘要（与 system 大块互补）。"""
    if not isinstance(data, dict) or not data:
        return ""
    try:
        import yaml  # type: ignore[import-untyped]

        body = yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
    except Exception:
        body = json.dumps(data, ensure_ascii=False, indent=2)
    cap = max(200, int(max_chars))
    if len(body) > cap:
        body = body[: max(0, cap - 20)].rstrip() + "\n…(截断，完整见 system《业务语义字典》)"
    return (
        "【绝对语义法则】自然语言业务词须先查下方 YAML；字典有定义则直接应用，勿向用户二次确认阈值。\n"
        f"```yaml\n{body}\n```"
    )


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
