"""读本机 nexus_config.json（L3 热读，与 core 持久化路径一致）。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def nexus_config_path() -> Path:
    try:
        from l3_node.jachin_config import get_jachin_root

        return get_jachin_root() / "nexus_config.json"
    except ImportError:
        return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))) / "nexus_config.json"


def get_nexus_config() -> dict[str, Any] | None:
    p = nexus_config_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as e:
        logger.debug("[nexus_config] 读取失败: %s", e)
        return None


def sync_merge_sqlite_read_from_env_to_nexus_config() -> None:
    """
    当 JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL 为真时，把 agent.merge_sqlite_read_into_tool_pool=true
    合并写入 nexus_config.json（尊重盘上显式 false，不覆盖）。

    便于开发机「设一次环境变量 → 下次启动仅靠配置文件即可生效」；运行时仍以 tool_pool.implicit_sqlite_read_merge_enabled 为准（env 优先）。
    """
    v = (os.environ.get("JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL") or "").strip().lower()
    if v not in ("1", "true", "yes"):
        return
    p = nexus_config_path()
    cfg: dict[str, Any] = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = raw
        except Exception as e:
            logger.warning("[nexus_config] 无法解析现有文件，跳过 merge_sqlite 持久化: %s", e)
            return
    agent = cfg.get("agent")
    if not isinstance(agent, dict):
        agent = {}
        cfg["agent"] = agent
    cur = agent.get("merge_sqlite_read_into_tool_pool")
    if cur is False:
        return
    if cur is True:
        return
    agent["merge_sqlite_read_into_tool_pool"] = True
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / (p.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        logger.info(
            "[nexus_config] 已根据 JACHIN_MERGE_SQLITE_READ_INTO_TOOL_POOL 写入 agent.merge_sqlite_read_into_tool_pool=true → %s",
            p,
        )
    except Exception as e:
        logger.warning("[nexus_config] 写入 merge_sqlite 标志失败: %s", e)
