"""
Skill 同步保护卫士（Skill Sync Guard）

当 L3 从 L1 拉取某个已订阅 Skill 的新版本时，若本地版本已经过自动进化
（diverged_from_upstream=True），不能直接覆盖，而是走以下策略：

  安全：本地无进化 → 直接同步（透传给调用方）
  合并：本地有进化 → 3-way smart merge
    base  = 上次从 L1 同步的快照（upstream_snapshots/{version}.md）
    local = 当前本地版本（含进化规则）
    new   = L1 新版本
    merge 策略：
      - Frontmatter：取 new（接受上游工具链/版本更新）
      - Rules / Persona / Examples 等正文段落：保留 local 中比 base 多出的行
        （即保留本地进化内容），同时接受 new 中比 base 多出的行
      - 冲突（local 和 new 都改了同一段）：保留 local，在 ## ⚠️上游更新 段落注明
  分叉告警：无论是否成功 merge，均写入 divergence 记录到进化日志

该模块由 L3 sync 层（mcp_sync / config_writeout 等）在安装/更新 Skill 时调用。
成功写盘路径须与 skill_evolver 一致触发 HR inline：`notify_skill_md_changed_from_disk_write`（见 skill_md_hot_reload）。

环境变量
--------
JACHIN_SKILL_SYNC_AUTO_MERGE=1    是否自动 smart merge（默认 1=开启）
JACHIN_SKILL_SYNC_FORCE_OVERWRITE=1  强制覆盖本地进化（危险，默认关）
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _notify_skill_md_hot_reload_after_write(skill_path: Path) -> None:
    """与 `skill_evolver` 一致：HR SKILL.md 写盘后触发 inline 世代 + `_skill_sop_dirty`（非 HR 路径为 no-op）。"""
    try:
        from l3_node.skill_md_hot_reload import notify_skill_md_changed_from_disk_write

        notify_skill_md_changed_from_disk_write(skill_path)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _auto_merge_enabled() -> bool:
    import os
    return (os.environ.get("JACHIN_SKILL_SYNC_AUTO_MERGE") or "1").strip() not in ("0", "false", "no")


def _force_overwrite() -> bool:
    import os
    return (os.environ.get("JACHIN_SKILL_SYNC_FORCE_OVERWRITE") or "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------

@dataclass
class SyncSafetyResult:
    """check_before_sync_overwrite 的返回值。"""
    safe_to_overwrite: bool     # True = 可直接覆盖
    has_local_evolution: bool   # 本地是否已进化
    local_evolution_count: int
    local_version: str
    upstream_version: str
    merge_recommended: bool     # 建议 smart merge
    reason: str


@dataclass
class SyncHandleResult:
    """handle_upstream_update 的返回值。"""
    action: str                 # overwritten / merged / skipped / forced
    skill_name: str
    new_local_path: str
    upstream_snapshot_path: str
    merge_conflicts: list[str]  # 合并时的冲突段落名称
    message: str


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def check_before_sync_overwrite(
    skill_name: str,
    new_upstream_version: str = "",
) -> SyncSafetyResult:
    """
    在 L1 同步覆盖 SKILL.md 之前调用。
    返回 SyncSafetyResult 告诉调用方是否安全覆盖。
    """
    from l3_node.autonomy.skill_evolver import find_skill_md_path, load_manifest

    skill_path = find_skill_md_path(skill_name)
    if skill_path is None:
        return SyncSafetyResult(
            safe_to_overwrite=True,
            has_local_evolution=False,
            local_evolution_count=0,
            local_version="",
            upstream_version=new_upstream_version,
            merge_recommended=False,
            reason="skill not found locally, safe to create",
        )

    manifest = load_manifest(skill_path, skill_name)

    if not manifest.diverged_from_upstream or manifest.local_evolution_count == 0:
        return SyncSafetyResult(
            safe_to_overwrite=True,
            has_local_evolution=False,
            local_evolution_count=0,
            local_version=manifest.local_version,
            upstream_version=new_upstream_version,
            merge_recommended=False,
            reason="no local evolution, safe to overwrite",
        )

    return SyncSafetyResult(
        safe_to_overwrite=_force_overwrite(),
        has_local_evolution=True,
        local_evolution_count=manifest.local_evolution_count,
        local_version=manifest.local_version,
        upstream_version=new_upstream_version,
        merge_recommended=_auto_merge_enabled(),
        reason=(
            f"skill has {manifest.local_evolution_count} local evolution(s); "
            f"smart merge {'recommended' if _auto_merge_enabled() else 'disabled'}"
        ),
    )


def handle_upstream_update(
    skill_name: str,
    new_upstream_content: str,
    new_upstream_version: str,
    upstream_skill_id: str = "",
) -> SyncHandleResult:
    """
    L1 推送新版本时的完整处理入口。

    1. 保存上游快照
    2. 若本地无进化 → 直接覆盖
    3. 若本地已进化 且 auto_merge 开启 → smart merge
    4. 若 force_overwrite → 覆盖并记录日志
    5. 否则 → 跳过覆盖，只记录 divergence 警告
    """
    from l3_node.autonomy.skill_evolver import (
        find_skill_md_path,
        load_manifest,
        save_manifest,
        save_upstream_snapshot,
        _append_evolution_log,
        SkillEvolutionRecord,
        _evolve_model,
        _snapshot_skill,
    )

    skill_path = find_skill_md_path(skill_name)

    # 保存上游快照（无论如何都保存，供 merge base 使用）
    upstream_snap = save_upstream_snapshot(skill_name, new_upstream_content, new_upstream_version)

    # 技能不存在本地 → 直接写入（首次安装）
    if skill_path is None:
        from l3_node.autonomy.skill_evolver import _l1_skill_root, mark_skill_origin
        target_dir = _l1_skill_root() / skill_name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "SKILL.md"
        target_path.write_text(new_upstream_content, encoding="utf-8")
        _notify_skill_md_hot_reload_after_write(target_path)
        mark_skill_origin(
            skill_name=skill_name,
            skill_path=target_path,
            origin="l1_subscribed",
            upstream_skill_id=upstream_skill_id or skill_name,
            upstream_version=new_upstream_version,
        )
        logger.info("[SkillSyncGuard] installed new skill=%s version=%s", skill_name, new_upstream_version)
        return SyncHandleResult(
            action="overwritten", skill_name=skill_name,
            new_local_path=str(target_path),
            upstream_snapshot_path=upstream_snap,
            merge_conflicts=[], message="newly installed from L1",
        )

    manifest = load_manifest(skill_path, skill_name)

    # 更新 manifest upstream 信息
    manifest.upstream_version = new_upstream_version
    manifest.upstream_last_synced = time.time()
    if upstream_skill_id:
        manifest.upstream_skill_id = upstream_skill_id

    # 无本地进化 → 直接覆盖
    if not manifest.diverged_from_upstream or manifest.local_evolution_count == 0:
        skill_path.write_text(new_upstream_content, encoding="utf-8")
        _notify_skill_md_hot_reload_after_write(skill_path)
        manifest.local_version = new_upstream_version
        manifest.diverged_from_upstream = False
        save_manifest(manifest)
        logger.info("[SkillSyncGuard] overwritten skill=%s version=%s (no local evolution)", skill_name, new_upstream_version)
        return SyncHandleResult(
            action="overwritten", skill_name=skill_name,
            new_local_path=str(skill_path),
            upstream_snapshot_path=upstream_snap,
            merge_conflicts=[], message="clean overwrite, no local evolution",
        )

    # 强制覆盖模式
    if _force_overwrite():
        original = skill_path.read_text(encoding="utf-8")
        pre_snap = _snapshot_skill(skill_name, original, "forced_overwrite", label="pre_forced")
        skill_path.write_text(new_upstream_content, encoding="utf-8")
        _notify_skill_md_hot_reload_after_write(skill_path)
        manifest.local_version = new_upstream_version
        manifest.diverged_from_upstream = False
        manifest.local_evolution_count = 0
        save_manifest(manifest)
        _log_divergence(skill_name, str(skill_path), manifest, "forced_overwrite", new_upstream_version)
        logger.warning("[SkillSyncGuard] FORCED overwrite skill=%s local evolutions lost! pre-snap=%s", skill_name, pre_snap)
        return SyncHandleResult(
            action="forced", skill_name=skill_name,
            new_local_path=str(skill_path),
            upstream_snapshot_path=upstream_snap,
            merge_conflicts=[], message=f"forced overwrite; {manifest.local_evolution_count} local evolution(s) discarded",
        )

    # Smart merge
    if _auto_merge_enabled():
        local_content = skill_path.read_text(encoding="utf-8")
        from l3_node.autonomy.skill_evolver import load_upstream_snapshot
        base_content = load_upstream_snapshot(skill_name, manifest.upstream_version or "") or ""
        merged, conflicts = _smart_merge(
            base=base_content,
            local=local_content,
            upstream=new_upstream_content,
        )
        # 备份本地版本
        pre_snap = _snapshot_skill(skill_name, local_content, "pre_merge", label="pre_merge")
        skill_path.write_text(merged, encoding="utf-8")
        _notify_skill_md_hot_reload_after_write(skill_path)
        from l3_node.autonomy.skill_evolver import _extract_local_version, _split_frontmatter
        new_fm, _ = _split_frontmatter(merged)
        manifest.local_version = _extract_local_version(new_fm) if new_fm else manifest.local_version
        manifest.diverged_from_upstream = bool(conflicts)
        save_manifest(manifest)
        _log_divergence(skill_name, str(skill_path), manifest, "smart_merged", new_upstream_version, conflicts=conflicts)
        logger.info(
            "[SkillSyncGuard] smart merged skill=%s upstream=%s conflicts=%d",
            skill_name, new_upstream_version, len(conflicts),
        )
        return SyncHandleResult(
            action="merged", skill_name=skill_name,
            new_local_path=str(skill_path),
            upstream_snapshot_path=upstream_snap,
            merge_conflicts=conflicts,
            message=(
                f"smart merged: {len(conflicts)} conflict section(s) kept local version"
                if conflicts else "smart merged: no conflicts"
            ),
        )

    # auto_merge 关闭，跳过覆盖，只记录警告
    _log_divergence(skill_name, str(skill_path), manifest, "skipped", new_upstream_version)
    logger.warning(
        "[SkillSyncGuard] skipped overwrite skill=%s (local evolved, auto_merge disabled)",
        skill_name,
    )
    return SyncHandleResult(
        action="skipped", skill_name=skill_name,
        new_local_path=str(skill_path),
        upstream_snapshot_path=upstream_snap,
        merge_conflicts=[],
        message="local evolution detected; auto_merge disabled; overwrite skipped",
    )


# ---------------------------------------------------------------------------
# Smart Merge（3-way：base + local + upstream）
# ---------------------------------------------------------------------------

def _split_md_sections(content: str) -> dict[str, str]:
    """
    将 Markdown 按 `# 段落名` 拆分为 {section_name: content} 字典。
    特殊 key "__frontmatter__" 存放 YAML Frontmatter。
    """
    from l3_node.autonomy.skill_evolver import _split_frontmatter
    fm, body = _split_frontmatter(content)
    sections: dict[str, str] = {}
    if fm:
        sections["__frontmatter__"] = fm

    current_key = "__preamble__"
    current_lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r'^(#{1,3})\s+(.+)$', line)
        if m:
            if current_lines:
                sections[current_key] = "\n".join(current_lines)
            current_key = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        sections[current_key] = "\n".join(current_lines)
    return sections


def _smart_merge(
    base: str,
    local: str,
    upstream: str,
) -> tuple[str, list[str]]:
    """
    3-way smart merge。
    返回 (merged_content, conflicts: list[section_name_with_conflict])。
    策略：
    - Frontmatter → 取 upstream（接受工具链更新），但保留 local version 字段
    - 正文段落：
        - only-local-changed → keep local
        - only-upstream-changed → keep upstream
        - both-changed → keep local，在该段末尾追加 upstream diff 作为注释段落
        - base-absent, local-present → keep local (local-added)
        - base-absent, upstream-present → keep upstream (upstream-added)
    """
    from l3_node.autonomy.skill_evolver import _split_frontmatter, _extract_local_version

    conflicts: list[str] = []

    if not base:
        # 无 base → 简单合并：取 upstream Frontmatter + local Rules/Persona
        up_fm, up_body = _split_frontmatter(upstream)
        lo_fm, lo_body = _split_frontmatter(local)
        lo_version = _extract_local_version(lo_fm) if lo_fm else "1.0.0"
        if up_fm:
            merged_fm = re.sub(r'version:\s*["\']?[^"\'\n]+["\']?', f'version: "{lo_version}"', up_fm)
        else:
            merged_fm = lo_fm
        merged = merged_fm + "\n\n" + lo_body if merged_fm else lo_body
        return merged, []

    base_sec = _split_md_sections(base)
    local_sec = _split_md_sections(local)
    up_sec = _split_md_sections(upstream)

    all_keys: list[str] = []
    seen: set[str] = set()
    for d in [base_sec, local_sec, up_sec]:
        for k in d:
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    result_parts: list[str] = []
    for key in all_keys:
        base_val = base_sec.get(key, "")
        local_val = local_sec.get(key, "")
        up_val = up_sec.get(key, "")

        if key == "__frontmatter__":
            # 取 upstream frontmatter，但保留 local version
            lo_fm_ver = _extract_local_version(local_val) if local_val else ""
            merged_fm = up_val or local_val
            if lo_fm_ver and merged_fm:
                merged_fm = re.sub(r'version:\s*["\']?[^"\'\n]+["\']?', f'version: "{lo_fm_ver}"', merged_fm)
            if merged_fm:
                result_parts.append(merged_fm)
            continue

        if key == "__preamble__":
            result_parts.append(local_val or up_val or base_val)
            continue

        local_changed = (local_val != base_val) and bool(local_val)
        up_changed = (up_val != base_val) and bool(up_val)

        if not local_val and not up_val:
            continue  # 被删除
        elif not local_changed and not up_changed:
            result_parts.append(local_val or up_val)
        elif local_changed and not up_changed:
            result_parts.append(local_val)
        elif not local_changed and up_changed:
            result_parts.append(up_val)
        else:
            # 双方都改了 → 保留 local，将 upstream 变更追加为注释段
            conflicts.append(key)
            conflict_note = (
                f"\n\n> ⚠️ **上游更新（v{_extract_version_tag(upstream)}）在此段也有改动，"
                f"已保留本地进化版本。如需合并请人工处理：**\n"
                + "\n".join(
                    f"> {line}" for line in (up_val or "").splitlines()
                    if line.strip() and line not in (local_val or "").splitlines()
                )
            )
            result_parts.append(local_val + conflict_note)

    merged = "\n\n".join(p for p in result_parts if p)
    return merged, conflicts


def _extract_version_tag(content: str) -> str:
    m = re.search(r'version:\s*["\']?([^"\'\n]+)["\']?', content)
    return m.group(1).strip().strip('"\'') if m else "?"


# ---------------------------------------------------------------------------
# 分叉警告日志
# ---------------------------------------------------------------------------

def _log_divergence(
    skill_name: str,
    skill_path: str,
    manifest: Any,
    action: str,
    new_upstream_version: str,
    conflicts: list[str] | None = None,
) -> None:
    from l3_node.autonomy.skill_evolver import _append_evolution_log, SkillEvolutionRecord, _evolve_model
    import hashlib

    record = SkillEvolutionRecord(
        evolution_id=f"sync_{int(time.time())}",
        skill_name=skill_name,
        skill_path=skill_path,
        status=f"sync_{action}",
        trigger="upstream_sync",
        change_summary=(
            f"上游版本 {new_upstream_version} 到达；"
            f"本地有 {getattr(manifest, 'local_evolution_count', 0)} 次进化；"
            f"操作: {action}"
            + (f"；冲突段落: {', '.join(conflicts)}" if conflicts else "")
        ),
        change_ratio=0.0,
        original_hash="",
        new_hash="",
        snapshot_path="",
        evidence_count=0,
        confidence=1.0,
        model=_evolve_model(),
        origin=getattr(manifest, "origin", "unknown"),
        upstream_version=new_upstream_version,
    )
    _append_evolution_log(record)


# ---------------------------------------------------------------------------
# 查询接口（供 HTTP 诊断端点）
# ---------------------------------------------------------------------------

def get_sync_guard_stats() -> dict[str, Any]:
    """返回全局同步保护状态摘要。"""
    from l3_node.autonomy.skill_evolver import list_diverged_skills
    diverged = list_diverged_skills()
    return {
        "auto_merge_enabled": _auto_merge_enabled(),
        "force_overwrite": _force_overwrite(),
        "diverged_skills_count": len(diverged),
        "diverged_skills": diverged,
    }
