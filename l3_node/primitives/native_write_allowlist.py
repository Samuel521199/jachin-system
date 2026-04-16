"""
Native 写路径白名单（core:fs_write、util:generate_office_doc 等）。

读权限与写解耦：core:fs_read 使用 assert_path_allowed_for_native_read + fs_path_blacklist；
写仍仅允许本文件中的白名单根目录。

安全要求：对目标路径与各白名单根目录均使用 .resolve()，并用 Path.is_relative_to 判定，
避免 ../ 路径穿越。与 workspace / HR 数据卷 / 用户常用目录对齐。
"""
from __future__ import annotations

from pathlib import Path
from typing import List


def get_builtin_native_write_roots() -> List[Path]:
    """
    内置写入白名单根目录（不含用户扩展），已 resolve、去重。
    """
    from l3_node.jachin_config import get_hr_jds_dir
    from l3_node.workspace_context import get_effective_workspace_root

    user = Path.home()
    try:
        workspace = get_effective_workspace_root().resolve()
    except (OSError, RuntimeError):
        workspace = (user / ".jachin" / "workspace").resolve()

    # loader.py 与 core/native_tools 的 proj 根：…/jachin-system-main
    here = Path(__file__).resolve()
    proj = here.parent.parent.parent

    candidates = [
        workspace,
        user / ".jachin" / "client_volumes",
        proj / "data" / "hr_resumes",
        get_hr_jds_dir(proj),
        user / "Desktop",
        user / "Downloads",
        user / "Documents",
    ]

    roots: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            r = c.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            roots.append(r)
    return roots


def get_allowed_native_write_roots() -> List[Path]:
    """
    返回已 resolve 的去重根目录列表（顺序稳定）。
    包含：内置根目录 + ~/.jachin/config/native_fs_policy.json 中的 write_allowlist_extra。
    """
    from l3_node.primitives.native_fs_policy_store import get_write_allowlist_extra_roots

    roots = list(get_builtin_native_write_roots())
    seen: set[str] = {str(r).lower() for r in roots}
    for extra in get_write_allowlist_extra_roots():
        key = str(extra).lower()
        if key not in seen:
            seen.add(key)
            roots.append(extra)
    return roots


def assert_path_allowed_for_native_write(path: Path) -> None:
    """
    校验 path（可先 expanduser）是否落在允许写入的根目录之下。
    不通过则抛出 ValueError。
    """
    try:
        rp = path.expanduser().resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"无效路径: {path} ({e})") from e

    roots = get_allowed_native_write_roots()
    for root in roots:
        try:
            if rp.is_relative_to(root):
                return
        except ValueError:
            continue

    raise ValueError(
        "路径越界，严禁写入该位置。仅允许写入 Jachin workspace、client_volumes、HR 数据目录，"
        "或用户 Desktop / Downloads / Documents 下（请使用已解析的绝对路径或 workspace 相对路径）。"
    )


def path_is_under_allowed_write_roots(path: Path) -> bool:
    """不抛异常的布尔判定（供 fs_read 候选路径快速过滤）。"""
    try:
        assert_path_allowed_for_native_write(path)
        return True
    except ValueError:
        return False


def assert_path_allowed_for_native_read(path: Path) -> None:
    """
    读取权限：全局放行，仅拒绝敏感路径黑名单（见 fs_path_blacklist.is_read_path_blacklisted）。
    不通过时抛出 ValueError（由 core.native_tools 转为 SecurityException）。
    """
    try:
        rp = path.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as e:
        raise ValueError(f"无效路径: {path} ({e})") from e

    from l3_node.primitives.fs_path_blacklist import is_read_path_blacklisted

    if is_read_path_blacklisted(rp):
        raise ValueError("权限受限：严禁系统读取底层密钥或系统级敏感文件")
