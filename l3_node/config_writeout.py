"""
配置写出：按包内 config/manifest.yaml 将配置写出到 ~/.jachin/config/

规范: .cursor/rules/075-config-root-and-cloud-sync.mdc
- L2 SyncDaemon 和 L3 mcp_sync 解压包后调用
- merge 策略：copy_missing / overwrite_if_missing 不覆盖已有；never_overwrite 已有则跳过
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 支持 JACHIN_HOME 环境变量
def _get_jachin_root() -> Path:
    import os
    home = os.environ.get("JACHIN_HOME")
    if home:
        return Path(home).expanduser().resolve()
    return Path.home() / ".jachin"


def _find_manifest(extracted_dir: Path) -> Path | None:
    """查找 config/manifest.yaml，支持根目录或 payload/ 嵌套结构"""
    candidates = [
        extracted_dir / "config" / "manifest.yaml",
        extracted_dir / "payload" / "config" / "manifest.yaml",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _should_write(target: Path, merge: str) -> bool:
    """根据 merge 策略判断是否应写入（不覆盖已有）"""
    if not target.exists():
        return True
    merge_lower = (merge or "overwrite_if_missing").lower()
    if merge_lower == "never_overwrite":
        return False
    # copy_missing, overwrite_if_missing: 目标存在则不写
    return False


def _copy_file(src: Path, dest: Path, merge: str) -> bool:
    """复制单个文件，按 merge 策略。返回是否执行了写入"""
    if not src.exists() or not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _should_write(dest, merge):
        shutil.copy2(src, dest)
        return True
    return False


def _copy_dir_recursive(src_dir: Path, dest_dir: Path, merge: str) -> int:
    """递归复制目录，按 merge 策略仅写入目标不存在的文件。返回写入文件数"""
    written = 0
    if not src_dir.exists() or not src_dir.is_dir():
        return 0
    for src_path in src_dir.rglob("*"):
        if src_path.is_file():
            rel = src_path.relative_to(src_dir)
            dest_path = dest_dir / rel
            if _copy_file(src_path, dest_path, merge):
                written += 1
    return written


def write_config_from_package(extracted_dir: Path, item_id: str = "") -> int:
    """
    按包内 config/manifest.yaml 将配置写出到 ~/.jachin/config/。

    Args:
        extracted_dir: 解压后的包根目录（含 plugin.json、config/ 等）
        item_id: 可选，用于日志

    Returns:
        成功写出的文件数量，0 表示无 manifest 或无需写出
    """
    manifest_path = _find_manifest(extracted_dir)
    if not manifest_path:
        return 0

    try:
        import yaml
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(
            "[ConfigWriteout] manifest 解析失败 item_id=%s path=%s: %s",
            item_id, manifest_path, e,
        )
        return 0

    if not raw or not isinstance(raw, dict):
        return 0

    writes = raw.get("writes")
    if not writes or not isinstance(writes, list):
        return 0

    output_root = _get_jachin_root()
    output_root_str = str(raw.get("output_root") or "~/.jachin").strip()
    if output_root_str and output_root_str != "~/.jachin":
        output_root = Path(output_root_str).expanduser().resolve()

    # 包内 config 根：与 manifest 同目录
    config_base = manifest_path.parent
    # 若 manifest 在 payload/config/，则 config_base 为 payload/config；源文件与 path 同结构
    # 若 manifest 在 config/，则 config_base 为 config；源文件为 config/xxx
    # writes 中的 path 如 config/skills/xxx，源文件在包内为 config_base 的父级 + path，或 config_base 的兄弟
    # 实际上 path 是相对 output_root 的，源文件在包内可能是 config/skills/xxx（与 path 相同）
    # 包结构：config/manifest.yaml, config/skills/com.jachin.xx/bi_daily_report.yaml
    # 所以源路径 = extracted_dir / path（因为 path 含 config/ 前缀）
    # 若 manifest 在 payload/config/，则 config 内容在 payload/config/ 下，path 是 config/skills/xxx
    # 源文件 = payload/config/skills/xxx = config_base / "skills" / ... = config_base.parent 不对
    # path 是 "config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml"
    # 若 config_base = payload/config，则 path 去掉 "config/" 得到 "skills/..."
    # 源 = config_base / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml"
    # 即 config_base / path[len("config/"):] 若 path 以 config/ 开头
    # 或 源 = extracted_dir / path，若包根有 config/ 目录
    # 统一：源 = extracted_dir / path，因为 extract 后包根可能有 config/ 子目录
    # 或 若 manifest 在 payload/config/，包根有 payload/，则 extracted_dir/payload/config/ 存在
    # path 为 config/skills/xxx，所以 源 = extracted_dir / path 需要 extracted_dir 下有 config/
    # 若 zip 根是 plugin.json, main.wasm, config/manifest.yaml，则 extracted_dir 下有 config/
    # 若 zip 根是 payload/，则 extracted_dir 下有 payload/，config 在 payload/config/
    # 此时 path config/skills/xxx 的源应在 payload/config/skills/xxx
    # 即：若 config_base 是 extracted_dir/config，则 源 = extracted_dir / path
    # 若 config_base 是 extracted_dir/payload/config，则 源 = config_base.parent.parent / path 不对
    # 简单：源 = config_base / path 去掉 "config/" 后的部分
    # 即 rel = path if path.startswith("config/") else path; rel = rel[8:]  # len("config/")
    # 源 = config_base / rel
    # 例如 path = "config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml"
    # rel = "skills/com.jachin.bi.daily_report/bi_daily_report.yaml"
    # 源 = config_base / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml"
    # config_base 可能是 extracted_dir/config 或 extracted_dir/payload/config
    # 两种情况下 config_base 下都有 "skills" 子目录（按包结构）
    # 所以 源 = config_base / rel 其中 rel = path[8:] if path.startswith("config/") else path

    total_written = 0
    for entry in writes:
        if not isinstance(entry, dict):
            continue
        path_str = entry.get("path")
        if not path_str or not isinstance(path_str, str):
            continue
        path_str = path_str.strip().lstrip("/")
        entry_type = (entry.get("type") or "file").lower()
        merge = (entry.get("merge") or "overwrite_if_missing").lower()

        # 源路径：包内 config/ 下的相对路径
        if path_str.startswith("config/"):
            rel_in_config = path_str[7:]  # len("config/")
        else:
            rel_in_config = path_str
        src_path = config_base / rel_in_config
        dest_path = output_root / path_str

        if entry_type == "directory":
            if src_path.exists() and src_path.is_dir():
                n = _copy_dir_recursive(src_path, dest_path, merge)
                total_written += n
                if n and item_id:
                    logger.debug(
                        "[ConfigWriteout] 写出目录 item_id=%s path=%s count=%d",
                        item_id, path_str, n,
                    )
        else:
            if _copy_file(src_path, dest_path, merge):
                total_written += 1
                if item_id:
                    logger.debug(
                        "[ConfigWriteout] 写出文件 item_id=%s path=%s",
                        item_id, path_str,
                    )

    if total_written and item_id:
        logger.info(
            "[ConfigWriteout] 已写出 %d 个配置到 ~/.jachin item_id=%s",
            total_written, item_id,
        )
    return total_written
