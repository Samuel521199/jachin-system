"""
L3 配置写出 — 按 payload/config/manifest.yaml 将包内配置写出到 ~/.jachin/config/

目标机只部署 L3，无 L2。L3 从 L2 拉取 MCP/Skill 包后解压，必须由 L3 自行执行配置写出，
保证下载即用。遵循 075-config-root-and-cloud-sync 规范。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("l3_node")

# 配置写出清单的候选路径（相对于解压根目录）
_MANIFEST_CANDIDATES = [
    "config/manifest.yaml",
    "payload/config/manifest.yaml",
]


def _resolve_output_root(raw: str | None) -> Path:
    """解析 output_root，支持 ~/.jachin"""
    if not raw or not str(raw).strip():
        from l3_node.jachin_config import get_jachin_root
        return get_jachin_root()
    s = str(raw).strip()
    if s.startswith("~/"):
        return Path.home() / s[2:].lstrip("/")
    return Path(s)


def _apply_write(
    config_dir: Path,
    output_root: Path,
    entry: dict[str, Any],
) -> bool:
    """
    执行单个 write 条目。
    entry: {path, type?, merge?}
    path 为相对 output_root 的路径，如 config/mcps/atom_xxx/config.yaml
    源文件在 config_dir 下，路径为 path 去掉 "config/" 前缀
    """
    path_str = entry.get("path") or ""
    if not path_str or not path_str.strip():
        return False
    path_str = path_str.strip().replace("\\", "/")

    merge = (entry.get("merge") or "overwrite_if_missing").strip().lower()
    if merge == "never_overwrite":
        merge = "skip_existing"
    elif merge in ("copy_missing", "overwrite_if_missing"):
        pass
    else:
        merge = "overwrite_if_missing"

    # 源路径：包内 config_dir 下的相对路径
    # path 如 "config/mcps/atom_xxx/config.yaml" -> 源 "mcps/atom_xxx/config.yaml"
    if path_str.startswith("config/"):
        src_relative = path_str[len("config/"):].lstrip("/")
    else:
        src_relative = path_str
    src_path = config_dir / src_relative

    dest_path = (output_root / path_str.replace("\\", "/")).resolve()

    if not src_path.exists():
        logger.debug("[ConfigManifest] 源不存在，跳过: %s", src_path)
        return False

    entry_type = (entry.get("type") or "file").strip().lower()

    if entry_type == "directory":
        if src_path.is_file():
            logger.warning("[ConfigManifest] 期望 directory 但源为文件: %s", src_path)
            return False
        # 递归复制目录，目标已存在则跳过
        any_written = False
        for f in src_path.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src_path)
                d = dest_path / rel
                if d.exists():
                    continue
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, d)
                any_written = True
                logger.debug("[ConfigManifest] 写出目录项: %s -> %s", rel, d)
        return any_written

    # type == "file"
    if src_path.is_dir():
        logger.warning("[ConfigManifest] 期望 file 但源为目录: %s", src_path)
        return False
    if dest_path.exists() and merge in ("copy_missing", "overwrite_if_missing"):
        return False  # 已存在则跳过
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dest_path)
    logger.debug("[ConfigManifest] 写出: %s -> %s", src_path, dest_path)
    return True


def write_config_from_manifest(extract_root: Path) -> bool:
    """
    若解压目录内存在 config/manifest.yaml 或 payload/config/manifest.yaml，
    则解析并按 writes 写出到 ~/.jachin/config/。

    extract_root: 解压根目录（如 l3_mcp_cache/{item_id}）

    Returns:
        True 若执行了写出，False 若未找到 manifest 或未写出任何内容
    """
    manifest_path: Path | None = None
    config_dir: Path | None = None

    for cand in _MANIFEST_CANDIDATES:
        p = extract_root / cand
        if p.exists() and p.is_file():
            manifest_path = p
            # config_dir = manifest 所在目录
            config_dir = p.parent
            break

    if not manifest_path or not config_dir:
        return False

    try:
        import yaml
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("[ConfigManifest] 解析 manifest 失败 %s: %s", manifest_path, e)
        return False

    output_root = _resolve_output_root(raw.get("output_root"))
    writes = raw.get("writes") or []
    if not isinstance(writes, list):
        return False

    written = 0
    for entry in writes:
        if not isinstance(entry, dict):
            continue
        if _apply_write(config_dir, output_root, entry):
            written += 1

    return written > 0
