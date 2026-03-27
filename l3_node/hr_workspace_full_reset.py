"""
与 scripts/reset_hr_recruitment_all.py 一致的「一键清空招聘」逻辑，供 CLI 与 Lark 硬指令共用。

**请先停止** L3 / 招聘调度 / Boss 浏览器自动化，避免文件锁导致删除不完整。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

EmitFn = Optional[Callable[[str], None]]


def _e(msg: str, emit: EmitFn) -> None:
    if emit:
        emit(msg)


def jachin_root() -> Path:
    h = (os.environ.get("JACHIN_HOME") or "").strip()
    if h:
        return Path(h).expanduser().resolve()
    return (Path.home() / ".jachin").resolve()


def hr_data_root() -> Path:
    root = jachin_root()
    custom = (os.environ.get("JACHIN_HR_DATA_ROOT") or "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        return p if p.is_absolute() else (root / custom).resolve()
    return (root / "workspace" / "hr_recruitment").resolve()


def hr_analysis_root() -> Path:
    custom = (os.environ.get("JACHIN_HR_ANALYSIS_OUTPUT") or "").strip()
    root = jachin_root()
    if custom:
        p = Path(custom).expanduser().resolve()
        return p if p.is_absolute() else (root / p).resolve()
    return (root / "workspace" / "hr_analysis").resolve()


def hr_resume_root() -> Path:
    root = jachin_root()
    custom = (os.environ.get("JACHIN_HR_RESUME_ROOT") or "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        return p if p.is_absolute() else (root / custom).resolve()
    return (root / "workspace" / "hr_resumes").resolve()


def client_volumes_root() -> Path:
    return (jachin_root() / "client_volumes").resolve()


def _rm_tree(path: Path, *, dry_run: bool, emit: EmitFn) -> bool:
    if not path.exists():
        return True
    try:
        if dry_run:
            _e(f"  [dry-run] 将删除: {path}", emit)
            return True
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink()
        return True
    except OSError as e:
        logger.warning("[HR reset] delete failed (will retry possible): %s -> %s", path, e)
        _e(f"  [WARN] 删除失败（将重试）: {path} -> {e}", emit)
        return False


def _unlink_if_exists(path: Path, *, dry_run: bool, emit: EmitFn) -> None:
    if not path.is_file():
        return
    if dry_run:
        _e(f"  [dry-run] 将删除文件: {path}", emit)
        return
    try:
        path.unlink()
    except OSError as e:
        logger.warning("[HR reset] unlink failed: %s -> %s", path, e)
        _e(f"  [WARN] 无法删除文件: {path} -> {e}", emit)


def _clear_hr_loose_files_at_data_root(hr_root: Path, *, dry_run: bool, emit: EmitFn) -> None:
    names = (
        "task_plan.md",
        "progress.md",
        "lark_tasks.json",
        "jd_to_publish.json",
    )
    for name in names:
        _unlink_if_exists(hr_root / name, dry_run=dry_run, emit=emit)


def _prune_hr_workflow_states(memory_dir: Path, *, dry_run: bool, emit: EmitFn) -> int:
    p = memory_dir / "workflow_states.json"
    if not p.is_file():
        return 0
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[HR reset] workflow_states.json parse: %s", e)
        _e(f"  [WARN] 无法解析 workflow_states.json: {e}", emit)
        return 0
    if not isinstance(raw, dict):
        return 0
    hr_keys = [k for k in raw if "hr_recruitment" in str(k)]
    if not hr_keys:
        return 0
    if dry_run:
        _e(f"  [dry-run] 将移除 workflow 键: {hr_keys}", emit)
        return len(hr_keys)
    for k in hr_keys:
        del raw[k]
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(hr_keys)


def _clear_lark_sessions(jachin: Path, *, dry_run: bool, emit: EmitFn) -> None:
    p = jachin / "l3_lark_sessions.json"
    if not p.is_file():
        return
    if dry_run:
        _e(f"  [dry-run] 将清空: {p}", emit)
        return
    p.write_text("{}", encoding="utf-8")


def _clear_audit(memory_dir: Path, *, dry_run: bool, emit: EmitFn) -> None:
    for name in ("hr_recruitment_audit.jsonl", "hr_recruitment_audit.jsonl.bak"):
        _unlink_if_exists(memory_dir / name, dry_run=dry_run, emit=emit)


def _clear_pointer(*, keep_lark_chat: bool, dry_run: bool, emit: EmitFn) -> None:
    try:
        from l3_node.local_memory import clear_all_hr_recruitment_pointer
    except ImportError as e:
        logger.warning("[HR reset] clear_all_hr_recruitment_pointer import: %s", e)
        _e(f"  [WARN] 无法导入 clear_all_hr_recruitment_pointer: {e}，改写字指针文件", emit)
        ptr = jachin_root() / "memory" / "hr_recruitment_workflow_pointer.json"
        if dry_run:
            _e(f"  [dry-run] 将重置: {ptr}", emit)
            return
        ptr.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {"jobs": [], "updated_at": time.time()}
        if keep_lark_chat and ptr.is_file():
            try:
                prev = json.loads(ptr.read_text(encoding="utf-8"))
                if isinstance(prev, dict) and (prev.get("lark_chat_id") or "").strip():
                    data["lark_chat_id"] = (prev.get("lark_chat_id") or "").strip()
            except Exception:
                pass
        ptr.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if dry_run:
        _e(
            f"  [dry-run] 将调用 clear_all_hr_recruitment_pointer(keep_lark_chat={keep_lark_chat})",
            emit,
        )
        return
    clear_all_hr_recruitment_pointer(keep_lark_chat=keep_lark_chat)


def _list_job_subdir_names(hr_root: Path) -> list[str]:
    if not hr_root.is_dir():
        return []
    return [p.name for p in hr_root.iterdir() if p.is_dir() and not p.name.startswith(".")]


def _delete_job_dirs_via_loader(*, dry_run: bool, emit: EmitFn) -> tuple[int, list[str]]:
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        from l3_node.hr_loader import hr_delete_all_hr_recruitment_workspace_dirs
    except ImportError as e:
        logger.warning("[HR reset] hr_loader import: %s", e)
        _e(f"  [WARN] 无法导入 hr_loader: {e}", emit)
        return 0, []
    if dry_run:
        hr_root = hr_data_root()
        names = _list_job_subdir_names(hr_root)
        _e(
            f"  [dry-run] 将删除岗位子目录 {len(names)} 个: {names[:20]}{'...' if len(names) > 20 else ''}",
            emit,
        )
        return len(names), names
    return hr_delete_all_hr_recruitment_workspace_dirs()


def _delete_job_dirs_via_repo_plugin(*, dry_run: bool, emit: EmitFn) -> tuple[int, list[str]]:
    plug = _REPO_ROOT / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
    if not (plug / "tools" / "hr_data_paths.py").is_file():
        return 0, []
    if dry_run:
        hr_root = hr_data_root()
        names = _list_job_subdir_names(hr_root)
        _e(f"  [dry-run] 仓库插件将删岗位子目录 {len(names)} 个", emit)
        return len(names), names
    prev = sys.path.copy()
    try:
        sys.path.insert(0, str(plug))
        from tools.hr_data_paths import delete_all_hr_position_directories

        return delete_all_hr_position_directories()
    except ImportError as e:
        logger.warning("[HR reset] repo plugin hr_data_paths: %s", e)
        _e(f"  [WARN] 仓库插件 hr_data_paths 不可用: {e}", emit)
        return 0, []
    finally:
        sys.path = prev


def _delete_all_job_dirs(*, dry_run: bool, emit: EmitFn) -> tuple[int, list[str]]:
    n, names = _delete_job_dirs_via_loader(dry_run=dry_run, emit=emit)
    if n == 0 and not dry_run:
        hr_root = hr_data_root()
        if _list_job_subdir_names(hr_root):
            _e(
                "  [WARN] loader 未删任何目录，尝试仓库内插件 delete_all_hr_position_directories …",
                emit,
            )
            n, names = _delete_job_dirs_via_repo_plugin(dry_run=dry_run, emit=emit)
    return n, names


def _sweep_hr_data_root_subdirs(hr_root: Path, *, dry_run: bool, emit: EmitFn) -> int:
    n = 0
    if not hr_root.is_dir():
        return 0
    for sub in list(hr_root.iterdir()):
        if sub.name.startswith("."):
            continue
        if sub.is_dir():
            if _rm_tree(sub, dry_run=dry_run, emit=emit):
                n += 1
    return n


def _clear_scheduler_memory_if_loaded(*, dry_run: bool, emit: EmitFn) -> None:
    if dry_run:
        _e("  [dry-run] 将清空招聘调度器内存与 rec_* 定时任务", emit)
        return
    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        rs = get_recruitment_scheduler()
        if rs is not None and hasattr(rs, "clear_all_recruitment_scheduler_memory"):
            rs.clear_all_recruitment_scheduler_memory()
            _e("  [OK] 调度器内存与 rec_* 任务已清空", emit)
    except Exception as e:
        logger.warning("[HR reset] scheduler clear failed: %s", e)
        _e(f"  [WARN] 调度器清空失败（可继续删数据）: {e}", emit)


def run_full_hr_recruitment_reset_round(
    *,
    dry_run: bool = False,
    keep_lark_chat: bool = False,
    keep_lark_sessions: bool = False,
    clear_client_volumes: bool = True,
    clear_hr_resume_root: bool = False,
    clear_hr_analysis: bool = True,
    emit: EmitFn = None,
) -> dict[str, int | str]:
    """
    执行一轮与 reset_hr_recruitment_all 一致的清空（含调度器内存清空）。
    emit 为 None 时不打印逐步信息（仍打 logger）。
    """
    jachin = jachin_root()
    hr_root = hr_data_root()
    memory_dir = jachin / "memory"
    report: dict[str, int | str] = {}

    _e(f"JACHIN_HOME      = {jachin}", emit)
    _e(f"HR 数据根        = {hr_root}", emit)
    _e(f"透析输出根       = {hr_analysis_root()}", emit)
    _e(f"client_volumes   = {client_volumes_root()}", emit)
    _e("", emit)

    _clear_scheduler_memory_if_loaded(dry_run=dry_run, emit=emit)

    n_del, names = _delete_all_job_dirs(dry_run=dry_run, emit=emit)
    report["job_dirs_loader"] = n_del
    if names and not dry_run:
        _e(
            f"  [OK] loader 已删 {n_del} 个目录: {names[:12]}{'...' if len(names) > 12 else ''}",
            emit,
        )

    extra = _sweep_hr_data_root_subdirs(hr_root, dry_run=dry_run, emit=emit)
    report["job_dirs_swept"] = extra
    if extra:
        _e(f"  [OK] 兜底扫描删除子目录: {extra} 个", emit)

    _clear_hr_loose_files_at_data_root(hr_root, dry_run=dry_run, emit=emit)
    _e("  [OK] 已处理 hr_recruitment 根目录松散文件（task_plan / lark_tasks 等）", emit)

    _clear_pointer(keep_lark_chat=keep_lark_chat, dry_run=dry_run, emit=emit)
    _e(f"  [OK] HR 指针已重置 (keep_lark_chat={keep_lark_chat})", emit)

    nk = _prune_hr_workflow_states(memory_dir, dry_run=dry_run, emit=emit)
    report["workflow_states_removed"] = nk
    if nk:
        _e(f"  [OK] 已移除含 hr_recruitment 的 workflow 条目: {nk} 条", emit)

    _clear_audit(memory_dir, dry_run=dry_run, emit=emit)
    _e("  [OK] 审计日志 hr_recruitment_audit.jsonl 已清理", emit)

    if not keep_lark_sessions:
        _clear_lark_sessions(jachin, dry_run=dry_run, emit=emit)
        _e("  [OK] l3_lark_sessions.json 已清空", emit)
    else:
        _e("  [-] 保留 l3_lark_sessions.json (--keep-lark-sessions)", emit)

    if clear_hr_analysis:
        ar = hr_analysis_root()
        if ar.exists():
            ok = _rm_tree(ar, dry_run=dry_run, emit=emit)
            _e(f"  [{'OK' if ok else 'WARN'}] 透析输出目录: {ar}", emit)
        else:
            _e(f"  [-] 透析输出目录不存在，跳过: {ar}", emit)
    else:
        _e("  [-] 跳过透析输出目录 (--no-clear-hr-analysis)", emit)

    if clear_hr_resume_root:
        rr = hr_resume_root()
        if rr.exists():
            ok = _rm_tree(rr, dry_run=dry_run, emit=emit)
            _e(f"  [{'OK' if ok else 'WARN'}] hr_resumes 根目录: {rr}", emit)
        else:
            _e(f"  [-] hr_resumes 不存在: {rr}", emit)
    else:
        _e("  [-] 保留 hr_resumes 根目录（未传 --clear-hr-resume-root）", emit)

    if clear_client_volumes:
        vol = client_volumes_root()
        removed = 0
        if vol.is_dir():
            for sub in list(vol.iterdir()):
                if sub.name in ("bi_data",) or sub.name.startswith("."):
                    continue
                if sub.is_dir():
                    if _rm_tree(sub, dry_run=dry_run, emit=emit):
                        removed += 1
                elif sub.is_file():
                    _unlink_if_exists(sub, dry_run=dry_run, emit=emit)
                    removed += 1
            _e(f"  [OK] client_volumes 已清理（保留 bi_data）子项约 {removed} 个", emit)
        else:
            _e(f"  [-] client_volumes 不存在: {vol}", emit)
    else:
        _e("  [-] 保留 client_volumes (--no-clear-client-volumes)", emit)

    return report


def run_full_hr_recruitment_reset_with_retries(
    *,
    max_rounds: int = 5,
    sleep_seconds: float = 2.0,
    dry_run: bool = False,
    keep_lark_chat: bool = False,
    keep_lark_sessions: bool = False,
    clear_client_volumes: bool = True,
    clear_hr_resume_root: bool = False,
    clear_hr_analysis: bool = True,
    emit: EmitFn = None,
) -> tuple[bool, dict[str, int | str], list[str], list[str]]:
    """
    多轮执行直到 hr_recruitment 根下无岗位子目录与松散文件，或达到 max_rounds。
    返回 (是否干净, 最后一轮 report, leftover_dirs, leftover_files)。
    """
    last_report: dict[str, int | str] = {}
    for round_i in range(1, max(1, max_rounds) + 1):
        last_report = run_full_hr_recruitment_reset_round(
            dry_run=dry_run,
            keep_lark_chat=keep_lark_chat,
            keep_lark_sessions=keep_lark_sessions,
            clear_client_volumes=clear_client_volumes,
            clear_hr_resume_root=clear_hr_resume_root,
            clear_hr_analysis=clear_hr_analysis,
            emit=emit,
        )
        root = hr_data_root()
        leftover_dirs: list[str] = []
        leftover_files: list[str] = []
        if root.is_dir():
            leftover_dirs = [
                p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
            ]
            leftover_files = [
                p.name for p in root.iterdir() if p.is_file() and not p.name.startswith(".")
            ]
        if not leftover_dirs and not leftover_files:
            return True, last_report, [], []
        if dry_run:
            return True, last_report, leftover_dirs, leftover_files
        if round_i < max_rounds:
            time.sleep(max(0.0, sleep_seconds))
    return False, last_report, leftover_dirs, leftover_files
