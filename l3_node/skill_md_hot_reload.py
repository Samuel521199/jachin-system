"""
SKILL.md 热重载（路线图 §六 P1）+ ReAct 中途 inline 对齐（§六 P2）

P1：每轮 ReAct 在 LLM 前从磁盘替换 HR 招聘 SKILL.md 注入段（可选关闭）。
P2：skill_evolver 等后台写入 SKILL.md 后 bump 世代 + 对当前前台 ReAct 上下文打
    `_skill_sop_dirty`，下一次 HOOK_BEFORE_LLM_THINK 前强制读盘并同步
    `_react_system_prompt_full`，无需等「自然轮次」外额外条件。

环境变量
--------
JACHIN_SKILL_MD_HOT_RELOAD=1       每轮读盘刷新（默认 1）
JACHIN_SKILL_MD_INLINE_ENABLE=1   P2 inline 打标/注册/notify（默认 1）
JACHIN_SKILL_MD_GENERIC_HOT_RELOAD=1  skills_repo / L1 缓存下任意 SKILL.md 写盘也触发 inline dirty（默认关）
"""
from __future__ import annotations

import logging
import os
import re
import threading
import weakref
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HR_SKILL_MD_BODY_START = "<!--JACHIN_HR_SKILL_MD_BODY-->"
HR_SKILL_MD_BODY_END = "<!--/JACHIN_HR_SKILL_MD_BODY-->"
GENERIC_SKILL_MD_START = "<!--JACHIN_GENERIC_SKILL_MD-->"
GENERIC_SKILL_MD_END = "<!--/JACHIN_GENERIC_SKILL_MD-->"

_lock = threading.Lock()
_hr_skill_inline_generation: int = 0
# run_id -> weakref(PipelineContext)；仅在有 HR 标记块的 ReAct 上注册
_inline_by_run_id: dict[str, weakref.ref] = {}


def skill_md_hot_reload_enabled() -> bool:
    return (os.environ.get("JACHIN_SKILL_MD_HOT_RELOAD") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def skill_md_inline_enabled() -> bool:
    return (os.environ.get("JACHIN_SKILL_MD_INLINE_ENABLE") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def skill_md_generic_hot_reload_enabled() -> bool:
    return (os.environ.get("JACHIN_SKILL_MD_GENERIC_HOT_RELOAD") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def get_hr_skill_inline_generation() -> int:
    with _lock:
        return _hr_skill_inline_generation


def bump_hr_skill_inline_generation() -> int:
    global _hr_skill_inline_generation
    with _lock:
        _hr_skill_inline_generation += 1
        g = _hr_skill_inline_generation
    logger.debug("[skill_md_hot_reload] HR skill inline generation -> %s", g)
    return g


def path_triggers_hr_skill_hot_inline(skill_path: Path) -> bool:
    """是否为 HR 招聘 SKILL.md（与 _load_hr_recruitment_skill_content 搜索域一致）。"""
    low = str(skill_path).replace("\\", "/").lower()
    if skill_path.name.lower() != "skill.md":
        return False
    return (
        "hr-recruitment" in low
        or "com.jachin.hr.recruitment" in low
        or skill_path.parent.name.lower() == "hr-recruitment"
    )


def path_triggers_skill_disk_hot_inline(skill_path: Path) -> bool:
    """HR 或（开启 generic 时）skills_repo / L1 缓存下的 SKILL.md。"""
    if path_triggers_hr_skill_hot_inline(skill_path):
        return True
    if not skill_md_generic_hot_reload_enabled():
        return False
    low = str(skill_path).replace("\\", "/").lower()
    if skill_path.name.lower() != "skill.md":
        return False
    return "/skills_repo/" in low or "/.jachin/skills/" in low


def register_react_ctx_for_skill_inline(run_id: str, ctx: Any) -> None:
    if not skill_md_inline_enabled() or not run_id:
        return
    sp = getattr(ctx, "system_prompt", "") or ""
    if HR_SKILL_MD_BODY_START not in sp and not bool(
        (getattr(ctx, "metadata", None) or {}).get("_skill_md_generic_watch")
    ):
        return

    rid = str(run_id).strip()

    def _cleanup(wr: weakref.ReferenceType) -> None:
        with _lock:
            if _inline_by_run_id.get(rid) is wr:
                _inline_by_run_id.pop(rid, None)

    with _lock:
        _inline_by_run_id[rid] = weakref.ref(ctx, _cleanup)


def unregister_react_ctx_for_skill_inline(run_id: str) -> None:
    rid = (run_id or "").strip()
    if not rid:
        return
    with _lock:
        _inline_by_run_id.pop(rid, None)


def notify_skill_md_changed_from_disk_write(skill_path: Path) -> None:
    """
    SKILL.md 写入磁盘后调用（如 skill_evolver、上游同步）。
    对 HR 招聘路径：bump 世代并对已注册 ReAct 上下文设置 _skill_sop_dirty。
    """
    if not skill_md_inline_enabled():
        return
    try:
        p = skill_path if isinstance(skill_path, Path) else Path(skill_path)
    except Exception:
        return
    if not path_triggers_skill_disk_hot_inline(p):
        return
    bump_hr_skill_inline_generation()
    with _lock:
        refs = list(_inline_by_run_id.items())
    for rid, r in refs:
        c = r()
        if c is None:
            continue
        try:
            sp = getattr(c, "system_prompt", "") or ""
            md = getattr(c, "metadata", None) or {}
            if HR_SKILL_MD_BODY_START in sp or md.get("_skill_md_generic_watch"):
                c.metadata["_skill_sop_dirty"] = True
                logger.info(
                    "[skill_md_hot_reload] inline dirty run_id=%s path=%s",
                    (rid or "")[:12],
                    p,
                )
        except Exception as e:
            logger.debug("[skill_md_hot_reload] inline dirty failed run_id=%s: %s", rid, e)


def refresh_hr_skill_md_body_in_system_prompt(
    system_prompt: str,
    *,
    force_disk_read: bool = False,
) -> str:
    """
    若 prompt 中含 HR_SKILL_MD 标记块，则用磁盘最新 SKILL.md 正文替换块内内容。
    force_disk_read：为 True 时每轮读盘（或与 hot_reload 打开时）；P2 在 dirty/gen 时强制 True。
    """
    if not force_disk_read and not skill_md_hot_reload_enabled():
        return system_prompt
    start, end = HR_SKILL_MD_BODY_START, HR_SKILL_MD_BODY_END
    if start not in system_prompt or end not in system_prompt:
        return system_prompt
    from l3_node.agent_core import _load_hr_recruitment_skill_content

    fresh = _load_hr_recruitment_skill_content()
    if fresh is None:
        return system_prompt
    pattern = re.escape(start) + r"[\s\S]*?" + re.escape(end)
    replacement = f"{start}\n{fresh}\n{end}"
    new_prompt, n = re.subn(pattern, replacement, system_prompt, count=1)
    return new_prompt if n else system_prompt


def discover_skill_md_paths(*, limit: int = 8) -> list[str]:
    """skills_repo 下 SKILL.md（generic 热重载监视列表）。"""
    import os
    from pathlib import Path

    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin")
    proj = Path(__file__).resolve().parents[1]
    bases = [
        proj / "skills_repo",
        root / "l3_skill_cache",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for base in bases:
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("SKILL.md"):
                if not p.is_file():
                    continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                if path_triggers_hr_skill_hot_inline(p):
                    continue
                out.append(key)
                if len(out) >= max(1, limit):
                    return out
        except OSError:
            continue
    return out


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip()
    return text


def build_generic_skill_md_block(paths: list[str], *, max_chars: int = 6000) -> str:
    parts: list[str] = []
    for raw in paths[:8]:
        try:
            p = Path(raw)
            body = _strip_frontmatter(p.read_text(encoding="utf-8")).strip()
            if not body:
                continue
            parts.append(f"### {p.parent.name}\n{body[:2000]}")
        except OSError:
            continue
    if not parts:
        return ""
    block = f"{GENERIC_SKILL_MD_START}\n" + "\n\n".join(parts) + f"\n{GENERIC_SKILL_MD_END}"
    if len(block) > max_chars:
        return block[: max_chars - 3] + "…"
    return block


def refresh_generic_skill_md_in_system_prompt(system_prompt: str, paths: list[str]) -> str:
    fresh = build_generic_skill_md_block(paths)
    if not fresh:
        return system_prompt
    pat = re.escape(GENERIC_SKILL_MD_START) + r"[\s\S]*?" + re.escape(GENERIC_SKILL_MD_END)
    if re.search(pat, system_prompt or ""):
        return re.sub(pat, fresh, system_prompt, count=1)
    return (system_prompt or "") + "\n\n" + fresh + "\n"


def apply_skill_md_hot_reload_to_react_ctx(ctx: Any) -> None:
    """HR + generic SKILL.md 热重载（每轮 / inline dirty）。"""
    apply_hr_skill_md_hot_reload_to_react_ctx(ctx)
    if not ctx.metadata.get("_skill_md_generic_watch"):
        return
    gen = get_hr_skill_inline_generation()
    dirty = bool(ctx.metadata.get("_skill_sop_dirty"))
    try:
        seen = int(ctx.metadata.get("_hr_skill_md_gen_seen") or 0)
    except (TypeError, ValueError):
        seen = 0
    if not (skill_md_hot_reload_enabled() or dirty or seen < gen):
        return
    paths = ctx.metadata.get("_skill_md_watched_paths")
    if not isinstance(paths, list) or not paths:
        paths = discover_skill_md_paths()
        ctx.metadata["_skill_md_watched_paths"] = paths
    if not paths:
        return
    ctx.system_prompt = refresh_generic_skill_md_in_system_prompt(
        getattr(ctx, "system_prompt", "") or "",
        paths,
    )
    full = ctx.metadata.get("_react_system_prompt_full")
    if isinstance(full, str):
        ctx.metadata["_react_system_prompt_full"] = refresh_generic_skill_md_in_system_prompt(
            full, paths
        )


def apply_hr_skill_md_hot_reload_to_react_ctx(ctx: Any) -> None:
    """
    每个 ReAct 迭代在 HOOK_BEFORE_LLM_THINK 之前调用：
    - P1：hot_reload 开则每轮读盘；
    - P2：_skill_sop_dirty 或 inline 世代落后则强制读盘，并同步冻结的 _react_system_prompt_full。
    """
    sp = getattr(ctx, "system_prompt", "") or ""
    if HR_SKILL_MD_BODY_START not in sp:
        try:
            ctx.metadata["_hr_skill_md_gen_seen"] = get_hr_skill_inline_generation()
        except Exception:
            pass
        return

    gen = get_hr_skill_inline_generation()
    dirty = bool(ctx.metadata.get("_skill_sop_dirty"))
    try:
        seen = int(ctx.metadata.get("_hr_skill_md_gen_seen") or 0)
    except (TypeError, ValueError):
        seen = 0
    need_disk = bool(skill_md_hot_reload_enabled() or dirty or seen < gen)
    if not need_disk:
        return

    ctx.system_prompt = refresh_hr_skill_md_body_in_system_prompt(
        getattr(ctx, "system_prompt", "") or "",
        force_disk_read=True,
    )
    full = ctx.metadata.get("_react_system_prompt_full")
    if isinstance(full, str) and HR_SKILL_MD_BODY_START in full:
        ctx.metadata["_react_system_prompt_full"] = refresh_hr_skill_md_body_in_system_prompt(
            full,
            force_disk_read=True,
        )
    try:
        ctx.metadata["_skill_sop_dirty"] = False
        ctx.metadata["_hr_skill_md_gen_seen"] = gen
    except Exception:
        pass
