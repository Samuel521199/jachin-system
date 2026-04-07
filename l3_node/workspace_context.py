"""子 Agent / delegate 工作区派生路径（ContextVar）；Native 兜底 cwd 见 get_effective_workspace_root。"""
from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

_workspace_sandbox_rel: ContextVar[str | None] = ContextVar("jachin_workspace_sandbox_rel", default=None)


def set_delegate_workspace_sandbox(relative_subdir: str | None) -> object | None:
    """
    relative_subdir 如 sandboxes/sub-abc123（相对于 ~/.jachin/workspace）。
    返回 reset token；须在 finally 中 reset_delegate_workspace_sandbox(token)。
    """
    if not relative_subdir or not str(relative_subdir).strip():
        return None
    clean = str(relative_subdir).strip().replace("\\", "/").lstrip("/")
    if ".." in clean.split("/"):
        return None
    return _workspace_sandbox_rel.set(clean)


def reset_delegate_workspace_sandbox(token: object | None) -> None:
    if token is not None:
        try:
            _workspace_sandbox_rel.reset(token)
        except ValueError:
            pass


def get_workspace_base() -> Path:
    root = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    return (root / "workspace").resolve()


def get_effective_workspace_root() -> Path:
    """Native fs_read/fs_write/shell 的默认 cwd 根：有沙箱时为 workspace/<sandbox_rel>。"""
    rel = _workspace_sandbox_rel.get()
    base = get_workspace_base()
    if not rel:
        return base
    return (base / rel).resolve()


def enforce_delegate_sandbox_enabled() -> bool:
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ag = cfg.get("agent") or {}
        return bool(ag.get("enforce_delegate_workspace_sandbox", True))
    except Exception:
        return True
