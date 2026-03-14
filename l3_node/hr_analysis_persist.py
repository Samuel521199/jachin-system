"""
HR 透析镜分析结果持久化

将 result.text 写入可配置的 output_dir（限制在项目根或 ~/.jachin/ 下）。
供 L3 HTTP API 与 Agent 自然语言调用共用。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("l3_node")

from l3_node.paths import get_app_root
_PROJ_ROOT = get_app_root()
_JACHIN_ROOT = Path.home() / ".jachin"

_last_saved_path: str | None = None

_HR_ANALYZER_MAP = {
    "jpp:com.jachin.hr.analyzer": ("hr-analyzer", "hr_analysis_output"),
    "jpp:com.jachin.hr.analyzer2": ("hr-analyzer2", "hr_analysis_output_2"),
    "jpp:com.jachin.hr.analyzer3": ("hr-analyzer3", "hr_analysis_output_3"),
    "jpp:com.jachin.hr.analyzer4": ("hr-analyzer4", "hr_analysis_output_4"),
}


def _resolve_safe_dir(raw: str, base: Path, use_absolute_path: bool = False) -> Path | None:
    """
    将路径解析为绝对路径。
    - use_absolute_path=False：必须位于项目根或 ~/.jachin/ 下
    - use_absolute_path=True：必须位于用户主目录下，或与主目录同盘符（如 C:\\analisy）
    返回 None 表示越界或无效。防止 path traversal 写入系统敏感目录。
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    else:
        p = p.resolve()
    proj = _PROJ_ROOT.resolve()
    jachin = _JACHIN_ROOT.resolve()
    home = Path.home().resolve()
    try:
        p = p.resolve()

        def _under(root: Path, child: Path) -> bool:
            try:
                child.relative_to(root)
                return True
            except ValueError:
                return False

        if use_absolute_path:
            if _under(home, p):
                return p
            try:
                home_drive = (home.drive or "").upper()
                child_drive = (p.drive or "").upper()
                # 允许与主目录同盘符的路径（如 C:\\analisy 当 home 为 C:\\Users\\xxx）
                if home_drive and child_drive and home_drive == child_drive:
                    return p
                # 允许 D:、E: 等其他盘符的绝对路径（用户数据盘）
                if child_drive and child_drive != home_drive:
                    return p
            except (AttributeError, OSError):
                pass
        else:
            if _under(proj, p) or _under(jachin, p):
                return p
    except (OSError, RuntimeError):
        pass
    return None


def persist_hr_analysis_result(
    skill_id: str,
    result_text: str,
    input_data: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str | None:
    """
    将 HR 透析镜分析结果写入可配置的 output_dir（限制在项目根或 ~/.jachin/ 下）。
    返回写入的文件路径，失败返回 None。
    """
    global _last_saved_path
    _last_saved_path = None
    sid = (skill_id or "").strip()
    if sid not in _HR_ANALYZER_MAP:
        return None
    if not result_text or not isinstance(result_text, str):
        return None
    resume_fn = (input_data.get("resume_filename") or "zhangsan_resume.md").replace(".md", "")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{resume_fn}_analysis_{ts}.md"

    config = config or {}
    use_abs = config.get("output_dir_use_absolute") in (True, "true", "1", "yes") or config.get("use_absolute_path") in (True, "true", "1", "yes")
    output_dir_cfg = (config.get("output_dir") or "data/hr_analysis").strip()
    out_dirs: list[Path] = []
    custom_dir = _resolve_safe_dir(output_dir_cfg, _PROJ_ROOT, use_absolute_path=use_abs)
    if custom_dir:
        out_dirs.append(custom_dir)
        logger.info("[HR Persist] 使用配置输出目录 output_dir=%s use_absolute=%s resolved=%s", output_dir_cfg, use_abs, custom_dir)
    else:
        logger.debug("[HR Persist] 配置路径无效或越界，使用默认目录 output_dir=%s use_absolute=%s", output_dir_cfg, use_abs)
    # 兜底：项目 data/hr_analysis 与 volume
    out_dirs.append(_PROJ_ROOT / "data" / "hr_analysis")
    _, vol_name = _HR_ANALYZER_MAP[sid]
    out_dirs.append(_JACHIN_ROOT / "volumes" / vol_name)
    seen: set[str] = set()
    written_path: str | None = None
    for out_dir in out_dirs:
        try:
            out_dir = out_dir.resolve()
            if str(out_dir) in seen:
                continue
            seen.add(str(out_dir))
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / filename
            out_file.write_text(result_text, encoding="utf-8")
            written_path = str(out_file.resolve())
            logger.info("[HR Persist] 分析报告已写入 path=%s", written_path)
            _last_saved_path = written_path
        except Exception as e:
            logger.warning("[HR Persist] 写入失败 dir=%s err=%s", out_dir, e)
    return written_path


def persist_hr_analysis_batch_item(
    skill_id: str,
    result_text: str,
    filename_stem: str,
    config: dict[str, Any] | None = None,
) -> str | None:
    """
    批量模式下，将单份分析结果写入 output_dir，命名为 {stem}_analysis.md。
    返回写入的文件路径，失败返回 None。
    """
    global _last_saved_path
    sid = (skill_id or "").strip()
    if sid not in _HR_ANALYZER_MAP:
        return None
    if not result_text or not isinstance(result_text, str):
        return None
    stem = (filename_stem or "unknown").replace(".md", "").replace(".txt", "").strip()
    if not stem:
        stem = "unknown"
    filename = f"{stem}_analysis.md"

    config = config or {}
    use_abs = config.get("output_dir_use_absolute") in (True, "true", "1", "yes") or config.get("use_absolute_path") in (True, "true", "1", "yes")
    output_dir_cfg = (config.get("output_dir") or "data/hr_analysis").strip()
    out_dirs: list[Path] = []
    custom_dir = _resolve_safe_dir(output_dir_cfg, _PROJ_ROOT, use_absolute_path=use_abs)
    if not custom_dir and "skills_repo" in output_dir_cfg and "plugin" in output_dir_cfg:
        # 收网调度器传入的 result 路径可能因编码/斜杠导致解析失败，显式按项目相对路径解析
        try:
            raw_norm = output_dir_cfg.replace("\\", "/").strip()
            idx = raw_norm.find("skills_repo")
            if idx >= 0:
                rel = raw_norm[idx:]
                cand = (_PROJ_ROOT / rel).resolve()
                if _resolve_safe_dir(str(cand), _PROJ_ROOT, use_absolute_path=False):
                    custom_dir = cand
                    logger.info("[HR Persist] 批量项使用 plugin 相对路径 output_dir=%s", cand)
        except Exception as e:
            logger.debug("[HR Persist] plugin 路径解析失败: %s", e)
    if custom_dir:
        out_dirs.append(custom_dir)
    out_dirs.append(_PROJ_ROOT / "data" / "hr_analysis")
    _, vol_name = _HR_ANALYZER_MAP[sid]
    out_dirs.append(_JACHIN_ROOT / "volumes" / vol_name)
    seen: set[str] = set()
    written_path: str | None = None
    for out_dir in out_dirs:
        try:
            out_dir = out_dir.resolve()
            if str(out_dir) in seen:
                continue
            seen.add(str(out_dir))
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / filename
            out_file.write_text(result_text, encoding="utf-8")
            written_path = str(out_file.resolve())
            logger.info("[HR Persist] 批量项已写入 path=%s", written_path)
            _last_saved_path = written_path
        except Exception as e:
            logger.warning("[HR Persist] 批量写入失败 dir=%s err=%s", out_dir, e)
    return written_path


def get_last_saved_path() -> str | None:
    """获取最近一次持久化的文件路径（供 API 响应使用）"""
    return _last_saved_path
