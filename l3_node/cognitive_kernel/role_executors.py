"""Role-agent execution adapters for WorkOrder dispatch.

Role adapters own per-role policy, ledger logging, verification evidence, and
failure shaping. Unknown tools still pass through a generic low-level transport,
but every call is wrapped in a WorkOrder and a RoleExecution event.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import html
import urllib.parse
import urllib.request
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
            result = RoleExecutionResult(
                observation=str(observation or ""),
                adapter_role=self.role_id,
                elapsed_ms=elapsed_ms,
                ok=True,
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
        evidence: dict[str, object] = {
            "strategy": self.adapter_kind,
            "tool": context.tool,
            "risk_level": str(work_order.tool_policy.risk_level.value),
        }
        governance = work_order.inputs.get("governance_policy")
        if isinstance(governance, dict):
            evidence["governance_policy"] = {
                "capability": governance.get("capability") or governance.get("capability_id") or "",
                "score": governance.get("score"),
                "level": governance.get("level") or "",
                "execution_mode": governance.get("execution_mode") or "",
                "requires_confirmation": bool(governance.get("requires_confirmation")),
            }
        return evidence

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        return evidence


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
            ["active_window", "foreground", "window_title", "hwnd", "screenshot", "ok"],
        )
        return enriched


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


class BrowserExecutor(RoleExecutionAdapter):
    role_id = "BrowserExecutorAgent"
    adapter_kind = "browser_research"

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        tool = (context.tool or "").strip()
        if tool == "mcp:tavily_search":
            return await _run_sync(lambda: _native_tavily_search(context.work_order_input, context.goal))
        if tool == "mcp:fetch":
            return await _run_sync(lambda: _native_fetch_pages(context.work_order_input))
        if tool != "core:web_research_summarize":
            return await tool_transport_executor(work_order)
        payload = _json_obj(context.work_order_input)
        args = payload if isinstance(payload, dict) else {}
        query = str(args.get("query") or context.goal or "").strip()
        recipients = _decode_json_list(args.get("recipients_json"))
        upstream = args.get("upstream_observations") if isinstance(args.get("upstream_observations"), list) else []
        message = _web_research_summary_message(query=query, recipients=recipients, upstream_observations=upstream)
        if not _summary_has_sources(message):
            return json.dumps(
                {
                    "ok": False,
                    "task": "web_research_summarize",
                    "channel": "BrowserExecutorAgent.native",
                    "query": query,
                    "recipients": recipients,
                    "message": message,
                    "error": "missing_search_or_fetch_evidence",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "ok": True,
                "task": "web_research_summarize",
                "channel": "BrowserExecutorAgent.native",
                "query": query,
                "recipients": recipients,
                "message": message,
                "source_node": str(args.get("source_node") or ""),
                "format": str(args.get("format") or "brief_lark_message"),
                "evidence": {
                    "summary_generated": True,
                    "grounded_in_upstream_results": True,
                },
            },
            ensure_ascii=False,
        )

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        payload = _json_obj(context.work_order_input)
        query = str((payload or {}).get("query") if isinstance(payload, dict) else "")
        return {
            **super().describe_evidence(work_order, context),
            "query_preview": query[:240],
            "expected_evidence": ["search_results", "fetched_pages", "summary_with_sources"],
        }

    def enrich_evidence(
        self,
        evidence: dict[str, object],
        observation: str,
        work_order: WorkOrder,
        context: RoleExecutionContext,
    ) -> dict[str, object]:
        enriched = dict(evidence)
        enriched["browser_result"] = _parse_observation_status(observation)
        parsed = _json_obj(observation)
        if isinstance(parsed, dict):
            enriched["summary_preview"] = str(parsed.get("message") or parsed.get("summary") or "")[:500]
        enriched["direct_browser_channel"] = "BrowserExecutorAgent.native" in str(observation or "")
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
        enriched["post_send_verified"] = _message_observation_has_delivery_evidence(observation)
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
        BrowserExecutor(),
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


def _message_error_retryable(reason: str) -> bool:
    low = (reason or "").lower()
    return any(x in low for x in ("timeout", "connection", "temporarily", "busy", "retry"))


def _message_observation_has_delivery_evidence(observation: str) -> bool:
    text = str(observation or "").strip()
    if not text:
        return False
    parsed = _json_obj(text)
    if isinstance(parsed, (dict, list)):
        return _message_delivery_evidence_in_json(parsed)
    low = text.lower()
    return any(
        marker in low
        for marker in (
            "message_id",
            "send_ok",
            "sent_and_verified_with_visual",
            "message_visible",
            "post_send_verified",
            "ocr",
            "screenshot",
        )
    )


def _message_delivery_evidence_in_json(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("duplicate_skipped") is True:
            return False
        if str(value.get("message_id") or "").strip():
            return True
        if value.get("send_ok") is True or value.get("sent") is True:
            return True
        if value.get("message_visible") is True and (value.get("recipient_visible") is True or value.get("screenshot") or value.get("screenshots")):
            return True
        detail = str(value.get("detail") or "").lower()
        if detail in {"sent_and_verified_with_visual", "message_sent", "send_ok", "sent"}:
            return True
        status = str(value.get("status") or "").lower()
        if status in {"sent", "message_sent", "send_ok"}:
            return True
        for key in ("evidence", "deliveries", "delivery", "result", "send_result", "visual", "screenshots"):
            nested = value.get(key)
            if _message_delivery_evidence_in_json(nested):
                return True
        return any(_message_delivery_evidence_in_json(v) for v in value.values() if isinstance(v, (dict, list)))
    if isinstance(value, list):
        return any(_message_delivery_evidence_in_json(item) for item in value)
    return False


def _decode_json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in re.split(r"[,，、;；]+", value) if item.strip()]
    return []


def _native_tavily_search(work_order_input: str, goal: str) -> str:
    payload = _json_obj(work_order_input)
    args = payload if isinstance(payload, dict) else {}
    query = str(args.get("query") or goal or "").strip()
    if not query:
        return json.dumps({"ok": False, "task": "tavily_search", "error": "missing_query"}, ensure_ascii=False)
    api_key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not api_key:
        return json.dumps(
            {"ok": False, "task": "tavily_search", "query": query, "error": "missing_TAVILY_API_KEY"},
            ensure_ascii=False,
        )
    body = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_answer": True,
            "include_raw_content": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return json.dumps(
            {"ok": False, "task": "tavily_search", "query": query, "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )
    try:
        data = json.loads(raw)
    except Exception:
        data = {"raw": raw}
    results = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "content": str(item.get("content") or "").strip()[:1000],
                "score": item.get("score"),
            }
        )
    return json.dumps(
        {
            "ok": bool(results),
            "task": "tavily_search",
            "channel": "BrowserExecutorAgent.native",
            "query": query,
            "answer": str(data.get("answer") or "").strip(),
            "results": results,
            "error": "" if results else "empty_search_results",
        },
        ensure_ascii=False,
    )


def _native_fetch_pages(work_order_input: str) -> str:
    payload = _json_obj(work_order_input)
    args = payload if isinstance(payload, dict) else {}
    urls: list[str] = []
    if args.get("url"):
        urls.append(str(args.get("url")))
    if isinstance(args.get("urls"), list):
        urls.extend(str(x) for x in args.get("urls") if str(x).strip())
    if not urls:
        urls = _urls_from_upstream(args.get("upstream_observations") if isinstance(args.get("upstream_observations"), list) else [])
    pages = []
    for url in urls[:3]:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 JachinBrowserExecutor/1.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(120_000).decode("utf-8", errors="replace")
            text = _html_to_text(raw)
            pages.append({"url": url, "title": _html_title(raw), "text": text[:2500], "ok": bool(text.strip())})
        except Exception as exc:
            pages.append({"url": url, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    ok_pages = [page for page in pages if page.get("ok")]
    return json.dumps(
        {
            "ok": bool(ok_pages),
            "task": "fetch",
            "channel": "BrowserExecutorAgent.native",
            "pages": pages,
            "error": "" if ok_pages else "no_readable_pages",
        },
        ensure_ascii=False,
    )


def _web_research_summary_message(
    *,
    query: str,
    recipients: list[str],
    upstream_observations: list[dict[str, object]] | None = None,
) -> str:
    query = str(query or "").strip() or "用户请求的最新信息"
    findings = _collect_research_findings(upstream_observations or [])
    if not findings:
        return f"【联网信息简报】\n主题：{query}\n状态：未拿到可引用的搜索/抓取证据，暂不发送未证实内容。"
    lines = [f"【{query}｜最新信息简报】"]
    for idx, item in enumerate(findings[:4], 1):
        title = _clean_research_text(str(item.get("title") or "来源").strip())
        content = _research_item_summary(item)
        url = str(item.get("url") or "").strip()
        if not content:
            content = "该来源包含相关信息，但正文抽取不足，建议打开链接查看原文。"
        if url:
            lines.append(f"{idx}. {title}：{content}\n链接：{url}")
        else:
            lines.append(f"{idx}. {title}：{content}")
    lines.append("以上由 Jachin 自动联网检索、抓取证据并整理。")
    return "\n".join(lines)


def _summary_has_sources(message: str) -> bool:
    return bool(re.search(r"https?://", str(message or "")))


def _collect_research_findings(upstream_observations: list[dict[str, object]]) -> list[dict[str, object]]:
    page_findings: list[dict[str, object]] = []
    search_findings: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in upstream_observations:
        text = str(item.get("observation") if isinstance(item, dict) else "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if not isinstance(obj, dict):
            continue
        for page in obj.get("pages") or []:
            if isinstance(page, dict) and page.get("ok"):
                url = str(page.get("url") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    page_findings.append(page)
        for result in obj.get("results") or []:
            if isinstance(result, dict):
                url = str(result.get("url") or "").strip()
                key = url or str(result.get("title") or "")
                if key and key not in seen:
                    seen.add(key)
                    search_findings.append(result)
    return page_findings + search_findings


def _urls_from_upstream(upstream_observations: list[dict[str, object]]) -> list[str]:
    urls: list[str] = []
    for item in upstream_observations:
        text = str(item.get("observation") if isinstance(item, dict) else "")
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            for result in obj.get("results") or []:
                if isinstance(result, dict):
                    url = str(result.get("url") or "").strip()
                    if url and url not in urls:
                        urls.append(url)
        for url in re.findall(r"https?://[^\s\"'<>]+", text):
            clean = url.rstrip(").,;，。")
            if clean and clean not in urls:
                urls.append(clean)
    return urls


def _html_title(raw: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", raw or "", re.I | re.S)
    return _compact_ws(_strip_tags(m.group(1))) if m else ""


def _html_to_text(raw: str) -> str:
    text = re.sub(
        r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>|<svg.*?</svg>",
        " ",
        raw or "",
    )
    text = _strip_tags(text)
    return _clean_research_text(text)


def _strip_tags(text: str) -> str:
    return re.sub(r"(?s)<[^>]+>", " ", text or "")


def _compact_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _research_item_summary(item: dict[str, object], *, max_chars: int = 140) -> str:
    text = str(item.get("text") or item.get("content") or "").strip()
    text = _clean_research_text(text)
    text = _drop_noisy_prefix(text)
    return _complete_sentence_excerpt(text, max_chars=max_chars)


def _clean_research_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    # Tavily/search snippets can contain URL-encoded SVG/CSS fragments.
    for _ in range(2):
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<svg.*?</svg>", " ", text)
    text = _strip_tags(text)
    text = re.sub(r"\.(?:st|cls|style)\d+\s*\{[^}]*\}", " ", text)
    text = re.sub(r"\b(?:fill|stroke|path|defs|viewBox|xmlns|clipPath)\s*[:=]\s*[^，。；;\s]+", " ", text, flags=re.I)
    text = re.sub(r"\[[^\]]{0,80}\]\((https?://[^)]+)\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace("\\*", "").replace("*", "")
    return _compact_ws(text)


def _drop_noisy_prefix(text: str) -> str:
    parts = re.split(r"(?<=[。！？!?])\s+|(?<=\.)\s+", text)
    good_parts = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if any(noise in low for noise in ("aibase -->", ".st0", "{ fill", "svg", "defs", "xmlns")):
            continue
        if len(re.findall(r"[A-Za-z0-9%#{}<>/=:;]", p)) > max(12, len(p) * 0.45) and not re.search(r"[\u4e00-\u9fff]", p):
            continue
        good_parts.append(p)
    return _compact_ws(" ".join(good_parts) if good_parts else text)


def _complete_sentence_excerpt(text: str, *, max_chars: int) -> str:
    text = _compact_ws(text)
    if not text:
        return ""
    sentences = _split_sentences(text)
    selected: list[str] = []
    total = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            sentence = _trim_to_word_boundary(sentence, max_chars=max_chars)
        if selected and total + len(sentence) + 1 > max_chars:
            break
        selected.append(sentence)
        total += len(sentence) + 1
        if total >= max_chars * 0.72:
            break
    result = _compact_ws(" ".join(selected)) if selected else _trim_to_word_boundary(text, max_chars=max_chars)
    if result and result[-1] not in "。.!！?？":
        result += "。"
    return result


def _split_sentences(text: str) -> list[str]:
    chunks = re.findall(r"[^。！？!?\.]+[。！？!?\.]?", text)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _trim_to_word_boundary(text: str, *, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    boundary = max(cut.rfind("，"), cut.rfind(","), cut.rfind("；"), cut.rfind(";"), cut.rfind(" "))
    if boundary >= max(24, int(max_chars * 0.45)):
        cut = cut[:boundary].rstrip()
    return cut.rstrip("，,；;：:")


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
