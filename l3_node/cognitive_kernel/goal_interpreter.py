"""Goal interpretation layer for the Cognitive Kernel.

The interpreter turns a raw user turn into a structured goal contract before
the Arbiter or TaskDecomposer decide how to execute it.  It is deliberately
metadata-aware but not tool-specific: capabilities can contribute candidates,
while this layer extracts stable goal slots, missing information, and risk.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import AgentInputEnvelope, RelevantMemoryBundle, StateSnapshot
from .ledger import append_event


@dataclass(slots=True)
class GoalInterpretation:
    goal_id: str
    turn_id: str
    raw_text: str
    normalized_goal: str
    primary_goal: str
    task_type: str
    entities: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    missing_information: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    confidence: float = 0.0
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SEND_RE = re.compile(
    r"(?:send|message|notify|tell)\s+(?P<recipient>[A-Za-z0-9_\-\s]{1,40}?)\s+(?:that\s+)?(?P<message>.+)",
    re.IGNORECASE,
)
_SEND_TO_RE = re.compile(
    r"(?:send|message|notify|tell)\s+(?P<message>.+?)\s+(?:to|for)\s+(?P<recipient>[A-Za-z0-9_\-\s]{1,40})$",
    re.IGNORECASE,
)
_CHINESE_SEND_RE = re.compile(
    r"(?:\u7ed9|\u5411)(?P<recipient>[\w\u4e00-\u9fff\-\s]{1,40})(?:\u53d1\u9001|\u53d1|send)(?:\u4e00\u6761)?(?:\u6d88\u606f)?(?:\uff0c|,|\s)*(?:\u5185\u5bb9(?:\u4e3a|\u662f))?(?P<message>.+)?"
)
_APP_RE = re.compile(
    r"(?:open|launch|start|switch to|focus|close|quit)\s+(?P<app>[A-Za-z0-9_\-\s]{2,60})",
    re.IGNORECASE,
)
_CALC_RE = re.compile(r"(?P<expr>\d+(?:\s*[-+*/xX]\s*\d+)+(?:\s*[-+*/xX]\s*\d+)*)")
_PATH_RE = re.compile(r"(?P<path>[A-Za-z]:\\[^\n\r\"']+)")
_TIME_RE = re.compile(r"(?:last|recent)\s+(?P<num>\d+)\s+(?P<unit>day|days|week|weeks)", re.IGNORECASE)


def _stable_id(turn_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{turn_id}:{text}".encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"goal_{digest}"


def _text(envelope: AgentInputEnvelope | dict[str, Any]) -> tuple[str, str]:
    if isinstance(envelope, AgentInputEnvelope):
        raw = envelope.raw_text or ""
        normalized = envelope.normalized_text or raw
        return raw, normalized
    raw = str(envelope.get("raw_text") or envelope.get("text") or "")
    normalized = str(envelope.get("normalized_text") or raw)
    return raw, normalized


def _turn_id(envelope: AgentInputEnvelope | dict[str, Any]) -> str:
    if isinstance(envelope, AgentInputEnvelope):
        return envelope.turn_id
    return str(envelope.get("turn_id") or "turn")


def _candidate_capability_ids(capability_candidates: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for candidate in capability_candidates or []:
        cap_id = str(candidate.get("id") or candidate.get("capability_id") or "")
        if cap_id and cap_id not in ids:
            ids.append(cap_id)
    return ids


def _infer_task_type(text: str, capability_candidates: list[dict[str, Any]] | None) -> tuple[str, list[str]]:
    lowered = text.lower()
    reasons: list[str] = []
    if any(k in lowered for k in ("send", "message", "notify", "tell")) or any(
        k in text for k in ("\u53d1\u9001", "\u53d1\u6d88\u606f", "\u544a\u8bc9")
    ):
        return "message_delivery", ["message verb detected"]
    if (_CALC_RE.search(text) and any(k in lowered for k in ("calculate", "calculator", "compute"))) or "\u8ba1\u7b97" in text:
        return "calculator_calculate", ["arithmetic expression detected"]
    if any(k in lowered for k in ("open", "launch", "start", "switch", "focus", "close", "quit")) or any(
        k in text for k in ("\u6253\u5f00", "\u542f\u52a8", "\u5207\u6362", "\u5173\u95ed")
    ):
        return "app_control", ["app control verb detected"]
    if _PATH_RE.search(text) or any(k in lowered for k in ("file", "folder", "directory", "reveal", "read")):
        return "file_operation", ["file or path cue detected"]
    if any(k in lowered for k in ("summarize", "briefing", "report", "analyze")) or any(
        k in text for k in ("\u603b\u7ed3", "\u7b80\u62a5", "\u5206\u6790", "\u6c47\u62a5")
    ):
        return "knowledge_work", ["analysis or reporting cue detected"]
    for candidate in capability_candidates or []:
        task_type = str(candidate.get("task_type") or "")
        score = float(candidate.get("score") or candidate.get("confidence") or 0)
        if task_type and score >= 0.62:
            reasons.append(f"capability candidate selected task_type={task_type} score={score:.2f}")
            return task_type, reasons
    return "general_conversation", ["no specialized goal cue was strong enough"]


def _extract_entities(text: str, task_type: str) -> dict[str, Any]:
    entities: dict[str, Any] = {}
    if task_type == "message_delivery":
        match = _SEND_TO_RE.search(text) or _SEND_RE.search(text) or _CHINESE_SEND_RE.search(text)
        if match:
            recipient = (match.groupdict().get("recipient") or "").strip(" ,.;:\u3002\uff0c")
            message = (match.groupdict().get("message") or "").strip(" ,.;:\u3002\uff0c")
            if recipient:
                entities["recipients"] = [recipient]
            if message:
                entities["message"] = message
    if task_type == "app_control":
        match = _APP_RE.search(text)
        if match:
            entities["app"] = match.group("app").strip(" ,.;:")
        else:
            for verb in ("\u6253\u5f00", "\u542f\u52a8", "\u5207\u6362", "\u5173\u95ed"):
                if verb in text:
                    tail = text.split(verb, 1)[1].strip(" \t\uff0c,.;:")
                    if tail:
                        entities["app"] = tail.split()[0].strip(" \t\uff0c,.;:")
                    break
        if re.search(r"\b(close|quit)\b", text, re.I) or "\u5173\u95ed" in text:
            entities["action"] = "close"
        elif re.search(r"\b(switch|focus)\b", text, re.I) or "\u5207\u6362" in text:
            entities["action"] = "switch"
        else:
            entities["action"] = "open"
    calc_match = _CALC_RE.search(text)
    if calc_match:
        entities["expression"] = calc_match.group("expr").replace("x", "*").replace("X", "*")
    path_match = _PATH_RE.search(text)
    if path_match:
        entities["path"] = path_match.group("path").strip()
    return entities


def _extract_constraints(text: str) -> dict[str, Any]:
    lowered = text.lower()
    constraints: dict[str, Any] = {}
    time_match = _TIME_RE.search(text)
    if time_match:
        constraints["time_range"] = {"amount": int(time_match.group("num")), "unit": time_match.group("unit").lower()}
    if "dry-run" in lowered or "dry run" in lowered or "\u53ea\u6f14\u7ec3" in text:
        constraints["dry_run"] = True
    if "markdown" in lowered:
        constraints["format"] = "markdown"
    if "\u6309\u6761" in text or "bullet" in lowered:
        constraints["format"] = "bullets"
    if "\u4e0d\u8981\u53d1\u9001" in text or "do not send" in lowered:
        constraints["send_allowed"] = False
    return constraints


def _input_adapter_constraints(envelope: AgentInputEnvelope | dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(envelope, AgentInputEnvelope):
        return {}, []
    evidence = envelope.modality_evidence or {}
    adapter = evidence.get("input_adapter") if isinstance(evidence.get("input_adapter"), dict) else {}
    voice_norm = evidence.get("voice_language_normalization") if isinstance(evidence.get("voice_language_normalization"), dict) else {}
    constraints: dict[str, Any] = {"input_source": envelope.source.value}
    rationale: list[str] = []
    if adapter:
        constraints["input_adapter"] = {
            "source": adapter.get("source") or envelope.source.value,
            "changed": bool(adapter.get("changed")),
            "steps": adapter.get("steps") or [],
        }
        rationale.append(
            f"InputAdapter source={adapter.get('source') or envelope.source.value} changed={bool(adapter.get('changed'))}."
        )
    if envelope.source.value == "voice":
        constraints["voice_raw_text"] = envelope.raw_text
        if voice_norm:
            constraints["voice_language"] = {
                "pending_confirmation_detected": bool(voice_norm.get("pending_confirmation_detected")),
                "pending_cancellation_detected": bool(voice_norm.get("pending_cancellation_detected")),
                "corrections": (voice_norm.get("correction") or {}).get("corrections") or [],
                "suspect_tokens": (voice_norm.get("correction") or {}).get("suspect_tokens") or [],
            }
            rationale.append("Voice Input Adapter evidence attached to goal interpretation.")
    return constraints, rationale


def _missing_info(task_type: str, entities: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if task_type == "message_delivery":
        if not entities.get("recipients"):
            missing.append("recipient")
        if not entities.get("message"):
            missing.append("message")
    elif task_type == "app_control":
        if not entities.get("app") and entities.get("action") != "close":
            missing.append("app")
    elif task_type == "calculator_calculate":
        if not entities.get("expression"):
            missing.append("expression")
    elif task_type == "file_operation":
        if not entities.get("path"):
            missing.append("path")
    return missing


def _risk_factors(task_type: str, text: str, constraints: dict[str, Any], missing: list[str]) -> list[str]:
    lowered = text.lower()
    risks: list[str] = []
    if task_type == "message_delivery" and constraints.get("send_allowed") is not False:
        risks.append("external_message")
    if any(k in lowered for k in ("delete", "remove", "overwrite", "move")) or any(
        k in text for k in ("\u5220\u9664", "\u8986\u76d6", "\u79fb\u52a8")
    ):
        risks.append("destructive_file_operation")
    if missing:
        risks.append("missing_required_info")
    if any(k in lowered for k in ("password", "token", "secret")):
        risks.append("sensitive_data")
    return risks


def interpret_goal(
    envelope: AgentInputEnvelope | dict[str, Any],
    *,
    state_snapshot: StateSnapshot | None = None,
    memory_bundle: RelevantMemoryBundle | None = None,
    capability_candidates: list[dict[str, Any]] | None = None,
) -> GoalInterpretation:
    raw, normalized = _text(envelope)
    turn_id = _turn_id(envelope)
    text = normalized or raw
    task_type, rationale = _infer_task_type(text, capability_candidates)
    entities = _extract_entities(text, task_type)
    constraints = _extract_constraints(text)
    input_constraints, input_rationale = _input_adapter_constraints(envelope)
    constraints.update(input_constraints)
    rationale.extend(input_rationale)
    if state_snapshot and task_type == "app_control" and entities.get("action") == "close" and not entities.get("app"):
        active = state_snapshot.active_window or {}
        inferred_app = active.get("app") or active.get("app_name") or active.get("process")
        if inferred_app:
            entities["app"] = inferred_app
            rationale.append("close target inferred from active window")
    if memory_bundle and memory_bundle.resolved_references:
        constraints["memory_resolved_references"] = [dict(ref) for ref in memory_bundle.resolved_references[:5]]
    missing = _missing_info(task_type, entities)
    risks = _risk_factors(task_type, text, constraints, missing)
    required_capabilities = _candidate_capability_ids(capability_candidates)
    confidence = 0.52
    if task_type != "general_conversation":
        confidence += 0.18
    if required_capabilities:
        confidence += 0.12
    if entities:
        confidence += 0.12
    if missing:
        confidence -= 0.18
    confidence = max(0.05, min(0.98, confidence))
    interpretation = GoalInterpretation(
        goal_id=_stable_id(turn_id, text),
        turn_id=turn_id,
        raw_text=raw,
        normalized_goal=text.strip(),
        primary_goal=text.strip(),
        task_type=task_type,
        entities=entities,
        constraints=constraints,
        missing_information=missing,
        risk_factors=risks,
        required_capabilities=required_capabilities,
        confidence=confidence,
        rationale=rationale,
    )
    append_event("goal_interpretation_finished", turn_id, interpretation.to_dict())
    return interpretation
