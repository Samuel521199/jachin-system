"""Role-agent execution adapters for WorkOrder dispatch.

Role adapters own per-role policy, ledger logging, verification evidence, and
failure shaping. Unknown tools still pass through a generic low-level transport,
but every call is wrapped in a WorkOrder and a RoleExecution event.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .contracts import WorkOrder
from .ledger import append_event

logger = logging.getLogger(__name__)

ToolTransportExecutor = Callable[[WorkOrder], Awaitable[str]]
_MESSAGE_SEND_DEDUPE: set[str] = set()


@dataclass(slots=True)
class RoleExecutionContext:
    turn_id: str
    goal: str
    tool: str
    role_id: str
    work_order_input: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "goal": self.goal,
            "tool": self.tool,
            "role_id": self.role_id,
            "work_order_input_len": len(self.work_order_input or ""),
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class RoleExecutionResult:
    observation: str
    adapter_role: str
    elapsed_ms: float
    ok: bool
    failure_reason: str = ""
    evidence: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_len": len(self.observation or ""),
            "observation_preview": (self.observation or "")[:800],
            "adapter_role": self.adapter_role,
            "elapsed_ms": self.elapsed_ms,
            "ok": self.ok,
            "failure_reason": self.failure_reason,
            "evidence": self.evidence,
        }


class RoleExecutionAdapter:
    role_id = "ToolExecutionAgent"
    adapter_kind = "role_adapter"

    async def execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> RoleExecutionResult:
        started = time.perf_counter()
        evidence = self.describe_evidence(work_order, context)
        try:
            from l3_node.terminal_turn_debug_log import log_role_agent_execution_detail

            log_role_agent_execution_detail(
                phase="started",
                role_id=self.role_id,
                adapter_kind=self.adapter_kind,
                work_order=work_order,
                context=context,
                evidence=evidence,
            )
        except Exception:
            pass
        append_event(
            "role_execution_started",
            context.turn_id,
            {
                "role_id": self.role_id,
                "adapter_kind": self.adapter_kind,
                "work_order_id": work_order.work_order_id,
                "tool": context.tool,
                "evidence": evidence,
            },
        )
        try:
            self.preflight(work_order, context)
            observation = await self._execute(work_order, tool_transport_executor, context)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            evidence = self.enrich_evidence(evidence, observation, work_order, context)
            ok, failure_reason = self.evaluate_observation(observation, evidence, work_order, context)
            result = RoleExecutionResult(
                observation=str(observation or ""),
                adapter_role=self.role_id,
                elapsed_ms=elapsed_ms,
                ok=ok,
                failure_reason=failure_reason,
                evidence=evidence,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            result = RoleExecutionResult(
                observation=f"[{self.role_id} failed] {type(exc).__name__}: {exc}",
                adapter_role=self.role_id,
                elapsed_ms=elapsed_ms,
                ok=False,
                failure_reason=str(exc),
                evidence=evidence,
            )
        append_event(
            "role_execution_finished",
            context.turn_id,
            {
                "role_id": self.role_id,
                "adapter_kind": self.adapter_kind,
                "work_order_id": work_order.work_order_id,
                "tool": context.tool,
                **result.to_dict(),
            },
        )
        try:
            from l3_node.terminal_turn_debug_log import log_role_agent_execution_detail

            log_role_agent_execution_detail(
                phase="finished",
                role_id=self.role_id,
                adapter_kind=self.adapter_kind,
                work_order=work_order,
                context=context,
                result=result,
                evidence=result.evidence,
            )
        except Exception:
            pass
        logger.debug(
            "[CognitiveKernel][%s] executed tool=%s ok=%s elapsed_ms=%.1f",
            self.role_id,
            context.tool,
            result.ok,
            result.elapsed_ms,
        )
        return result

    def preflight(self, work_order: WorkOrder, context: RoleExecutionContext) -> None:
        if work_order.role_agent != self.role_id:
            raise ValueError(f"work_order role mismatch: {work_order.role_agent} != {self.role_id}")

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        return await tool_transport_executor(work_order)

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        return {
            "strategy": self.adapter_kind,
            "tool": context.tool,
            "risk_level": str(work_order.tool_policy.risk_level.value),
        }

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        return evidence

    def evaluate_observation(
        self,
        observation: str,
        evidence: dict[str, object],
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> tuple[bool, str]:
        status = _parse_observation_status(observation)
        if not bool(status.get("ok")):
            return False, str(status.get("reason") or "adapter_observation_failed")[:500]
        return True, ""


class AppControlExecutor(RoleExecutionAdapter):
    role_id = "AppControlExecutorAgent"
    adapter_kind = "app_control"

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        tool = (context.tool or "").strip()
        payload = _json_obj(context.work_order_input)
        args = payload if isinstance(payload, dict) else {}
        if not context.metadata.get("mainline"):
            return await tool_transport_executor(work_order)
        if tool == "mcp:windows_open_app":
            from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

            return await _run_sync(
                lambda: windows_uia_server.windows_open_app(
                    str(args.get("app_name") or args.get("name") or args.get("app") or ""),
                    str(args.get("args_json") or "[]"),
                    str(args.get("out_dir") or ""),
                )
            )
        if tool == "mcp:windows_window_switch":
            from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

            return await _run_sync(
                lambda: windows_uia_server.windows_window_switch(
                    str(args.get("keywords") or args.get("keyword") or args.get("window_title") or ""),
                    str(args.get("exclude_keywords") or ""),
                    float(args.get("timeout") or 5.0),
                    str(args.get("out_dir") or ""),
                )
            )
        if tool == "mcp:windows_window_close":
            from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

            return await _run_sync(
                lambda: windows_uia_server.windows_window_close(
                    str(args.get("keywords") or args.get("keyword") or args.get("window_title") or ""),
                    str(args.get("exclude_keywords") or ""),
                    float(args.get("timeout") or 5.0),
                    str(args.get("out_dir") or ""),
                )
            )
        return await tool_transport_executor(work_order)

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        return {
            **super().describe_evidence(work_order, context),
            "preflight": "window_or_app_control_request",
            "window_hints": _extract_window_like_values(context.work_order_input),
            "expected_evidence": ["active_window", "screenshot", "process_or_window_title"],
        }

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        enriched = dict(evidence)
        enriched["app_control_result"] = _parse_observation_status(observation)
        enriched["foreground_verified"] = _observation_mentions_any(
            observation,
            ["active_window", "foreground", "window_title", "active_title", "hwnd", "screenshot"],
        )
        return enriched

    def evaluate_observation(
        self,
        observation: str,
        evidence: dict[str, object],
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> tuple[bool, str]:
        ok, reason = super().evaluate_observation(observation, evidence, work_order, context)
        if not ok:
            return ok, reason
        if evidence.get("foreground_verified") is False:
            return False, "app_control_foreground_unverified"
        return True, ""


class FileExecutor(RoleExecutionAdapter):
    role_id = "FileExecutorAgent"
    adapter_kind = "file"

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        return {
            **super().describe_evidence(work_order, context),
            "paths": _extract_path_like_values(context.work_order_input),
            "expected_evidence": ["file_path", "exists_or_changed", "operation_result"],
        }

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        tool = (context.tool or "").strip()
        if tool == "core:fs_read":
            path = _extract_single_path(context.work_order_input)
            if not path:
                raise ValueError("core:fs_read requires path or file_path")
            from core.native_tools import core_fs_read

            content = await _run_sync(lambda: core_fs_read(path))
            return json.dumps(
                {
                    "ok": True,
                    "channel": "FileExecutorAgent.native",
                    "operation": "read",
                    "path": path,
                    "content": content,
                    "content_len": len(str(content or "")),
                },
                ensure_ascii=False,
            )
        if context.metadata.get("mainline") and tool in {
            "mcp:windows_file_open",
            "mcp:windows_file_reveal_in_explorer",
        }:
            path = _extract_single_path(context.work_order_input)
            if not path:
                raise ValueError(f"{tool} requires path or file_path")
            from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

            if tool == "mcp:windows_file_open":
                return await _run_sync(lambda: windows_uia_server.windows_file_open(path, ""))
            return await _run_sync(lambda: windows_uia_server.windows_file_reveal_in_explorer(path, ""))
        if tool == "core:fs_write":
            path, content = _extract_write_payload(context.work_order_input)
            if not path:
                raise ValueError("core:fs_write requires path or file_path")
            from core.native_tools import core_fs_write

            resolved_path = _resolve_effective_workspace_path(path)
            existed_before = resolved_path.exists()
            await _run_sync(lambda: core_fs_write(path, content))
            exists_after = resolved_path.exists()
            return json.dumps(
                {
                    "ok": True,
                    "channel": "FileExecutorAgent.native",
                    "operation": "write",
                    "path": path,
                    "resolved_path": str(resolved_path),
                    "content_len": len(content),
                    "existed_before": existed_before,
                    "exists_after": exists_after,
                },
                ensure_ascii=False,
            )
        return await tool_transport_executor(work_order)

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        enriched = dict(evidence)
        enriched["file_result"] = _parse_observation_status(observation)
        enriched["direct_native_channel"] = "FileExecutorAgent.native" in str(observation or "")
        return enriched


class MessageExecutor(RoleExecutionAdapter):
    role_id = "MessageExecutorAgent"
    adapter_kind = "message"

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        preview = _extract_message_preview(context.work_order_input)
        dedupe_key = _message_dedupe_key(work_order, context)
        return {
            **super().describe_evidence(work_order, context),
            "recipient_hints": _extract_recipient_like_values(context.work_order_input),
            "send_preview": preview[:300],
            "preview_len": len(preview),
            "dedupe_key": dedupe_key,
            "expected_evidence": ["recipient", "message_preview", "send_status"],
        }

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        self._validate_send_payload(context)
        dedupe_key = _message_dedupe_key(work_order, context)
        if context.metadata.get("mainline") and dedupe_key in _MESSAGE_SEND_DEDUPE:
            return json.dumps(
                {
                    "ok": True,
                    "duplicate_skipped": True,
                    "channel": "MessageExecutorAgent.dedupe",
                    "dedupe_key": dedupe_key,
                    "recipients": _extract_recipient_like_values(context.work_order_input),
                },
                ensure_ascii=False,
            )
        first = await tool_transport_executor(work_order)
        status = _parse_observation_status(first)
        if status.get("ok") is False and _message_error_retryable(str(status.get("reason") or "")):
            append_event(
                "role_execution_retry",
                context.turn_id,
                {
                    "role_id": self.role_id,
                    "work_order_id": work_order.work_order_id,
                    "tool": context.tool,
                    "reason": status.get("reason") or "retryable_message_failure",
                },
            )
            second = await tool_transport_executor(work_order)
            second_status = _parse_observation_status(second)
            if second_status.get("ok") is not False:
                _MESSAGE_SEND_DEDUPE.add(dedupe_key)
            return second
        if status.get("ok") is not False:
            _MESSAGE_SEND_DEDUPE.add(dedupe_key)
        return first

    def _validate_send_payload(self, context: RoleExecutionContext) -> None:
        recipients = _extract_recipient_like_values(context.work_order_input)
        message = _extract_message_preview(context.work_order_input)
        low_tool = (context.tool or "").lower()
        if any(x in low_tool for x in ("lark", "send", "smtp", "post", "publish")):
            if not recipients and "publish" not in low_tool and "upload" not in low_tool:
                raise ValueError("message send requires recipient/chat_id/to")
            if not message and "upload" not in low_tool:
                raise ValueError("message send requires non-empty message/text/content")

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        enriched = dict(evidence)
        enriched["send_result"] = _parse_observation_status(observation)
        enriched["dedupe_key"] = _message_dedupe_key(work_order, context)
        enriched["duplicate_skipped"] = "duplicate_skipped" in str(observation or "")
        enriched["post_send_verified"] = _observation_mentions_any(
            observation,
            ["sent", "send_ok", "message_id", "success", "ocr", "screenshot", "ok"],
        )
        return enriched


class MemoryRecallExecutor(RoleExecutionAdapter):
    role_id = "MemoryRecallAgent"
    adapter_kind = "memory_recall"

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        query = _extract_memory_query(context.work_order_input)
        return {
            **super().describe_evidence(work_order, context),
            "memory_query_preview": query[:300],
            "memory_query_len": len(query),
            "expected_evidence": ["memory_hits", "memory_gaps", "conflict_markers"],
        }

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        tool = (context.tool or "").strip()
        query = _extract_memory_query(context.work_order_input)
        if tool == "recall_memory":
            from l3_node.cognitive_kernel.memory_tools import recall_memory_search

            return await recall_memory_search(query)
        if tool == "core:local_memory_search":
            try:
                from l3_node.local_memory_search import (
                    async_search_local_memories,
                    get_local_memory_search_timeout_sec,
                    parse_core_local_memory_search_work_order_input,
                )

                kwargs = parse_core_local_memory_search_work_order_input(context.work_order_input)
                timeout = get_local_memory_search_timeout_sec()
                result = await __import__("asyncio").wait_for(
                    async_search_local_memories(**kwargs),
                    timeout=timeout + max(2.0, timeout * 0.1 + 1.0),
                )
                if isinstance(result, dict) and result.get("ok") and result.get("hits"):
                    try:
                        from l3_node.local_memory import touch_entries_from_search_hits

                        touch_entries_from_search_hits(list(result["hits"]))
                    except Exception:
                        pass
                if isinstance(result, dict):
                    result.setdefault("channel", "MemoryRecallAgent.native")
                return json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as exc:
                return json.dumps(
                    {
                        "ok": False,
                        "channel": "MemoryRecallAgent.native",
                        "error": "memory_recall_failed",
                        "message": str(exc),
                        "hits": [],
                    },
                    ensure_ascii=False,
                )
        return await tool_transport_executor(work_order)

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        enriched = dict(evidence)
        parsed = _json_obj(observation)
        hits = []
        if isinstance(parsed, dict):
            raw_hits = parsed.get("hits") or parsed.get("results") or parsed.get("memories") or []
            hits = raw_hits if isinstance(raw_hits, list) else []
        enriched["memory_result"] = _parse_observation_status(observation)
        enriched["hit_count"] = len(hits)
        enriched["direct_memory_channel"] = "MemoryRecallAgent.native" in str(observation or "")
        return enriched


class MemoryWriteExecutor(RoleExecutionAdapter):
    role_id = "MemoryWriteAgent"
    adapter_kind = "memory_write"

    def preflight(self, work_order: WorkOrder, context: RoleExecutionContext) -> None:
        super().preflight(work_order, context)
        if context.tool == "core:local_memory_append" and not (context.work_order_input or "").strip():
            raise ValueError("memory append requires non-empty tool input")

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        content, tags = _extract_memory_payload(context.work_order_input)
        return {
            **super().describe_evidence(work_order, context),
            "memory_write_len": len(context.work_order_input or ""),
            "memory_content_len": len(content),
            "memory_tags": tags or [],
            "memory_class": _classify_memory_tags(tags),
            "expected_evidence": ["memory_write_result", "dedupe_or_append_status"],
        }

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        if context.tool != "core:local_memory_append":
            return await tool_transport_executor(work_order)
        content, tags = _extract_memory_payload(context.work_order_input)
        if not content:
            raise ValueError("memory append requires content/body/text")
        from l3_node.tools.core_local_memory_append import async_run_local_memory_append

        result = await async_run_local_memory_append(content=content, tags=tags)
        if isinstance(result, dict):
            result.setdefault("channel", "MemoryWriteAgent.native")
            result.setdefault("memory_class", _classify_memory_tags(tags))
        return json.dumps(result, ensure_ascii=False, indent=2)

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        enriched = dict(evidence)
        enriched["memory_result"] = _parse_observation_status(observation)
        enriched["direct_memory_channel"] = "MemoryWriteAgent.native" in str(observation or "")
        return enriched


class GenericToolTransportExecutor(RoleExecutionAdapter):
    role_id = "ToolExecutionAgent"
    adapter_kind = "role_adapter"

    def preflight(self, work_order: WorkOrder, context: RoleExecutionContext) -> None:
        if work_order.role_agent != self.role_id:
            # Generic transport may still be used as an explicit fallback.
            logger.debug(
                "[CognitiveKernel][ToolExecutionAgent] generic transport bridge for role=%s tool=%s",
                work_order.role_agent,
                context.tool,
            )


class RoleExecutorRegistry:
    def __init__(self, adapters: list[RoleExecutionAdapter] | None = None) -> None:
        self._adapters: dict[str, RoleExecutionAdapter] = {}
        for adapter in adapters or default_role_executors():
            self.register(adapter)

    def register(self, adapter: RoleExecutionAdapter) -> None:
        self._adapters[adapter.role_id] = adapter

    def get(self, role_id: str) -> RoleExecutionAdapter:
        return self._adapters.get(role_id) or self._adapters["ToolExecutionAgent"]

    async def execute(
        self,
        role_id: str,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> RoleExecutionResult:
        return await self.get(role_id).execute(work_order, tool_transport_executor, context)


def default_role_executors() -> list[RoleExecutionAdapter]:
    return [
        AppControlExecutor(),
        FileExecutor(),
        MessageExecutor(),
        MemoryRecallExecutor(),
        MemoryWriteExecutor(),
        GenericToolTransportExecutor(),
    ]


DEFAULT_ROLE_EXECUTOR_REGISTRY = RoleExecutorRegistry()


def get_default_role_executor_registry() -> RoleExecutorRegistry:
    return DEFAULT_ROLE_EXECUTOR_REGISTRY


def _extract_path_like_values(work_order_input: str) -> list[str]:
    obj = _json_obj(work_order_input)
    if not isinstance(obj, dict):
        return []
    keys = ("path", "file_path", "source", "target", "src", "dst", "directory", "cwd")
    out: list[str] = []
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out[:8]


def _resolve_effective_workspace_path(path: str) -> Path:
    p = Path(path).expanduser()
    if p.is_absolute():
        return p.resolve()
    try:
        from l3_node.workspace_context import get_effective_workspace_root

        return (get_effective_workspace_root() / p).resolve()
    except Exception:
        return p.resolve()


def _extract_single_path(work_order_input: str) -> str:
    obj = _json_obj(work_order_input)
    if isinstance(obj, dict):
        for key in ("path", "file_path", "target", "source"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raw = (work_order_input or "").strip()
    if raw and not raw.startswith("{"):
        return raw.strip('"').strip("'")
    return ""


def _extract_write_payload(work_order_input: str) -> tuple[str, str]:
    obj = _json_obj(work_order_input)
    if isinstance(obj, dict):
        path = ""
        for key in ("path", "file_path", "target"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                path = value.strip()
                break
        content = obj.get("content")
        if content is None:
            content = obj.get("text")
        if content is None:
            content = obj.get("body")
        return path, str(content or "")
    return "", ""


def _extract_recipient_like_values(work_order_input: str) -> list[str]:
    obj = _json_obj(work_order_input)
    if not isinstance(obj, dict):
        return []
    keys = ("recipient", "recipients", "recipients_json", "chat_id", "chat_ids", "user", "users", "to")
    out: list[str] = []
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            if key == "recipients_json":
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        out.extend(str(v) for v in parsed if str(v))
                    else:
                        out.append(value)
                except Exception:
                    out.append(value)
            else:
                out.append(value)
        elif isinstance(value, list):
            out.extend(str(v) for v in value if str(v))
    return out[:12]


def _extract_window_like_values(work_order_input: str) -> list[str]:
    obj = _json_obj(work_order_input)
    if not isinstance(obj, dict):
        raw = (work_order_input or "").strip()
        return [raw] if raw else []
    keys = ("app", "app_name", "window", "window_title", "keywords", "title", "name")
    out: list[str] = []
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out[:8]


def _extract_message_preview(work_order_input: str) -> str:
    obj = _json_obj(work_order_input)
    if isinstance(obj, dict):
        for key in ("message", "text", "content", "body", "summary"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raw = (work_order_input or "").strip()
    if raw and not raw.startswith("{"):
        return raw[:500]
    return ""


def _message_dedupe_key(work_order: WorkOrder, context: RoleExecutionContext) -> str:
    recipients = sorted(x.strip().lower() for x in _extract_recipient_like_values(context.work_order_input) if x.strip())
    preview = _extract_message_preview(context.work_order_input).strip()
    digest = hashlib.sha256(
        json.dumps(
            {
                "work_order_id": work_order.work_order_id,
                "recipients": recipients,
                "message": preview,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"msg:{digest}"


def _extract_memory_payload(work_order_input: str) -> tuple[str, list[str] | None]:
    try:
        from l3_node.tools.core_local_memory_append import parse_core_local_memory_append_work_order_input

        return parse_core_local_memory_append_work_order_input(work_order_input)
    except Exception:
        obj = _json_obj(work_order_input)
        if isinstance(obj, dict):
            content = str(obj.get("content") or obj.get("body") or obj.get("text") or "").strip()
            tags_obj = obj.get("tags")
            tags: list[str] | None = None
            if isinstance(tags_obj, str):
                tags = [x.strip() for x in tags_obj.split(",") if x.strip()]
            elif isinstance(tags_obj, list):
                tags = [str(x).strip() for x in tags_obj if str(x).strip()]
            return content, tags
        return (work_order_input or "").strip(), None


def _extract_memory_query(work_order_input: str) -> str:
    obj = _json_obj(work_order_input)
    if isinstance(obj, dict):
        for key in ("query", "q", "text", "content", "keywords"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return (work_order_input or "").strip()


def _classify_memory_tags(tags: list[str] | None) -> str:
    joined = " ".join(tags or []).lower()
    if any(x in joined for x in ("preference", "persona", "user")):
        return "user_preference"
    if any(x in joined for x in ("project", "task", "action", "workflow")):
        return "short_term_action"
    if any(x in joined for x in ("correction", "failure", "recovery")):
        return "failure_hint"
    return "learned_skill"


def _parse_observation_status(observation: str) -> dict[str, object]:
    text = str(observation or "")
    unknown_reason = _unknown_tool_reason(text)
    if unknown_reason:
        return {
            "ok": False,
            "reason": unknown_reason,
            "json": False,
        }
    parsed = _json_obj(text)
    if isinstance(parsed, dict):
        ok = parsed.get("ok")
        if ok is None:
            ok = parsed.get("success")
        status = parsed.get("status")
        reason = parsed.get("error") or parsed.get("reason") or parsed.get("message") or status
        return {
            "ok": bool(ok) if ok is not None else str(status).lower() not in {"failed", "error"},
            "reason": str(reason or "")[:500],
            "json": True,
        }
    low = text.lower()
    fail_match = re.search(r"(traceback|exception|error|failed|timeout|not allowed|permission denied)", low)
    return {
        "ok": fail_match is None,
        "reason": fail_match.group(1) if fail_match else "",
        "json": False,
    }


def _unknown_tool_reason(text: str) -> str:
    raw = str(text or "")
    low = raw.lower()
    if "[未知工具" in raw or "未知工具" in raw:
        return "unknown_tool"
    if "unknown tool" in low:
        return "unknown_tool"
    if "未知 wasm 技能" in low or "未找到技能" in raw:
        return "unknown_tool"
    return ""


def _message_error_retryable(reason: str) -> bool:
    low = (reason or "").lower()
    return any(x in low for x in ("timeout", "connection", "temporarily", "busy", "retry"))


def _observation_mentions_any(observation: str, needles: list[str]) -> bool:
    low = str(observation or "").lower()
    return any(n.lower() in low for n in needles)


def _json_obj(work_order_input: str) -> object | None:
    try:
        return json.loads(work_order_input or "{}")
    except Exception:
        return None


async def _run_sync(fn):
    import asyncio

    return await asyncio.to_thread(fn)




