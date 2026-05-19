"""
Wing 注册表（AL — Wing 全量重映射）

规范 Memory Nexus 的 Wing 命名，提供 normalize_wing() 将旧名/别名统一映射到规范名，
以及每个 Wing 的语义元数据（描述、半衰期、重要性系数），供 memory_backend.py 统一查询。

规范 Wing 名（五个）
--------------------
  Episodes   - 情节/事件记忆（短期，30d 半衰期）
  Knowledge  - 知识/事实（中期，90d 半衰期）
  Procedures - 操作步骤/经验（长期，180d 半衰期）
  Core       - 核心人设/用户画像（长期，180d 半衰期）
  Inbox      - 待归类/暂存（短期，7d 半衰期）

环境变量
--------
JACHIN_WING_IMPORTANCE_OVERRIDE  JSON 对象，可覆盖默认重要性系数
    示例：{"Procedures": 1.5, "Knowledge": 1.1}
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WingMeta:
    name: str                 # 规范名
    description: str          # 一句话描述
    half_life_days: float     # Ebbinghaus 遗忘半衰期（天）
    importance_mult: float    # 重要性乘数基准（1.0 = 中性）
    aliases: tuple[str, ...]  # 旧名 / 别名（小写匹配）


# ---------------------------------------------------------------------------
# 规范注册表
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, WingMeta] = {
    "Episodes": WingMeta(
        name="Episodes",
        description="情节记忆：具体事件、对话片段、临时上下文",
        half_life_days=30.0,
        importance_mult=1.00,
        aliases=("episode", "events", "event", "chat", "dialog", "情节", "事件"),
    ),
    "Knowledge": WingMeta(
        name="Knowledge",
        description="知识/事实：持久性概念、领域知识、用户偏好",
        half_life_days=90.0,
        importance_mult=1.20,
        aliases=("knowledge", "fact", "facts", "info", "知识", "事实"),
    ),
    "Procedures": WingMeta(
        name="Procedures",
        description="操作步骤/程序记忆：SOP、成功工具调用路径、修复经验",
        half_life_days=180.0,
        importance_mult=1.30,
        aliases=("procedure", "procedures", "sop", "steps", "howto", "how_to",
                 "程序", "步骤", "操作"),
    ),
    "Core": WingMeta(
        name="Core",
        description="核心人设/用户画像：用户身份、偏好、长期背景",
        half_life_days=180.0,
        importance_mult=1.25,
        aliases=("core", "persona", "profile", "identity", "user_profile",
                 "核心", "人设", "画像"),
    ),
    "Inbox": WingMeta(
        name="Inbox",
        description="待归类暂存：新进记忆等待分类归档",
        half_life_days=7.0,
        importance_mult=0.80,
        aliases=("inbox", "staging", "buffer", "pending", "暂存", "待归类"),
    ),
}

# 别名 → 规范名 小写映射缓存
_ALIAS_MAP: dict[str, str] = {}
for _meta in _REGISTRY.values():
    _ALIAS_MAP[_meta.name.lower()] = _meta.name
    for _alias in _meta.aliases:
        _ALIAS_MAP[_alias.lower()] = _meta.name

CANONICAL_WINGS: tuple[str, ...] = tuple(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def normalize_wing(raw: str) -> str:
    """
    将任意 Wing 字符串归一化为规范名。
    未知名称原样返回（保持向后兼容，避免丢弃已有数据）。
    """
    if not raw:
        return raw
    canon = _ALIAS_MAP.get(raw.strip().lower())
    if canon:
        return canon
    # 大小写精确匹配（如 "PROCEDURES"）
    title = raw.strip().title()
    if title in _REGISTRY:
        return title
    return raw  # 未知 Wing 原样保留


def get_wing_meta(wing: str) -> WingMeta | None:
    """返回规范 Wing 的元数据；未知 Wing 返回 None。"""
    return _REGISTRY.get(normalize_wing(wing))


def wing_half_life_days(wing: str) -> float:
    """规范 Wing 的 Ebbinghaus 半衰期（天），未知 Wing 返回默认 30d。"""
    meta = get_wing_meta(wing)
    return meta.half_life_days if meta else 30.0


def wing_importance_mult(wing: str) -> float:
    """
    Wing 的重要性系数。读取 JACHIN_WING_IMPORTANCE_OVERRIDE 可覆盖。
    未知 Wing 返回 1.0（中性）。
    """
    meta = get_wing_meta(wing)
    base = meta.importance_mult if meta else 1.0
    # 允许运行时覆盖
    _override_raw = os.environ.get("JACHIN_WING_IMPORTANCE_OVERRIDE") or ""
    if _override_raw.strip():
        try:
            overrides = json.loads(_override_raw)
            canon = normalize_wing(wing)
            if canon in overrides:
                return float(overrides[canon])
        except Exception:
            pass
    return base


def list_all_wings() -> list[dict]:
    """返回所有规范 Wing 的元数据列表（供 HTTP 诊断端点使用）。"""
    return [
        {
            "name": m.name,
            "description": m.description,
            "half_life_days": m.half_life_days,
            "importance_mult": wing_importance_mult(m.name),
            "aliases": list(m.aliases),
        }
        for m in _REGISTRY.values()
    ]
