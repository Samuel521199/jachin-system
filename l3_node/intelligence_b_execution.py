"""
智能化阶段 B：execution_mode（react | planned | strict）与计划卡轻量校验；
可选 **brainstorm 卡**、**strict 下硬只读 verify 轮** 工具白名单；
**force_universal_planning_chain**：react 下也强制计划卡（及可选 brainstorm）门禁。

计划卡格式（assistant 消息内 JSON 对象，可置于 ```json 代码块）：
{
  "jachin_plan_card": {
    "goal": "…",
    "steps": ["…", "…"],
    "risks": "…",
    "rollback_point": "…"
  }
}
也可顶层即 plan 字段：goal, steps, risks, rollback_point

Brainstorm 卡（intelligence_b.require_brainstorm_card=true 且 planned/strict）：
{
  "jachin_brainstorm_card": {
    "angles": ["思路1", "思路2"],
    "constraints": "已知约束",
    "open_questions": "待澄清"
  }
}
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from l3_node.jachin_config import get_jachin_root

logger = logging.getLogger(__name__)


def _nexus_path():
    return get_jachin_root() / "nexus_config.json"


def load_intelligence_b_config() -> dict[str, Any]:
    try:
        import json as _json
        p = _nexus_path()
        if not p.exists():
            return {}
        cfg = _json.loads(p.read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_b")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[IntelB] 读取 intelligence_b 失败: %s", e)
        return {}


def get_execution_mode() -> str:
    """react | planned | strict"""
    cfg = load_intelligence_b_config()
    m = str(cfg.get("execution_mode", "react") or "react").lower().strip()
    if m not in ("react", "planned", "strict"):
        return "react"
    return m


def get_require_brainstorm_card() -> bool:
    """是否在 planned/strict 下强制先输出 brainstorm 卡（再计划卡）。"""
    return bool(load_intelligence_b_config().get("require_brainstorm_card", False))


def get_enforce_readonly_verify_round() -> bool:
    """
    strict 下写类工具后，是否 **硬限制** 仅允许只读工具直至 VERIFY_PASS。
    未配置时：strict 默认为 True，其余模式 False。
    """
    cfg = load_intelligence_b_config()
    if "enforce_readonly_verify_round" in cfg:
        return bool(cfg.get("enforce_readonly_verify_round"))
    return get_execution_mode() == "strict"


def get_allow_recall_before_plan_gates() -> bool:
    """计划卡 / brainstorm 门禁前是否允许 recall_memory。"""
    return bool(load_intelligence_b_config().get("allow_recall_before_plan_gates", True))


def get_force_task_plan_file() -> bool:
    """是否启用「多步意图须先落盘 task_plan.md」路由（见 task_plan_policy）。"""
    return bool(load_intelligence_b_config().get("force_task_plan_file", False))


def get_force_universal_planning_chain() -> bool:
    """
    若为 True：即使在 **react** 模式下也强制 **计划卡**（及可选 brainstorm 卡）门禁，
    与 planned/strict 相同校验逻辑（见 agent_core 计划门）。
    """
    return bool(load_intelligence_b_config().get("force_universal_planning_chain", False))


def get_verify_round_extra_tool_ids() -> list[str]:
    """只读 verify 轮额外允许的 tool id（小写）。"""
    raw = load_intelligence_b_config().get("verify_round_extra_tools")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        s = str(x or "").strip().lower()
        if s:
            out.append(s)
    return out


# 默认可在 verify 轮使用的工具（仅 ID 精确匹配，小写）
_DEFAULT_VERIFY_READONLY_IDS = frozenset(
    {
        "core:fs_read",
        "core:shell_job_status",
        "core:check_background_task",
    },
)


def verify_round_allowed_tool_ids() -> frozenset[str]:
    extra = frozenset(get_verify_round_extra_tool_ids())
    return _DEFAULT_VERIFY_READONLY_IDS | extra


def filter_tools_for_verify_round(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 LLM 可见工具列表限制为只读 verify 白名单。"""
    allow = verify_round_allowed_tool_ids()
    out: list[dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or "").strip().lower()
        if tid in allow:
            out.append(t)
    return out


def parse_types_allowed_before_plan_gates() -> frozenset[str]:
    types: set[str] = set()
    if get_allow_recall_before_plan_gates():
        types.add("recall")
    return frozenset(types)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    # ```json ... ```
    for pattern in (r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", r"```\s*(\{[\s\S]*?\})\s*```"):
        m = re.search(pattern, text)
        if m:
            try:
                obj = json.loads(m.group(1))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                pass
    # 裸对象（首个 { 配对）
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    start = -1
    return None


def validate_plan_card_from_assistant(text: str) -> tuple[bool, str]:
    """
    轻量校验器（非 LLM）：必填 goal、steps（非空列表）、risks、rollback_point。
    """
    obj = _extract_json_object(text or "")
    if not obj:
        return False, "未找到可解析的 JSON 计划对象"
    card = obj.get("jachin_plan_card")
    if isinstance(card, dict):
        data = card
    else:
        data = obj
    goal = str(data.get("goal", "")).strip()
    steps = data.get("steps")
    risks = str(data.get("risks", "")).strip()
    rollback = str(data.get("rollback_point", data.get("rollback", ""))).strip()
    if not goal:
        return False, "计划缺少 goal"
    if not isinstance(steps, list) or len(steps) == 0:
        return False, "计划缺少 steps 或非空列表"
    if not all(isinstance(s, str) and s.strip() for s in steps):
        return False, "steps 须为非空字符串列表"
    if not risks:
        return False, "计划缺少 risks"
    if not rollback:
        return False, "计划缺少 rollback_point"
    return True, "ok"


def scan_messages_for_valid_plan(messages: list[dict[str, Any]]) -> bool:
    """是否已有 assistant 消息含合法计划卡。"""
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        content = m.get("content") or ""
        if not isinstance(content, str):
            continue
        ok, _ = validate_plan_card_from_assistant(content)
        if ok:
            return True
    return False


def validate_brainstorm_card_from_assistant(text: str) -> tuple[bool, str]:
    """轻量校验 brainstorm：angles 非空列表，constraints / open_questions 非空字符串。"""
    obj = _extract_json_object(text or "")
    if not obj:
        return False, "未找到可解析的 JSON brainstorm 对象"
    card = obj.get("jachin_brainstorm_card")
    if isinstance(card, dict):
        data = card
    else:
        data = obj
    angles = data.get("angles")
    constraints = str(data.get("constraints", "")).strip()
    open_q = str(data.get("open_questions", data.get("questions", ""))).strip()
    if not isinstance(angles, list) or len(angles) == 0:
        return False, "brainstorm 缺少 angles 或非空列表"
    if not all(isinstance(s, str) and s.strip() for s in angles):
        return False, "angles 须为非空字符串列表"
    if not constraints:
        return False, "brainstorm 缺少 constraints"
    if not open_q:
        return False, "brainstorm 缺少 open_questions"
    return True, "ok"


def scan_messages_for_valid_brainstorm(messages: list[dict[str, Any]]) -> bool:
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        content = m.get("content") or ""
        if not isinstance(content, str):
            continue
        ok, _ = validate_brainstorm_card_from_assistant(content)
        if ok:
            return True
    return False
