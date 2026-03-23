"""
意图级 task_plan.md 门禁（与 HR 招聘路径独立；HR 相关用户话术 **豁免**）。

启用：`nexus_config.json` → `intelligence_b.force_task_plan_file: true`
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_HR_EXEMPT = re.compile(
    r"招聘|简历|JD|职位|boss|筛简历|无人值守|打招呼|候选人|透析镜|atom_post|终局审判|雷达",
    re.IGNORECASE,
)

_MULTI_STEP = re.compile(
    r"多步|多个任务|分步|分阶段|路线图|roadmap|长期任务|实现.{0,20}功能|重构|迁移|部署上线|"
    r"先.+再|按计划|编写.+并|同时完成",
    re.IGNORECASE,
)


def is_hr_exempt_user_message(text: str) -> bool:
    """HR / 招聘域用户输入不强制 workspace 根目录 task_plan.md。"""
    return bool(_HR_EXEMPT.search(text or ""))


def user_message_suggests_multi_step_task(text: str) -> bool:
    """启发式：是否像多步/工程类任务（非向量路由，零依赖）。"""
    s = (text or "").strip()
    if len(s) >= 900:
        return True
    return bool(_MULTI_STEP.search(s))


def should_enforce_task_plan_file(user_message: str) -> bool:
    """本 turn 是否要求先落盘 task_plan.md 再执行写类工具。"""
    try:
        from l3_node.intelligence_b_execution import get_force_task_plan_file

        if not get_force_task_plan_file():
            return False
    except ImportError:
        return False
    if is_hr_exempt_user_message(user_message):
        return False
    return user_message_suggests_multi_step_task(user_message)


def fs_write_targets_workspace_task_plan(tool_id: str, action_input: str) -> bool:
    """是否 core:fs_write 指向 ~/.jachin/workspace/task_plan.md（允许先写计划文件）。"""
    tid = (tool_id or "").strip().lower()
    if tid != "core:fs_write":
        return False
    try:
        from l3_node.task_planning import get_task_plan_path

        target = get_task_plan_path().resolve()
    except Exception:
        return False
    raw = (action_input or "").strip()
    fp = ""
    if raw.startswith("{"):
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                fp = str(o.get("file_path") or o.get("path") or "").strip()
        except json.JSONDecodeError:
            pass
    if not fp and raw:
        lines = raw.split("\n")
        fp = (lines[0] or "").strip()
    if not fp:
        return False
    p = Path(fp).expanduser()
    if not p.is_absolute():
        p = (Path.home() / ".jachin" / "workspace" / fp.lstrip("/")).resolve()
    else:
        p = p.resolve()
    try:
        return p.samefile(target)
    except OSError:
        return str(p).lower() == str(target).lower()


def task_plan_gate_blocks_action(parsed: dict, user_message: str) -> bool:
    """是否因未写 task_plan 而拦截该 Action（不含 recall_memory）。"""
    if not should_enforce_task_plan_file(user_message):
        return False
    try:
        from l3_node.task_planning import task_plan_is_substantial

        if task_plan_is_substantial():
            return False
    except ImportError:
        return False
    ptype = parsed.get("type")
    if ptype == "delegate":
        return True
    if ptype == "coordinate":
        return True
    if ptype == "native":
        tool = str(parsed.get("tool") or "")
        tl = tool.lower()
        inp = str(parsed.get("input") or "")
        if tl == "core:fs_write" and fs_write_targets_workspace_task_plan(tool, inp):
            return False
        if tl in ("core:fs_write", "core:shell_exec", "core:apply_patch"):
            return True
    return False
