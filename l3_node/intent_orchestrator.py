"""Intent Orchestrator and HIDCA guardrails.

This layer sits above mission parsing and below RoleExecutionAgent.  It turns a raw
utterance into an evidence-bearing routing decision, then physically narrows
the tool/context surface before the model sees it.
"""
from __future__ import annotations

import fnmatch
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from l3_node.capability_router import choose_capability_route
from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionTaskType
from l3_node.semantic_intent_engine import parse_semantic_intent_async
from l3_node.semantic_slot_parser import parse_mission_intent


HIDCA_OS_CONTROL = "OS_CONTROL"
HIDCA_WORKSPACE_LARK = "WORKSPACE_LARK"
HIDCA_CHITCHAT = "CHITCHAT"
HIDCA_UNKNOWN = "UNKNOWN"

_LARK_MARKERS = ("lark", "feishu", "flybook", "windows_lark", "codex_lark")
_OS_TOOL_MARKERS = (
    "windows_calculator",
    "calculator",
    "windows_open_app",
    "windows_file",
    "windows_workspace",
    "windows_system",
    "windows_notepad",
    "uia_",
    "mcp:uia",
    "desktop",
    "computer",
    "screen",
    "shell",
    "fs_",
    "file",
)
_LARK_CONTEXT_KEY_RE = re.compile(r"(lark|feishu|flybook|receive_id|chat_id)", re.I)


@dataclass
class IntentCandidate:
    domain: str
    action: str
    target: str = ""
    score: float = 0.0
    support: list[str] = field(default_factory=list)
    counter: list[str] = field(default_factory=list)
    task_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntentFrame:
    goal: str
    domain: str
    action: str
    target: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    forbidden: list[dict[str, Any]] = field(default_factory=list)
    explicit_signals: list[str] = field(default_factory=list)
    side_effect_level: str = "none"
    confidence: float = 0.0
    route_policy: str = "role_execution_fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCandidate:
    tool_id: str
    score: float
    match_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoutingDecision:
    utterance: str
    normalized: str
    intent: MissionIntent
    route: CapabilityRoute
    intent_frame: IntentFrame
    candidates: list[IntentCandidate]
    tool_candidates: list[ToolCandidate] = field(default_factory=list)
    chosen: dict[str, Any] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    clarification: str | None = None
    hidca: dict[str, Any] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    evidence_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance": self.utterance,
            "normalized": self.normalized,
            "intent": self.intent.to_dict(),
            "route": self.route.to_dict(),
            "intent_frame": self.intent_frame.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "tool_candidates": [c.to_dict() for c in self.tool_candidates],
            "chosen": dict(self.chosen),
            "rejected": [dict(x) for x in self.rejected],
            "clarification": self.clarification,
            "hidca": dict(self.hidca),
            "latency_ms": dict(self.latency_ms),
            "evidence_path": self.evidence_path,
        }


def _tool_id(tool: dict[str, Any]) -> str:
    return str(tool.get("id") or tool.get("name") or "").strip()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _contains_lark(text: str) -> bool:
    lower = str(text or "").lower()
    return "lark" in lower or "feishu" in lower or "flybook" in lower or "飞书" in text


def _extract_forbidden(text: str) -> list[dict[str, Any]]:
    forbidden: list[dict[str, Any]] = []
    patterns = (
        r"(?:不要|不需要|别|禁止|无需|不用|不是|而不是)\s*(?:打开|启动|使用|用|操作)?\s*(lark|feishu|flybook|飞书)",
        r"(?:do\s*not|don't|dont|without|no)\s*(?:open|use|operate)?\s*(lark|feishu|flybook)",
    )
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            entity = str(m.group(1) or "").strip().lower()
            if entity == "飞书":
                entity = "lark"
            item = {"entity": entity, "scope": "tool_or_app", "source": "user_negation"}
            if item not in forbidden:
                forbidden.append(item)
    return forbidden


def _explicit_signals(text: str) -> list[str]:
    signals: list[str] = []
    if re.search(r"windows\s*mcp|windows那个mcp|Windows那个MCP", text, re.I):
        signals.append("windows_mcp")
    if re.search(r"原生|本机|电脑原本|本地", text, re.I):
        signals.append("local_os")
    if re.search(r"计算器|calculator|calc", text, re.I):
        signals.append("calculator")
    if re.search(r"\d+\s*[\+\-\*/xX]\s*\d+", text):
        signals.append("arithmetic_expression")
    if _extract_forbidden(text):
        signals.append("user_negation")
    if _contains_lark(text):
        signals.append("lark_mention")
    return signals


def _domain_for_task(task_type: MissionTaskType) -> str:
    if task_type in {
        MissionTaskType.CALCULATOR_CALCULATE,
        MissionTaskType.APP_CONTROL,
        MissionTaskType.FILE_TO_APP,
        MissionTaskType.SYSTEM_STATUS_REPORT,
        MissionTaskType.PROJECT_MEMORY_UPDATE,
    }:
        return "desktop_control"
    if task_type in {
        MissionTaskType.LARK_MESSAGE_SEND,
        MissionTaskType.PROJECT_BRIEFING_DELIVERY,
        MissionTaskType.CODEX_ASK_LARK_SEND,
    }:
        return "communication"
    return "unknown"


def _action_for_task(task_type: MissionTaskType) -> str:
    return {
        MissionTaskType.CALCULATOR_CALCULATE: "calculate",
        MissionTaskType.APP_CONTROL: "open",
        MissionTaskType.FILE_TO_APP: "transfer_file",
        MissionTaskType.SYSTEM_STATUS_REPORT: "inspect",
        MissionTaskType.PROJECT_MEMORY_UPDATE: "remember",
        MissionTaskType.LARK_MESSAGE_SEND: "send",
        MissionTaskType.PROJECT_BRIEFING_DELIVERY: "summarize_and_send",
        MissionTaskType.CODEX_ASK_LARK_SEND: "ask_and_send",
    }.get(task_type, "unknown")


def _hidca_domain_for_frame(frame: IntentFrame) -> str:
    if frame.domain in {"desktop_control", "file"}:
        return HIDCA_OS_CONTROL
    if frame.domain == "communication":
        return HIDCA_WORKSPACE_LARK
    if frame.domain == "chitchat":
        return HIDCA_CHITCHAT
    return HIDCA_UNKNOWN


def _side_effect_for_task(task_type: MissionTaskType) -> str:
    if task_type in {MissionTaskType.LARK_MESSAGE_SEND, MissionTaskType.PROJECT_BRIEFING_DELIVERY, MissionTaskType.CODEX_ASK_LARK_SEND}:
        return "external"
    if task_type in {
        MissionTaskType.CALCULATOR_CALCULATE,
        MissionTaskType.APP_CONTROL,
        MissionTaskType.FILE_TO_APP,
        MissionTaskType.SYSTEM_STATUS_REPORT,
        MissionTaskType.PROJECT_MEMORY_UPDATE,
    }:
        return "local"
    return "none"


def _goal_for_intent(intent: MissionIntent) -> str:
    slots = intent.slots
    if intent.task_type == MissionTaskType.CALCULATOR_CALCULATE:
        return f"calculate {slots.expression} in Windows Calculator".strip()
    if intent.task_type == MissionTaskType.APP_CONTROL:
        return f"open or control local app {slots.app_name}".strip()
    if intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        return "send a Lark message"
    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        return "summarize project work and deliver it through Lark"
    if intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
        return "ask Codex a question and deliver the reply through Lark"
    if intent.task_type == MissionTaskType.FILE_TO_APP:
        return "move a local file into a desktop app"
    if intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        return "inspect local Windows system status"
    return "understand user request"


def _inputs_for_intent(intent: MissionIntent) -> dict[str, Any]:
    slots = intent.slots
    out: dict[str, Any] = {}
    if slots.expression:
        out["expression"] = slots.expression
    if slots.recipients:
        out["recipients"] = list(slots.recipients)
    if slots.message:
        out["message"] = slots.message
    if slots.feature_query:
        out["feature_query"] = slots.feature_query
    if slots.app_name:
        out["app_name"] = slots.app_name
    if slots.file_path:
        out["file_path"] = slots.file_path
    if slots.project_name:
        out["project_name"] = slots.project_name
    if slots.project_path:
        out["project_path"] = slots.project_path
    return out


def _build_constraints(text: str, forbidden: list[dict[str, Any]]) -> dict[str, Any]:
    require_domains: list[str] = []
    exclude_domains: list[str] = []
    exclude_tools: list[str] = []
    if re.search(r"windows\s*mcp|windows那个mcp|原生|本机|电脑原本|本地", text, re.I):
        require_domains.append("os_assistant")
    if any(str(x.get("entity") or "").lower() in {"lark", "feishu", "flybook"} for x in forbidden):
        exclude_domains.append("lark_im")
        exclude_tools.extend(["mcp:windows_lark_*", "*lark*", "*feishu*"])
    return {
        "require_domains": require_domains,
        "require_tools": [],
        "exclude_domains": exclude_domains,
        "exclude_tools": exclude_tools,
    }


def _candidate_from_intent(intent: MissionIntent, forbidden: list[dict[str, Any]], signals: list[str]) -> IntentCandidate:
    support = list(intent.reasoning or [])
    if "windows_mcp" in signals or "local_os" in signals:
        support.append("explicit local Windows domain signal")
    if intent.slots.expression:
        support.append("arithmetic expression present")
    if intent.slots.app_name:
        support.append(f"target app={intent.slots.app_name}")
    counter: list[str] = []
    if intent.task_type in {MissionTaskType.LARK_MESSAGE_SEND, MissionTaskType.PROJECT_BRIEFING_DELIVERY}:
        if any(str(x.get("entity") or "").lower() in {"lark", "feishu", "flybook"} for x in forbidden):
            counter.append("user_negation(lark)")
    return IntentCandidate(
        domain=_domain_for_task(intent.task_type),
        action=_action_for_task(intent.task_type),
        target=intent.slots.app_name or ("lark" if intent.task_type == MissionTaskType.LARK_MESSAGE_SEND else ""),
        score=float(intent.confidence or 0.0),
        support=support,
        counter=counter,
        task_type=intent.task_type.value,
    )


def _shadow_candidates(text: str, top: IntentCandidate, forbidden: list[dict[str, Any]]) -> list[IntentCandidate]:
    candidates = [top]
    if _contains_lark(text) or any(x.get("entity") == "lark" for x in forbidden):
        counter = []
        if any(str(x.get("entity") or "").lower() in {"lark", "feishu", "flybook"} for x in forbidden):
            counter.append("user_negation(lark)")
        if top.domain == "desktop_control":
            counter.append("primary task is local desktop control")
        if top.action == "calculate":
            counter.append("arithmetic expression belongs to calculator task")
        candidates.append(
            IntentCandidate(
                domain="communication",
                action="send_or_open",
                target="lark",
                score=0.08 if counter else 0.31,
                support=["lark mentioned in utterance"],
                counter=counter,
                task_type=MissionTaskType.LARK_MESSAGE_SEND.value,
            )
        )
    if top.domain != "unknown":
        candidates.append(
            IntentCandidate(
                domain="unknown",
                action="unknown",
                score=0.05,
                support=[],
                counter=["higher-confidence structured intent exists"],
                task_type=MissionTaskType.UNKNOWN.value,
            )
        )
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def _tool_candidates(route: CapabilityRoute) -> list[ToolCandidate]:
    if not route.tool_id:
        return []
    reasons = [route.reason] if route.reason else []
    return [ToolCandidate(tool_id=route.tool_id, score=0.91 if route.ok else 0.35, match_reasons=reasons)]


def analyze_intent(
    user_input: str,
    tools: list[dict[str, Any]] | None = None,
    allowed: list[str] | None = None,
    implicit_attribution: dict[str, Any] | None = None,
) -> RoutingDecision:
    t0 = time.perf_counter()
    text = str(user_input or "").strip()
    normalized = _compact(text)
    forbidden = _extract_forbidden(text)
    signals = _explicit_signals(text)
    intent = parse_mission_intent(text)
    route = choose_capability_route(intent, tools or [], allowed)
    frame = IntentFrame(
        goal=_goal_for_intent(intent),
        domain=_domain_for_task(intent.task_type),
        action=_action_for_task(intent.task_type),
        target=intent.slots.app_name or ("lark" if intent.task_type == MissionTaskType.LARK_MESSAGE_SEND else ""),
        inputs=_inputs_for_intent(intent),
        constraints=_build_constraints(text, forbidden),
        forbidden=forbidden,
        explicit_signals=signals,
        side_effect_level=_side_effect_for_task(intent.task_type),
        confidence=float(intent.confidence or 0.0),
        route_policy="execute" if route.ok and intent.confidence >= 0.72 else ("clarify" if intent.missing_slots else "role_execution_fallback"),
    )
    hidca_domain = _hidca_domain_for_frame(frame)
    if hidca_domain == HIDCA_OS_CONTROL and any(str(x.get("entity") or "").lower() == "lark" for x in forbidden):
        frame.constraints.setdefault("exclude_tools", []).extend(["mcp:windows_lark_*", "*lark*", "*feishu*"])
    top = _candidate_from_intent(intent, forbidden, signals)
    decision = RoutingDecision(
        utterance=text,
        normalized=normalized,
        intent=intent,
        route=route,
        intent_frame=frame,
        candidates=_shadow_candidates(text, top, forbidden),
        tool_candidates=_tool_candidates(route),
        chosen={
            "tool_id": route.tool_id,
            "workflow_id": route.workflow_id,
            "route_policy": frame.route_policy,
            "why": route.reason or "no route selected",
            "consistency": "PASS" if route.ok else "BLOCK",
        },
        rejected=[],
        clarification=None if route.ok or not intent.missing_slots else f"missing slots: {', '.join(intent.missing_slots)}",
        hidca={
            "semantic_router_domain": hidca_domain,
            "tools_before_prune": len(tools or []),
            "tools_after_prune": len(tools or []),
            "stripped_context_keys": [],
            "system_prompt_profile": _system_prompt_profile(hidca_domain),
            "implicit_channel": (implicit_attribution or {}).get("channel") if isinstance(implicit_attribution, dict) else None,
        },
        latency_ms={"parse": round((time.perf_counter() - t0) * 1000.0, 3)},
    )
    decision.rejected.extend(_rejected_tools_for_decision(decision))
    decision.latency_ms["total"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return decision


async def analyze_intent_async(
    user_input: str,
    tools: list[dict[str, Any]] | None = None,
    allowed: list[str] | None = None,
    implicit_attribution: dict[str, Any] | None = None,
    engine: Any | None = None,
) -> RoutingDecision:
    t0 = time.perf_counter()
    text = str(user_input or "").strip()
    normalized = _compact(text)
    forbidden = _extract_forbidden(text)
    signals = _explicit_signals(text)
    semantic = await parse_semantic_intent_async(text, engine=engine)
    intent = semantic.intent
    route = choose_capability_route(intent, tools or [], allowed)
    frame = IntentFrame(
        goal=_goal_for_intent(intent),
        domain=_domain_for_task(intent.task_type),
        action=_action_for_task(intent.task_type),
        target=intent.slots.app_name or ("lark" if intent.task_type == MissionTaskType.LARK_MESSAGE_SEND else ""),
        inputs=_inputs_for_intent(intent),
        constraints=_build_constraints(text, forbidden),
        forbidden=forbidden,
        explicit_signals=signals,
        side_effect_level=_side_effect_for_task(intent.task_type),
        confidence=float(intent.confidence or 0.0),
        route_policy="execute" if route.ok and intent.confidence >= 0.72 else ("clarify" if intent.missing_slots else "role_execution_fallback"),
    )
    hidca_domain = _hidca_domain_for_frame(frame)
    if hidca_domain == HIDCA_OS_CONTROL and any(str(x.get("entity") or "").lower() == "lark" for x in forbidden):
        frame.constraints.setdefault("exclude_tools", []).extend(["mcp:windows_lark_*", "*lark*", "*feishu*"])
    top = _candidate_from_intent(intent, forbidden, signals)
    decision = RoutingDecision(
        utterance=text,
        normalized=normalized,
        intent=intent,
        route=route,
        intent_frame=frame,
        candidates=_shadow_candidates(text, top, forbidden),
        tool_candidates=_tool_candidates(route),
        chosen={
            "tool_id": route.tool_id,
            "workflow_id": route.workflow_id,
            "route_policy": frame.route_policy,
            "why": route.reason or "no route selected",
            "consistency": "PASS" if route.ok else "BLOCK",
        },
        rejected=[],
        clarification=None if route.ok or not intent.missing_slots else f"missing slots: {', '.join(intent.missing_slots)}",
        hidca={
            "semantic_router_domain": hidca_domain,
            "tools_before_prune": len(tools or []),
            "tools_after_prune": len(tools or []),
            "stripped_context_keys": [],
            "system_prompt_profile": _system_prompt_profile(hidca_domain),
            "implicit_channel": (implicit_attribution or {}).get("channel") if isinstance(implicit_attribution, dict) else None,
            "semantic_intent": semantic.meta,
        },
        latency_ms={"parse": round((time.perf_counter() - t0) * 1000.0, 3)},
    )
    decision.rejected.extend(_rejected_tools_for_decision(decision))
    decision.latency_ms["total"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return decision
def _system_prompt_profile(hidca_domain: str) -> str:
    if hidca_domain == HIDCA_OS_CONTROL:
        return "os_local_admin"
    if hidca_domain == HIDCA_WORKSPACE_LARK:
        return "lark_workspace_orchestrator"
    if hidca_domain == HIDCA_CHITCHAT:
        return "companion_chat"
    return "general"


def _is_lark_tool(tool_id: str) -> bool:
    lower = tool_id.lower()
    return any(marker in lower for marker in _LARK_MARKERS)


def _is_os_tool(tool_id: str) -> bool:
    lower = tool_id.lower()
    if _is_lark_tool(lower):
        return False
    return lower.startswith("mcp:windows_") or any(marker in lower for marker in _OS_TOOL_MARKERS)


def _tool_matches_any(tool_id: str, patterns: list[str]) -> bool:
    lower = tool_id.lower()
    for pat in patterns:
        p = str(pat or "").lower()
        if not p:
            continue
        if fnmatch.fnmatch(lower, p) or fnmatch.fnmatch("mcp:" + lower.removeprefix("mcp:"), p):
            return True
    return False


def _rejected_tools_for_decision(decision: RoutingDecision) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    patterns = [str(x) for x in decision.intent_frame.constraints.get("exclude_tools") or []]
    if patterns:
        out.append({"tool_id": ",".join(patterns), "why": "excluded by user constraint or forbidden entity"})
    if decision.hidca.get("semantic_router_domain") == HIDCA_OS_CONTROL:
        out.append({"tool_id": "mcp:windows_lark_*", "why": "domain mismatch: OS_CONTROL forbids Lark tools"})
    return out


def prune_tools_for_hidca(
    tools: list[dict[str, Any]],
    decision: RoutingDecision,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    before = len(tools or [])
    hidca_domain = str(decision.hidca.get("semantic_router_domain") or HIDCA_UNKNOWN)
    exclude_patterns = [str(x) for x in decision.intent_frame.constraints.get("exclude_tools") or []]
    pruned: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        tid = _tool_id(tool)
        if not tid:
            continue
        if _tool_matches_any(tid, exclude_patterns):
            continue
        if hidca_domain == HIDCA_OS_CONTROL:
            if _is_os_tool(tid):
                pruned.append(tool)
            continue
        if hidca_domain == HIDCA_WORKSPACE_LARK:
            if _is_lark_tool(tid) or "schedule" in tid.lower() or tid.lower().startswith("util:"):
                pruned.append(tool)
            continue
        if hidca_domain == HIDCA_CHITCHAT:
            continue
        pruned.append(tool)
    if not pruned and tools:
        # Never strand RoleExecutionAgent with an empty pool for unknown domains.
        pruned = list(tools)
    meta = {
        "semantic_router_domain": hidca_domain,
        "tools_before_prune": before,
        "tools_after_prune": len(pruned),
        "system_prompt_profile": _system_prompt_profile(hidca_domain),
    }
    decision.hidca.update(meta)
    return pruned, meta


def sandbox_implicit_attribution(
    implicit_attribution: dict[str, Any] | None,
    decision: RoutingDecision,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(implicit_attribution, dict):
        return implicit_attribution, []
    hidca_domain = str(decision.hidca.get("semantic_router_domain") or HIDCA_UNKNOWN)
    if hidca_domain != HIDCA_OS_CONTROL:
        return dict(implicit_attribution), []
    sanitized: dict[str, Any] = {}
    stripped: list[str] = []
    for key, value in implicit_attribution.items():
        if _LARK_CONTEXT_KEY_RE.search(str(key)):
            stripped.append(str(key))
            continue
        sanitized[key] = value
    decision.hidca["stripped_context_keys"] = stripped
    return sanitized, stripped


def format_hidca_prompt_block(decision: RoutingDecision) -> str:
    hidca_domain = str(decision.hidca.get("semantic_router_domain") or HIDCA_UNKNOWN)
    if hidca_domain == HIDCA_OS_CONTROL:
        forbidden = ", ".join(sorted({str(x.get("entity") or "") for x in decision.intent_frame.forbidden if x.get("entity")})) or "-"
        return (
            "\n\n[Intent Orchestrator / HIDCA]\n"
            f"domain={hidca_domain}; action={decision.intent_frame.action}; target={decision.intent_frame.target or '-'}; "
            f"forbidden={forbidden}; system_prompt_profile=os_local_admin\n"
            "You are controlling the user's local Windows desktop through MCP tools. "
            "Do not open, use, or route through Lark/Feishu/IM tools unless the current user message explicitly asks to send or read messages. "
            "User negations and excluded tools are hard constraints.\n"
        )
    if hidca_domain == HIDCA_WORKSPACE_LARK:
        return (
            "\n\n[Intent Orchestrator / HIDCA]\n"
            f"domain={hidca_domain}; action={decision.intent_frame.action}; system_prompt_profile=lark_workspace_orchestrator\n"
            "This turn is a Lark/workspace communication task. Keep recipient, message, and delivery evidence explicit.\n"
        )
    return ""


def check_tool_consistency(
    tool_id: str,
    work_order_input: str,
    decision: RoutingDecision | None,
) -> dict[str, Any] | None:
    """Return a routing violation when a RoleExecutionAgent tool call conflicts with IO."""
    if decision is None:
        return None
    hidca_domain = str(decision.hidca.get("semantic_router_domain") or HIDCA_UNKNOWN)
    if hidca_domain != HIDCA_OS_CONTROL:
        return None
    tid = str(tool_id or "").strip()
    inp = str(work_order_input or "")
    lower_blob = f"{tid}\n{inp}".lower()
    forbidden_entities = {
        str(x.get("entity") or "").lower()
        for x in decision.intent_frame.forbidden
        if isinstance(x, dict) and str(x.get("entity") or "").strip()
    }
    has_lark_forbidden = bool(forbidden_entities & {"lark", "feishu", "flybook"})
    if _is_lark_tool(tid):
        return {
            "ok": False,
            "error": "routing_violation",
            "reason": "OS_CONTROL cannot call Lark tools",
            "tool_id": tid,
            "hidca_domain": hidca_domain,
        }
    if has_lark_forbidden and any(marker in lower_blob for marker in ("lark", "feishu", "flybook", "飞书")):
        return {
            "ok": False,
            "error": "routing_violation",
            "reason": "user forbade Lark/Feishu in this OS_CONTROL turn",
            "tool_id": tid,
            "hidca_domain": hidca_domain,
        }
    return None
def write_router_evidence(
    decision: RoutingDecision,
    *,
    output_dir: str | Path | None = None,
) -> str:
    base = Path(output_dir) if output_dir is not None else Path("output") / "intent_orchestrator"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"intent_orchestrator_{time.strftime('%Y%m%dT%H%M%S')}_{int(time.time() * 1000) % 100000:05d}.json"
    path.write_text(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    decision.evidence_path = str(path)
    return str(path)
