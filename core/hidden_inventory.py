"""
L2 隐藏清单：Skill 与 MCP 的 hide/unhide 状态

存储于 ~/.jachin/hidden_inventory.json
格式: { "skills": ["item_id1", ...], "l3_mcps": ["item_id1", ...] }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_HIDDEN_PATH = Path.home() / ".jachin" / "hidden_inventory.json"


def _load() -> dict[str, list[str]]:
    if not _HIDDEN_PATH.exists():
        return {"skills": [], "l3_mcps": []}
    try:
        data = json.loads(_HIDDEN_PATH.read_text(encoding="utf-8"))
        skills = data.get("skills") or []
        l3_mcps = data.get("l3_mcps") or []
        return {"skills": list(skills) if isinstance(skills, list) else [], "l3_mcps": list(l3_mcps) if isinstance(l3_mcps, list) else []}
    except Exception as e:
        logger.warning("[HiddenInventory] 读取失败: %s", e)
        return {"skills": [], "l3_mcps": []}


def _save(data: dict[str, list[str]]) -> None:
    _HIDDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HIDDEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_hidden_skill(item_id: str) -> bool:
    return item_id in _load()["skills"]


def is_hidden_l3_mcp(item_id: str) -> bool:
    return item_id in _load()["l3_mcps"]


def hide_skill(item_id: str) -> bool:
    data = _load()
    if item_id in data["skills"]:
        return False
    data["skills"].append(item_id)
    _save(data)
    return True


def unhide_skill(item_id: str) -> bool:
    data = _load()
    if item_id not in data["skills"]:
        return False
    data["skills"].remove(item_id)
    _save(data)
    return True


def hide_l3_mcp(item_id: str) -> bool:
    data = _load()
    if item_id in data["l3_mcps"]:
        return False
    data["l3_mcps"].append(item_id)
    _save(data)
    return True


def unhide_l3_mcp(item_id: str) -> bool:
    data = _load()
    if item_id not in data["l3_mcps"]:
        return False
    data["l3_mcps"].remove(item_id)
    _save(data)
    return True


def get_hidden_skills() -> set[str]:
    return set(_load()["skills"])


def get_hidden_l3_mcps() -> set[str]:
    return set(_load()["l3_mcps"])
