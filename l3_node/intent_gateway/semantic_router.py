"""
§5 L2 语义路由的本地占位：关键词袋 → routing_hint（可观测/后续接向量分类器）。
"""
from __future__ import annotations

from typing import Any, Optional

# skill_id → 关键词（小写匹配）
_KEYWORD_BAGS: list[tuple[str, tuple[str, ...]]] = [
    ("skill.bi_daily_report", ("bi", "日报", "经营分析", "战略战报")),
    ("core.stop_automated_recruitment", ("停止招聘", "关闭招聘", "取消招聘", "无人值守")),
    ("mcp:add_automated_recruitment_task", ("收网", "打招呼", "抓简历", "无人值守", "透析")),
    ("mcp:atom_post_job_boss", ("发帖", "发布职位", "jd", "岗位")),
]

# Embedding 原型：与关键词袋对齐的短文本（可扩展为多句）
SKILL_PROTOTYPE_TEXTS: list[tuple[str, str]] = [
    (sid, f"{sid} " + " ".join(kws)) for sid, kws in _KEYWORD_BAGS
]


def merge_route_hints(
    keyword_hint: Optional[dict[str, Any]],
    embedding_hint: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """合并关键词袋与向量 Top-K，供可观测与后续策略。"""
    if not keyword_hint and not embedding_hint:
        return None
    return {
        "kind": "merged",
        "keyword": keyword_hint,
        "embedding": embedding_hint,
    }


def infer_semantic_route_hint(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    if len(raw) < 2:
        return None
    low = raw.lower()
    hits: list[dict[str, Any]] = []
    for skill_id, kws in _KEYWORD_BAGS:
        if skill_id == "skill.bi_daily_report" and not _route_capability_available(
            ids=("com.jachin.bi.daily_report", "com.jachin.bi.analysis"),
            prefixes=("com.jachin.bi",),
            name_includes=("bi ", "bi每日", "bi 每日", "战报"),
            dev_env="JACHIN_DEV_LOAD_BI_CAPABILITY",
        ):
            continue
        matched = [k for k in kws if k.lower() in low or k in raw]
        if matched:
            hits.append({"skill_id": skill_id, "keywords": matched[:5]})
    if not hits:
        return None
    return {"kind": "keyword_bag", "hits": hits[:5]}


def _route_capability_available(
    *,
    ids: tuple[str, ...] = (),
    prefixes: tuple[str, ...] = (),
    name_includes: tuple[str, ...] = (),
    dev_env: str | None = None,
) -> bool:
    try:
        from l3_node.capability_runtime_gate import capability_available

        return capability_available(ids=ids, prefixes=prefixes, name_includes=name_includes, dev_env=dev_env)
    except Exception:
        return False
