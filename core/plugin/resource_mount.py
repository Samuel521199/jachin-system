"""
Resource Mount - 静态资源型 JMP 只读卷挂载机制

execution_model: "resource_mount" 用于 Persona 语音包、Memory 向量库等重型静态资产。
无需作为进程运行，解压到只读目录，通过环境变量 JACHIN_VOL_{PLUGIN_ID} 供 VAD/RAG 等插件读取。

架构降维打击：
- 沙箱隔离：LLM 进程无法修改 memory-legal-rag 原文件（只读目录）
- 即插即用：LLM 只需读取 JACHIN_VOL_xxx，无需写死路径
- 无限组合：同一 LLM 引擎，挂载 persona-cyber-maid 即毒舌女仆，换成 persona-butler 即英式管家
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

_resources_dir: Path | None = None
_mounts: dict[str, str] = {}

ENV_PREFIX = "JACHIN_VOL_"


def _plugin_id_to_env_key(plugin_id: str) -> str:
    """plugin_id -> 环境变量名，如 com.jachin.memory-legal -> JACHIN_VOL_COM_JACHIN_MEMORY_LEGAL"""
    return ENV_PREFIX + plugin_id.replace(".", "_").replace("-", "_").upper()


def get_resources_dir() -> Path:
    """获取资源根目录（只读挂载点）"""
    global _resources_dir
    if _resources_dir is None:
        from core.config import settings
        base = Path(getattr(settings, "RESOURCES_REPO_PATH", "./resources_repo"))
        if not base.is_absolute():
            base = Path.cwd() / base
        base.mkdir(parents=True, exist_ok=True)
        _resources_dir = base
    return _resources_dir


def get_mount_path(plugin_id: str) -> str | None:
    """
    获取已挂载资源的路径

    Skill 插件读取方式（二选一）：
        vol_path = os.environ.get("JACHIN_VOL_COM_JACHIN_MEMORY_LEGAL")
        vol_path = get_mount_path("com.jachin.memory-legal-rag")
    """
    return _mounts.get(plugin_id)


def register_mount(plugin_id: str, path: str, make_readonly: bool = True) -> None:
    """
    注册资源挂载路径，设置环境变量，并可选施加只读保护

    Args:
        plugin_id: 插件 ID
        path: 挂载目录绝对路径
        make_readonly: 是否移除写权限（生产环境可配合 mount --bind -o ro 实现内核级锁死）
    """
    _mounts[plugin_id] = path
    env_key = _plugin_id_to_env_key(plugin_id)
    os.environ[env_key] = path

    if make_readonly:
        _make_readonly_volume(Path(path))

    logger.info(
        "Resource mount registered: %s -> %s (env: %s, readonly=%s)",
        plugin_id,
        path,
        env_key,
        make_readonly,
    )


def _make_readonly_volume(path: Path) -> None:
    """
    对挂载目录施加只读保护（移除写权限）
    生产环境可进一步使用 mount --bind -o ro 实现内核级锁死
    """
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                p = Path(root) / name
                try:
                    mode = p.stat().st_mode
                    p.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
                except OSError:
                    pass
            for name in dirs:
                p = Path(root) / name
                try:
                    mode = p.stat().st_mode
                    p.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
                except OSError:
                    pass
        mode = path.stat().st_mode
        path.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
        logger.debug("Read-only applied to %s", path)
    except OSError as e:
        logger.warning("Could not apply read-only to %s: %s", path, e)


def list_mounts() -> dict[str, str]:
    """列出所有已挂载资源"""
    return dict(_mounts)
