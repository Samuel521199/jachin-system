"""Capability-aware semantic intent candidates for ReviewBoard.

This layer is intentionally lightweight and deterministic today. It behaves as
the local semantic parser under ReviewBoard: combine mission-slot parsing,
capability metadata, learned corrections, and app/entity alternatives into a
ranked candidate list. A small LLM parser can later replace or augment
``_mission_candidates`` behind the same output contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.capability_embedding_index import CapabilityMatch, match_capabilities
from l3_node.capability_semantic_registry import CapabilityDescriptor, build_capability_registry
from l3_node.task_understanding_engine import infer_task_understanding

from .contracts import RelevantMemoryBundle, StateSnapshot
from .entity_corrections import get_learned_app_correction, normalize_entity_surface
from .ledger import append_event


@dataclass(slots=True)
class SemanticIntentCandidate:
    intent: str
    task_type: str
    confidence: float
    tool: str = ""
    capability_id: str = ""
    workflow_id: str = ""
    target_patch: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    reason: str = ""
    source: str = "semantic"
    descriptor: dict[str, Any] = field(default_factory=dict)
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_APP_INTENT_WORDS = {
    "open_app": ("open", "launch", "start", "打开", "启动", "运行"),
    "close_app": ("close", "quit", "关闭", "关掉", "退出"),
    "switch_app": ("switch", "focus", "切换", "切到", "回到"),
}


_APP_ALTERNATIVES: dict[str, list[tuple[str, str, float]]] = {
    "browser": [("Browser", "generic_browser", 0.86), ("Chrome", "browser_candidate", 0.78), ("Edge", "browser_candidate", 0.76)],
    "浏览器": [("Browser", "generic_browser", 0.86), ("Chrome", "browser_candidate", 0.78), ("Edge", "browser_candidate", 0.76)],
    "chrome": [("Chrome", "explicit_app", 0.96), ("Browser", "generic_browser", 0.74)],
    "edge": [("Edge", "explicit_app", 0.96), ("Browser", "generic_browser", 0.74)],
    "lock": [("Lark", "entity_correction_candidate", 0.91)],
    "lark": [("Lark", "explicit_app", 0.98)],
    "feishu": [("Lark", "explicit_app", 0.95)],
    "飞书": [("Lark", "explicit_app", 0.95)],
    "wechat": [("WeChat", "explicit_app", 0.95)],
    "weixin": [("WeChat", "explicit_app", 0.95)],
    "微信": [("WeChat", "explicit_app", 0.95)],
    "calculator": [("Calculator", "explicit_app", 0.95)],
    "计算器": [("Calculator", "explicit_app", 0.95)],
}


def resolve_semantic_intent_candidates(
    *,
    text: str,
    base_intent: str = "",
    base_task_type: str = "",
    base_target: dict[str, Any] | None = None,
    state_snapshot: StateSnapshot | None = None,
    memory_bundle: RelevantMemoryBundle | None = None,
    tools: list[dict[str, Any]] | None = None,
    limit: int = 6,
) -> list[SemanticIntentCandidate]:
    raw = str(text or "").strip()
    if not raw:
        return []
    registry = build_capability_registry(tools)
    candidates: list[SemanticIntentCandidate] = []
    candidates.extend(_mission_candidates(raw, registry, limit=limit))
    candidates.extend(_llm_semantic_candidates(raw, registry, limit=limit))
    candidates.extend(_capability_candidates(raw, registry, limit=limit))
    candidates.extend(_app_alternative_candidates(raw, base_intent=base_intent, base_task_type=base_task_type))
    candidates.extend(_memory_correction_candidates(raw, base_intent=base_intent, base_task_type=base_task_type))
    if base_intent and base_intent != "conversation":
        candidates.append(
            SemanticIntentCandidate(
                intent=base_intent,
                task_type=base_task_type or _task_type_for_intent(base_intent),
                confidence=0.64,
                tool="",
                target_patch=dict(base_target or {}),
                reason="rule_based_reviewboard_candidate",
                source="review_board_rule",
            )
        )
    ranked = _dedupe_rank(candidates, limit=limit)
    append_event(
        "semantic_intent_candidates",
        getattr(memory_bundle, "turn_id", "") or "semantic-intent",
        {"text_preview": raw[:200], "candidates": [item.to_dict() for item in ranked]},
    )
    return ranked


def choose_semantic_override(
    *,
    text: str = "",
    base_intent: str,
    base_task_type: str,
    base_tool: str,
    base_target: dict[str, Any],
    candidates: list[SemanticIntentCandidate],
) -> SemanticIntentCandidate | None:
    if not candidates:
        return None
    best = candidates[0]
    if not best.intent:
        return None
    if not _candidate_allowed_for_text(best, text=text, base_intent=base_intent):
        return None
    if base_intent in {"", "conversation"} and best.confidence >= 0.55:
        return best
    if base_intent == "message_send" and best.task_type in {"project_briefing_delivery", "codex_ask_lark_send", "web_research_delivery"} and best.confidence >= 0.55:
        return best
    if not base_tool and best.tool and best.confidence >= 0.58 and best.intent == base_intent:
        return best
    if (
        best.source in {"learned_entity_correction", "entity_correction_candidate"}
        and best.confidence >= 0.84
        and best.intent == base_intent
    ):
        return best
    return None


def _candidate_allowed_for_text(candidate: SemanticIntentCandidate, *, text: str, base_intent: str) -> bool:
    task_type = str(candidate.task_type or "").strip()
    raw = str(text or "")
    low = raw.lower()
    if task_type == "project_briefing_delivery":
        if not _looks_like_project_briefing_goal(low, candidate.target_patch):
            return False
    if task_type == "codex_ask_lark_send":
        if not _looks_like_codex_ask_goal(low):
            return False
    if task_type == "web_research_delivery":
        if not _looks_like_web_research_delivery_goal(low):
            return False
    if base_intent == "message_send" and task_type == "project_briefing_delivery":
        return _looks_like_project_briefing_goal(low, candidate.target_patch)
    if base_intent == "message_send" and task_type == "codex_ask_lark_send":
        return _looks_like_codex_ask_goal(low)
    if base_intent == "message_send" and task_type == "web_research_delivery":
        return _looks_like_web_research_delivery_goal(low)
    # A clear deterministic route should not be replaced by a different
    # capability family. App/entity correction still gets handled below by
    # matching the same base intent.
    if base_intent not in {"", "conversation"}:
        expected = _task_type_for_intent(base_intent)
        if expected and task_type and task_type != expected:
            return False
    return True


def _looks_like_project_briefing_goal(low: str, target_patch: dict[str, Any]) -> bool:
    if target_patch.get("project_name") or target_patch.get("project_path") or target_patch.get("feature_query"):
        return True
    project_terms = (
        "project",
        "repo",
        "repository",
        "codebase",
        "codex",
        "jachin",
        "git",
        "\\",
        "/",
        "\u9879\u76ee",  # 项目
        "\u4ee3\u7801",  # 代码
        "\u4ed3\u5e93",  # 仓库
        "\u672c\u673a",  # 本机
        "\u76ee\u5f55",  # 目录
        "\u529f\u80fd",  # 功能
        "\u8fdb\u5c55",  # 进展
    )
    briefing_terms = (
        "summary",
        "summarize",
        "brief",
        "briefing",
        "\u603b\u7ed3",  # 总结
        "\u6574\u7406",  # 整理
        "\u6c47\u62a5",  # 汇报
        "\u7b80\u62a5",  # 简报
        "\u6700\u8fd1",  # 最近
    )
    return any(term in low for term in project_terms) and any(term in low for term in briefing_terms)


def _looks_like_codex_ask_goal(low: str) -> bool:
    return any(
        term in low
        for term in (
            "codex",
            "ask codex",
            "\u8ba9 codex",
            "\u95ee codex",
            "\u4f7f\u7528 codex",
        )
    )


def _looks_like_web_research_delivery_goal(low: str) -> bool:
    search_terms = (
        "search",
        "web",
        "internet",
        "latest",
        "news",
        "\u641c\u7d22",  # 搜索
        "\u4e0a\u7f51",  # 上网
        "\u67e5\u4e00\u4e0b",  # 查一下
        "\u6700\u65b0",  # 最新
        "\u6d88\u606f",  # 消息
        "\u65b0\u95fb",  # 新闻
        "\u8d44\u8baf",  # 资讯
    )
    delivery_terms = (
        "send",
        "message",
        "lark",
        "feishu",
        "\u53d1\u7ed9",  # 发给
        "\u53d1\u9001",  # 发送
        "\u53d1\u5230",  # 发到
        "\u98de\u4e66",  # 飞书
        "\u7fa4",  # 群
    )
    summarize_terms = (
        "summary",
        "summarize",
        "brief",
        "\u603b\u7ed3",  # 总结
        "\u6574\u7406",  # 整理
        "\u6458\u8981",  # 摘要
        "\u91cd\u70b9",  # 重点
    )
    has_search = any(term in low for term in search_terms)
    has_delivery = any(term in low for term in delivery_terms) or bool(
        re.search(r"(发|发送|推送|同步|转发)(给|到|至)?[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", low)
    )
    _has_summary = any(term in low for term in summarize_terms)
    # Searching current information and sending it implies a sendable summary.
    # Do not misroute "search latest AI model news and send Neil" as a raw message.
    return has_search and has_delivery


def _mission_candidates(text: str, registry: list[CapabilityDescriptor], *, limit: int) -> list[SemanticIntentCandidate]:
    try:
        understanding = infer_task_understanding(text)
    except Exception:
        return []
    intent = understanding.intent
    task_type = str(intent.task_type.value)
    if task_type == "unknown":
        return []
    capability = _best_descriptor_for_task_type(task_type, registry)
    return [
        SemanticIntentCandidate(
            intent=_intent_for_task_type(task_type, text),
            task_type=_review_task_type_for_mission(task_type),
            confidence=round(float(understanding.confidence or intent.confidence or 0.0), 3),
            tool=capability.id if capability and capability.id.startswith(("mcp:", "core:", "util:")) else _tool_for_task_type(task_type),
            capability_id=capability.id if capability else "",
            workflow_id=capability.workflow_id if capability else "",
            target_patch=_target_from_mission(intent.slots.to_dict(), task_type),
            missing_slots=list(intent.missing_slots),
            reason="lightweight_semantic_parser",
            source="mission_semantic_parser",
            descriptor=capability.to_dict() if capability else {},
        )
    ][:limit]


def _llm_semantic_candidates(text: str, registry: list[CapabilityDescriptor], *, limit: int) -> list[SemanticIntentCandidate]:
    if (os.environ.get("JACHIN_SEMANTIC_INTENT_LLM") or "1").strip().lower() in {"0", "false", "off", "no"}:
        return []
    try:
        from l3_node.agent_ref import engine_ref

        engine = engine_ref.get("engine")
    except Exception:
        engine = None
    if engine is None:
        return []
    timeout_sec = float(os.environ.get("JACHIN_SEMANTIC_INTENT_LLM_TIMEOUT") or "1.2")
    model = (os.environ.get("JACHIN_SEMANTIC_INTENT_LLM_MODEL") or "dashscope/qwen-turbo").strip()
    capability_sample = [
        {
            "id": item.id,
            "task_type": item.task_type,
            "domain": item.domain,
            "actions": item.actions[:4],
            "objects": item.objects[:4],
            "examples": item.examples[:3],
        }
        for item in registry[:30]
    ]
    prompt = (
        "你是 Jachin 的轻量意图解析器，只输出 JSON。"
        "根据用户输入和能力清单，给出 1-4 个候选 intent。"
        "字段: intent, task_type, confidence, target_patch, capability_id, reason。"
        "不要决定执行，不要编造不存在的能力。\n"
        f"用户输入: {text}\n"
        f"能力清单: {json.dumps(capability_sample, ensure_ascii=False)}\n"
        "JSON 格式: {\"candidates\":[...]}"
    )
    try:
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(_run_llm_parse_in_thread, engine, prompt, model)
        try:
            raw = future.result(timeout=timeout_sec)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    except FutureTimeout:
        append_event("semantic_intent_llm_timeout", "semantic-intent", {"timeout_sec": timeout_sec, "text_preview": text[:160]})
        return []
    except Exception as exc:
        append_event("semantic_intent_llm_failed", "semantic-intent", {"error": str(exc)[:300], "text_preview": text[:160]})
        return []
    parsed = _parse_llm_candidates(raw)
    out: list[SemanticIntentCandidate] = []
    registry_by_id = {item.id: item for item in registry}
    for item in parsed[:limit]:
        if not isinstance(item, dict):
            continue
        capability_id = str(item.get("capability_id") or "").strip()
        descriptor = registry_by_id.get(capability_id)
        task_type = str(item.get("task_type") or (descriptor.task_type if descriptor else "") or "").strip()
        intent = str(item.get("intent") or _intent_for_task_type(task_type, text)).strip()
        if not intent or not task_type:
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
        except Exception:
            confidence = 0.0
        out.append(
            SemanticIntentCandidate(
                intent=intent,
                task_type=_review_task_type_for_mission(task_type),
                confidence=round(confidence, 3),
                tool=_tool_for_task_type(_review_task_type_for_mission(task_type)),
                capability_id=capability_id,
                workflow_id=descriptor.workflow_id if descriptor else "",
                target_patch=item.get("target_patch") if isinstance(item.get("target_patch"), dict) else {},
                reason=str(item.get("reason") or "lightweight_llm_semantic_parse")[:300],
                source="lightweight_llm_semantic_parser",
                descriptor=descriptor.to_dict() if descriptor else {},
            )
        )
    return out


def _run_llm_parse_in_thread(engine: Any, prompt: str, model: str) -> str:
    async def _call() -> str:
        return await engine.generate_response(
            [
                {"role": "system", "content": "Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            l3_override_model=model,
            max_tokens=420,
            temperature=0,
        )

    return str(asyncio.run(_call()))


def _parse_llm_candidates(raw: str) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return []
    if "```" in text:
        parts = [p for p in text.split("```") if p.strip()]
        text = parts[-1].strip() if parts else text
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        obj = json.loads(text)
    except Exception:
        match = None
        import re

        for match in re.finditer(r"\{.*\}", text, re.S):
            pass
        if not match:
            return []
        try:
            obj = json.loads(match.group(0))
        except Exception:
            return []
    candidates = obj.get("candidates") if isinstance(obj, dict) else obj
    return candidates if isinstance(candidates, list) else []


def _capability_candidates(text: str, registry: list[CapabilityDescriptor], *, limit: int) -> list[SemanticIntentCandidate]:
    out: list[SemanticIntentCandidate] = []
    try:
        matches = match_capabilities(text, registry, limit=limit)
    except Exception:
        return []
    for match in matches:
        task_type = match.capability.task_type or _review_task_type_for_descriptor(match.capability)
        intent = _intent_for_task_type(task_type, text)
        tool = match.capability.id if match.capability.id.startswith(("mcp:", "core:", "util:")) else _tool_for_task_type(task_type)
        out.append(
            SemanticIntentCandidate(
                intent=intent,
                task_type=_review_task_type_for_mission(task_type),
                confidence=round(float(match.score or 0.0), 3),
                tool=tool,
                capability_id=match.capability.id,
                workflow_id=match.capability.workflow_id,
                target_patch={},
                reason=match.reason,
                source=match.capability.source,
                descriptor=match.capability.to_dict(),
                matched_terms=list(match.matched_terms),
            )
        )
    return out


def _app_alternative_candidates(text: str, *, base_intent: str, base_task_type: str) -> list[SemanticIntentCandidate]:
    low = text.lower()
    intent = base_intent if base_intent in {"open_app", "close_app", "switch_app", "message_send"} else _app_intent_from_text(low)
    if not intent:
        return []
    out: list[SemanticIntentCandidate] = []
    for surface, alternatives in _APP_ALTERNATIVES.items():
        if surface not in low:
            continue
        for app_name, source, score in alternatives:
            target: dict[str, Any]
            if intent == "message_send":
                target = {"type": "lark_message", "app": app_name, "source": source}
            else:
                target = {"type": "app", "name": app_name, "source": source}
            if source == "entity_correction_candidate":
                target.update(
                    {
                        "requires_entity_confirmation": True,
                        "heard_as": surface,
                        "surface_norm": normalize_entity_surface(surface),
                        "candidate_alias": app_name,
                        "entity_score": score,
                    }
                )
            out.append(
                SemanticIntentCandidate(
                    intent=intent,
                    task_type=base_task_type or _task_type_for_intent(intent),
                    confidence=score,
                    tool=_tool_for_task_type(base_task_type or _task_type_for_intent(intent)),
                    capability_id="mcp:windows_open_app" if intent != "message_send" else "mcp:windows_lark_send_message",
                    workflow_id="app_entity_candidate",
                    target_patch=target,
                    reason=f"ranked app candidate from surface {surface}",
                    source=source,
                )
            )
    return out


def _memory_correction_candidates(text: str, *, base_intent: str, base_task_type: str) -> list[SemanticIntentCandidate]:
    words = [x for x in text.replace(",", " ").replace("，", " ").split() if len(x.strip()) >= 3]
    out: list[SemanticIntentCandidate] = []
    for word in words:
        learned = get_learned_app_correction(word)
        if not learned:
            continue
        intent = base_intent if base_intent in {"open_app", "close_app", "switch_app", "message_send"} else _app_intent_from_text(text.lower()) or "open_app"
        app_name = str(learned.get("name") or "").strip()
        if not app_name:
            continue
        target = (
            {"type": "lark_message", "app": app_name, "source": "learned_entity_correction"}
            if intent == "message_send"
            else {"type": "app", "name": app_name, "source": "learned_entity_correction"}
        )
        target.update(
            {
                "heard_as": word,
                "surface_norm": learned.get("surface_norm") or normalize_entity_surface(word),
                "candidate_alias": learned.get("alias") or app_name,
                "entity_score": learned.get("score") or 1.0,
                "memory_id": learned.get("memory_id") or "",
                "requires_entity_confirmation": bool(learned.get("requires_confirmation") or False),
            }
        )
        out.append(
            SemanticIntentCandidate(
                intent=intent,
                task_type=base_task_type or _task_type_for_intent(intent),
                confidence=max(0.9, float(learned.get("score") or 0.0)),
                tool=_tool_for_task_type(base_task_type or _task_type_for_intent(intent)),
                capability_id="mcp:windows_open_app" if intent != "message_send" else "mcp:windows_lark_send_message",
                workflow_id="learned_entity_correction",
                target_patch=target,
                reason="confirmed correction memory matched",
                source="learned_entity_correction",
            )
        )
    return out


def _dedupe_rank(candidates: list[SemanticIntentCandidate], *, limit: int) -> list[SemanticIntentCandidate]:
    best: dict[tuple[str, str, str, str], SemanticIntentCandidate] = {}
    for item in candidates:
        if not item.intent:
            continue
        target_key = str(item.target_patch.get("name") or item.target_patch.get("app") or "")
        key = (item.intent, item.task_type, item.tool, target_key)
        current = best.get(key)
        if current is None or item.confidence > current.confidence:
            best[key] = item
    def _priority(item: SemanticIntentCandidate) -> tuple[float, float, bool, bool]:
        source_boost = 0.0
        if item.source == "learned_entity_correction":
            source_boost = 0.18
        elif item.source == "entity_correction_candidate":
            source_boost = 0.12
        elif item.source in {"explicit_app", "generic_browser", "browser_candidate"}:
            source_boost = 0.05
        return (item.confidence + source_boost, item.confidence, bool(item.tool), bool(item.capability_id))

    return sorted(best.values(), key=_priority, reverse=True)[:limit]


def _best_descriptor_for_task_type(task_type: str, registry: list[CapabilityDescriptor]) -> CapabilityDescriptor | None:
    wanted = {task_type, _review_task_type_for_mission(task_type)}
    for item in registry:
        if item.task_type in wanted:
            return item
    tool_id = _tool_for_task_type(task_type)
    for item in registry:
        if tool_id and item.id == tool_id:
            return item
    return None


def _review_task_type_for_descriptor(capability: CapabilityDescriptor) -> str:
    if capability.task_type:
        return capability.task_type
    if "send_message" in capability.actions or "lark" in capability.domain:
        return "message_delivery"
    if "calculate" in capability.actions or "calculator" in capability.domain:
        return "calculator_calculate"
    if capability.domain.endswith("app_control"):
        return "app_control"
    if "file" in capability.domain:
        return "file_operation"
    return capability.task_type or ""


def _review_task_type_for_mission(task_type: str) -> str:
    return {
        "lark_message_send": "message_delivery",
        "calculator_calculate": "calculator_calculate",
        "app_control": "app_control",
        "file_to_app": "file_operation",
        "file_find": "file_operation",
        "file_delete": "file_operation",
        "system_status_report": "system_status_report",
        "project_briefing_delivery": "project_briefing_delivery",
        "codex_ask_lark_send": "codex_ask_lark_send",
        "web_research_delivery": "web_research_delivery",
    }.get(task_type, task_type)


def _intent_for_task_type(task_type: str, text: str) -> str:
    if task_type in {"message_delivery", "lark_message_send"}:
        return "message_send"
    if task_type == "calculator_calculate":
        return "calculator_calculate"
    if task_type == "app_control":
        return _app_intent_from_text(text.lower()) or "open_app"
    if task_type in {"file_operation", "file_to_app", "file_find", "file_delete"}:
        return "file_operation"
    if task_type == "web_research_delivery":
        return "web_research_delivery"
    if task_type:
        return task_type
    return ""


def _app_intent_from_text(low: str) -> str:
    for intent, words in _APP_INTENT_WORDS.items():
        if any(word in low for word in words):
            return intent
    return ""


def _task_type_for_intent(intent: str) -> str:
    if intent == "calculator_calculate":
        return "calculator_calculate"
    if intent in {"open_app", "close_app", "switch_app"}:
        return "app_control"
    if intent == "message_send":
        return "message_delivery"
    if intent == "file_operation":
        return "file_operation"
    return "conversation"


def _tool_for_task_type(task_type: str) -> str:
    return {
        "message_delivery": "mcp:windows_lark_send_message",
        "lark_message_send": "mcp:windows_lark_send_message",
        "calculator_calculate": "mcp:windows_calculator_calculate",
        "app_control": "mcp:windows_open_app",
        "file_operation": "core:fs_read",
        "project_briefing_delivery": "mcp:windows_codex_lark_workflow_template",
        "codex_ask_lark_send": "mcp:windows_codex_ask_lark_send",
        "web_research_delivery": "mcp:web_research_delivery",
    }.get(task_type, "")


def _target_from_mission(slots: dict[str, Any], task_type: str) -> dict[str, Any]:
    if task_type in {"lark_message_send", "message_delivery"}:
        return {"type": "lark_message", "app": "Lark", "recipients": slots.get("recipients") or [], "message": slots.get("message") or "", "source": "semantic_parser"}
    if task_type == "calculator_calculate":
        return {"type": "calculator", "name": "Calculator", "expression": slots.get("expression") or "", "source": "semantic_parser"}
    if task_type == "app_control":
        app = str(slots.get("app_name") or "").strip()
        return {"type": "app", "name": app, "source": "semantic_parser"} if app else {}
    if task_type in {"file_operation", "file_to_app", "file_find", "file_delete"}:
        path = str(slots.get("file_path") or slots.get("directory_path") or "").strip()
        return {"type": "file", "path": path, "name": path, "source": "semantic_parser"} if path else {}
    if task_type == "web_research_delivery":
        query = str(slots.get("query") or slots.get("topic") or slots.get("message") or "").strip()
        return {
            "type": "web_research_delivery",
            "app": "Lark",
            "query": query,
            "name": query,
            "recipients": slots.get("recipients") or [],
            "freshness": slots.get("freshness") or "latest",
            "delivery_stub": f"网页研究摘要生成中：{query}" if query else "网页研究摘要生成中",
            "source": "semantic_parser",
        }
    return {}
