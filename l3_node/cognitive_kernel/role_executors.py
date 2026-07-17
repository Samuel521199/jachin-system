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
from typing import Awaitable, Callable, Any

from .contracts import WorkOrder
from .ledger import append_event
from .source_quality_memory import (
    domain_from_url,
    rank_findings_by_source_quality,
    rank_urls_by_source_quality,
    record_web_research_source_quality,
    source_reputation_for_url,
)

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
        execution_preference = work_order.inputs.get("execution_preference")
        if isinstance(execution_preference, dict) and execution_preference.get("source") != "none":
            evidence["execution_preference"] = {
                "source": execution_preference.get("source") or "",
                "selection_reason": execution_preference.get("selection_reason") or "",
                "selected_memory_id": execution_preference.get("selected_memory_id") or "",
                "selected_confidence": execution_preference.get("selected_confidence"),
                "selected_success_rate": execution_preference.get("selected_success_rate"),
                "preferred_execution_strategy": execution_preference.get("preferred_execution_strategy") or "",
                "preferred_work_order_chain": execution_preference.get("preferred_work_order_chain") or [],
                "execution_order_advice": execution_preference.get("execution_order_advice") or work_order.inputs.get("execution_order_advice") or {},
                "candidate_count": execution_preference.get("candidate_count") or 0,
            }
        candidate_tool_reliability = context.metadata.get("candidate_tool_reliability")
        if isinstance(candidate_tool_reliability, list) and candidate_tool_reliability:
            evidence["candidate_tool_reliability"] = candidate_tool_reliability[:8]
        selected_tool_reliability = context.metadata.get("selected_tool_reliability")
        if isinstance(selected_tool_reliability, dict) and selected_tool_reliability:
            evidence["selected_tool_reliability"] = selected_tool_reliability
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
        summary_payload = _web_research_summary_payload(
            query=query,
            recipients=recipients,
            upstream_observations=upstream,
            turn_id=context.turn_id,
        )
        message = str(summary_payload.get("message") or "")
        quality_report = summary_payload.get("quality_report") if isinstance(summary_payload.get("quality_report"), dict) else {}
        if not _summary_has_sources(message) or quality_report.get("send_ready") is not True:
            return json.dumps(
                {
                    "ok": False,
                    "task": "web_research_summarize",
                    "channel": "BrowserExecutorAgent.native",
                    "query": query,
                    "recipients": recipients,
                    "message": message,
                    "quality_report": quality_report,
                    "sources": summary_payload.get("sources") or [],
                    "error": str(quality_report.get("primary_issue") or "missing_search_or_fetch_evidence"),
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
                "quality_report": quality_report,
                "sources": summary_payload.get("sources") or [],
                "requires_send_confirmation": bool(summary_payload.get("requires_send_confirmation")),
                "send_preview": message[:1200],
                "source_node": str(args.get("source_node") or ""),
                "format": str(args.get("format") or "brief_lark_message"),
                "evidence": {
                    "summary_generated": True,
                    "grounded_in_upstream_results": True,
                    "quality_level": quality_report.get("quality_level"),
                    "source_count": quality_report.get("source_count"),
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
            if isinstance(parsed.get("quality_report"), dict):
                enriched["web_research_quality_report"] = parsed.get("quality_report")
            if isinstance(parsed.get("sources"), list):
                sources = parsed.get("sources") or []
                enriched["source_count"] = len(sources)
                enriched["source_quality"] = _source_quality_evidence(sources)
        enriched["direct_browser_channel"] = "BrowserExecutorAgent.native" in str(observation or "")
        return enriched


class MessageExecutor(RoleExecutionAdapter):
    role_id = "MessageExecutorAgent"
    adapter_kind = "message"

    def describe_evidence(self, work_order: WorkOrder, context: RoleExecutionContext) -> dict[str, object]:
        preview = _extract_message_preview(context.work_order_input)
        dedupe_key = _message_dedupe_key(work_order, context)
        quality_report = _extract_web_research_quality_report(context.work_order_input)
        return {
            **super().describe_evidence(work_order, context),
            "recipient_hints": _extract_recipient_like_values(context.work_order_input),
            "send_preview": preview[:300],
            "preview_len": len(preview),
            "dedupe_key": dedupe_key,
            "web_research_quality_report": quality_report,
            "send_preview_policy": _web_research_send_preview_policy(quality_report),
            "expected_evidence": ["recipient", "message_preview", "send_status"],
        }

    async def _execute(
        self,
        work_order: WorkOrder,
        tool_transport_executor: ToolTransportExecutor,
        context: RoleExecutionContext,
    ) -> str:
        self._validate_send_payload(context)
        delivery_mode = _message_delivery_mode(context.work_order_input)
        if delivery_mode == "dry_run":
            quality_report = _extract_web_research_quality_report(context.work_order_input)
            return json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "delivery_mode": "dry_run",
                    "channel": "MessageExecutorAgent.preview",
                    "recipients": _extract_recipient_like_values(context.work_order_input),
                    "message": _extract_message_preview(context.work_order_input),
                    "quality_report": quality_report,
                    "sources": _extract_sources_from_message_payload(context.work_order_input),
                    "dry_run_preview_verified": True,
                    "detail": "preview_generated_no_external_send",
                },
                ensure_ascii=False,
            )
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
        enriched["delivery_mode"] = _message_delivery_mode(context.work_order_input)
        enriched["dry_run_preview_verified"] = _message_observation_has_dry_run_preview(observation)
        enriched["post_send_verified"] = _message_observation_has_delivery_evidence(observation)
        quality_report = _extract_web_research_quality_report(context.work_order_input)
        if quality_report:
            enriched["web_research_quality_report"] = quality_report
            enriched["send_preview_policy"] = _web_research_send_preview_policy(quality_report)
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


def _extract_web_research_quality_report(work_order_input: str) -> dict[str, object]:
    obj = _json_obj(work_order_input)
    if not isinstance(obj, dict):
        return {}
    report = obj.get("quality_report") or obj.get("web_research_quality_report")
    if isinstance(report, dict):
        return dict(report)
    summary = obj.get("summary_result") if isinstance(obj.get("summary_result"), dict) else {}
    if isinstance(summary.get("quality_report"), dict):
        return dict(summary["quality_report"])
    upstream = obj.get("upstream_observations") if isinstance(obj.get("upstream_observations"), list) else []
    for item in reversed(upstream):
        if not isinstance(item, dict):
            continue
        text = str(item.get("observation") or "")
        parsed = _json_obj(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("quality_report"), dict):
            return dict(parsed["quality_report"])
    return {}


def _extract_sources_from_message_payload(work_order_input: str) -> list[object]:
    obj = _json_obj(work_order_input)
    if not isinstance(obj, dict):
        return []
    sources = obj.get("sources")
    return list(sources[:8]) if isinstance(sources, list) else []


def _message_delivery_mode(work_order_input: str) -> str:
    obj = _json_obj(work_order_input)
    if not isinstance(obj, dict):
        return "live_run"
    explicit = str(obj.get("delivery_mode") or "").strip().lower()
    if explicit in {"dry", "dry_run", "preview", "preview_only"}:
        return "dry_run"
    if explicit in {"live", "live_run", "send", "send_now"}:
        return "live_run"
    if obj.get("dry_run") is True or obj.get("send_allowed") is False:
        return "dry_run"
    return "live_run"


def _web_research_send_preview_policy(report: dict[str, object]) -> dict[str, object]:
    if not report:
        return {"applies": False, "requires_preview": False, "reason": "no_web_research_quality_report"}
    send_ready = report.get("send_ready") is True
    requires_preview = bool(report.get("requires_preview")) or _web_research_requires_send_confirmation(report)
    if not send_ready:
        reason = str(report.get("primary_issue") or "quality_gate_not_send_ready")
    elif requires_preview:
        reason = str(report.get("primary_issue") or "usable_with_preview")
    else:
        reason = "production_quality"
    return {
        "applies": True,
        "send_ready": send_ready,
        "requires_preview": requires_preview,
        "reason": reason,
        "quality_level": report.get("quality_level"),
        "score": report.get("score"),
        "source_count": report.get("source_count"),
        "issues": report.get("issues") if isinstance(report.get("issues"), list) else [],
    }


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


def _message_observation_has_dry_run_preview(observation: str) -> bool:
    parsed = _json_obj(observation)
    if isinstance(parsed, dict):
        return parsed.get("dry_run") is True and parsed.get("dry_run_preview_verified") is True
    return False


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
    urls = rank_urls_by_source_quality(urls)
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
    payload = _web_research_summary_payload(query=query, recipients=recipients, upstream_observations=upstream_observations)
    return str(payload.get("message") or "")


def _web_research_summary_payload(
    *,
    query: str,
    recipients: list[str],
    upstream_observations: list[dict[str, object]] | None = None,
    turn_id: str = "",
) -> dict[str, object]:
    query = str(query or "").strip() or "用户请求的最新信息"
    findings = _collect_research_findings(upstream_observations or [])
    summary_findings = _select_findings_for_summary(findings)
    if not findings:
        message = f"【联网信息简报】\n主题：{query}\n状态：未拿到可引用的搜索/抓取证据，暂不发送未证实内容。"
        report = _web_research_quality_report(message=message, query=query, page_summaries=[], findings=[])
        return {
            "ok": False,
            "message": message,
            "quality_report": report,
            "sources": [],
            "requires_send_confirmation": False,
        }
    page_summaries: list[dict[str, str]] = []
    for item in summary_findings:
        summary = _summarize_research_page(item, query=query)
        if not summary:
            continue
        if _looks_like_web_residue(summary.get("summary", "")) or _looks_like_web_residue(summary.get("title", "")):
            continue
        page_summaries.append(summary)
        if len(page_summaries) >= 4:
            break
    if not page_summaries:
        message = f"【联网信息简报】\n主题：{query}\n状态：搜索结果存在，但没有通过可读性与来源质量校验，暂不发送未证实内容。"
        report = _web_research_quality_report(message=message, query=query, page_summaries=[], findings=findings)
        sources = _web_research_sources_from_findings(findings)
        record_web_research_source_quality(
            query=query,
            quality_report=report,
            sources=sources,
            turn_id=turn_id,
        )
        return {
            "ok": False,
            "message": message,
            "quality_report": report,
            "sources": sources,
            "requires_send_confirmation": False,
        }

    message = _compose_final_human_brief(query=query, recipients=recipients, page_summaries=page_summaries)
    report = _web_research_quality_report(message=message, query=query, page_summaries=page_summaries, findings=findings)
    payload = {
        "ok": bool(report.get("send_ready")),
        "message": message,
        "quality_report": report,
        "sources": _web_research_sources(page_summaries),
        "requires_send_confirmation": _web_research_requires_send_confirmation(report),
    }
    record_web_research_source_quality(
        query=query,
        quality_report=report,
        sources=payload["sources"] if isinstance(payload.get("sources"), list) else [],
        turn_id=turn_id,
    )
    return payload


def _web_research_quality_report(
    *,
    message: str,
    query: str,
    page_summaries: list[dict[str, str]],
    findings: list[dict[str, object]],
) -> dict[str, object]:
    issues: list[str] = []
    msg = str(message or "")
    urls = re.findall(r"https?://\S+", msg)
    available_source_count = len([item for item in page_summaries if str(item.get("url") or "").startswith(("http://", "https://"))])
    source_url_lines = [line for line in msg.splitlines() if re.search(r"https?://", line)]
    cited_source_count = max(len(urls), len(source_url_lines))
    source_count = cited_source_count if cited_source_count else available_source_count
    model_source_count = len([item for item in page_summaries if item.get("source") == "model"])
    rule_source_count = len([item for item in page_summaries if item.get("source") == "rules"])
    readable_finding_count = len([item for item in findings if len(_research_item_text(item)) >= 120])
    source_health = _source_health_counts(page_summaries)
    if source_health["degraded"] > 0:
        issues.append("degraded_source_used")
    if source_count == 0:
        issues.append("source_count_zero")
    if len(urls) < source_count:
        issues.append("source_url_missing")
    if len(msg.strip()) < 120:
        issues.append("brief_too_short")
    if _looks_like_web_residue(msg):
        issues.append("brief_contains_web_residue")
    if "..." in msg:
        issues.append("brief_contains_ellipsis")
    if "](" in msg or "```" in msg or "|---" in msg:
        issues.append("brief_contains_markdown_artifact")
    if "暂不发送未证实内容" in msg:
        issues.append("ungrounded_content_blocked")
    bullet_lines = [line.strip() for line in msg.splitlines() if re.match(r"^\d+[.、]\s*", line.strip())]
    incomplete_bullets = [line for line in bullet_lines if line and line[-1] not in "。.!！?？"]
    if incomplete_bullets:
        issues.append("brief_incomplete_sentence")
    source_lines = [line for line in msg.splitlines() if line.strip().startswith(("来源：", "链接："))]
    if bullet_lines and len(source_lines) < len(bullet_lines):
        issues.append("brief_source_line_missing")

    blocking = {
        "source_count_zero",
        "source_url_missing",
        "brief_contains_web_residue",
        "brief_contains_ellipsis",
        "brief_contains_markdown_artifact",
        "ungrounded_content_blocked",
        "brief_incomplete_sentence",
        "brief_source_line_missing",
    }
    score = 1.0
    for issue in issues:
        score -= 0.35 if issue in blocking else 0.16
    score = max(0.0, round(score, 3))
    quality_level = "production" if score >= 0.86 else "usable_with_preview" if score >= 0.68 else "blocked"
    primary_issue = next((issue for issue in issues if issue in blocking), issues[0] if issues else "")
    return {
        "query": query,
        "send_ready": not any(issue in blocking for issue in issues),
        "requires_preview": bool(issues) or rule_source_count > model_source_count,
        "quality_level": quality_level,
        "score": score,
        "issues": issues,
        "primary_issue": primary_issue,
        "source_count": source_count,
        "available_source_count": available_source_count,
        "source_url_count": len(urls),
        "model_source_count": model_source_count,
        "rule_source_count": rule_source_count,
        "readable_finding_count": readable_finding_count,
        "source_health": source_health,
        "message_length": len(msg),
    }


def _web_research_sources(page_summaries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "summary": str(item.get("summary") or "")[:240],
            "source": str(item.get("source") or ""),
        }
        for item in page_summaries
        if str(item.get("url") or "").strip()
    ]


def _source_quality_evidence(sources: object) -> list[dict[str, object]]:
    if not isinstance(sources, list):
        return []
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        reputation = source_reputation_for_url(url)
        rows.append(
            {
                "url": url,
                "domain": domain_from_url(url),
                "title": str(item.get("title") or "")[:180],
                "source": str(item.get("source") or ""),
                "reputation_score": reputation.get("score"),
                "health": reputation.get("health"),
                "success_rate": reputation.get("success_rate"),
                "use_count": reputation.get("use_count"),
                "average_quality_score": reputation.get("average_quality_score"),
                "last_primary_issue": reputation.get("last_primary_issue"),
            }
        )
        if len(rows) >= 8:
            break
    return rows


def _web_research_sources_from_findings(findings: list[dict[str, object]]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in findings or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(
            {
                "title": str(item.get("title") or "")[:240],
                "url": url,
                "summary": _research_item_summary(item, max_chars=180),
                "source": "finding_rejected",
            }
        )
    return sources[:6]


def _web_research_requires_send_confirmation(report: dict[str, object]) -> bool:
    raw = os.getenv("JACHIN_WEB_RESEARCH_REQUIRE_SEND_CONFIRM")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(report.get("send_ready")) and str(report.get("quality_level") or "") != "production"


def _compose_final_human_brief(
    *,
    query: str,
    recipients: list[str],
    page_summaries: list[dict[str, str]],
) -> str:
    model_brief = _model_compose_final_brief(query=query, page_summaries=page_summaries)
    conclusion = str(model_brief.get("conclusion") or "").strip() if model_brief else ""
    if not conclusion:
        conclusion = _fallback_brief_conclusion(query, page_summaries)
    highlights = model_brief.get("highlights") if isinstance(model_brief, dict) else None
    normalized_highlights = _normalize_model_highlights(highlights, page_summaries)

    lines = [
        f"【{query}｜最新信息简报】",
        f"一句话结论：{_ensure_sentence(conclusion)}",
        "重点信息：",
    ]
    for idx, item in enumerate(normalized_highlights, 1):
        lines.append(f"{idx}. {_ensure_sentence(item['summary'])}")
        if item.get("why_matters"):
            lines.append(f"   为什么重要：{_ensure_sentence(item['why_matters'])}")
        lines.append(f"   来源：{item['title']}")
        lines.append(f"   链接：{item['url']}")
    lines.append(f"建议关注：{_fallback_next_step(query, page_summaries)}")
    if recipients:
        lines.append(f"发送对象：{', '.join(recipients)}。")
    lines.append(f"核对依据：已读取并整理 {len(page_summaries)} 个可引用来源，所有要点均保留原始链接。")
    return "\n".join(lines)


def _summarize_research_page(item: dict[str, object], *, query: str) -> dict[str, str]:
    url = str(item.get("url") or "").strip()
    if not url:
        return {}
    title = _source_title(item, query=query)
    text = _research_item_text(item)
    if not text:
        text = f"该来源标题显示其与“{query}”相关。"
    model_summary = _model_summarize_page(query=query, title=title, url=url, text=text)
    if model_summary:
        summary = _ensure_sentence(str(model_summary.get("summary") or ""))
        why = _ensure_sentence(str(model_summary.get("why_matters") or ""))
        if summary and not _looks_like_web_residue(summary):
            return {
                "title": title,
                "url": url,
                "summary": _trim_to_word_boundary(summary, max_chars=170),
                "why_matters": _trim_to_word_boundary(why, max_chars=110) if why else _fallback_why_matters(query, text),
                "source": "model",
            }
    return {
        "title": title,
        "url": url,
        "summary": _research_item_summary(item, max_chars=170) or f"该来源提供了与“{query}”相关的信息。",
        "why_matters": _fallback_why_matters(query, text),
        "source": "rules",
    }


def _summary_has_sources(message: str) -> bool:
    return bool(re.search(r"https?://", str(message or "")))


def _model_summarize_page(*, query: str, title: str, url: str, text: str) -> dict[str, object] | None:
    if not _web_research_model_enabled():
        return None
    text_budget = _safe_int_env("JACHIN_WEB_RESEARCH_PAGE_TEXT_CHARS", 6000)
    prompt = (
        "你是企业办公场景的信息分析助手。请基于单个网页内容，提炼一条适合放进 Lark 简报的中文要点。\n"
        "要求：只输出 JSON，不要 Markdown；summary 必须是完整中文句子；why_matters 说明为什么值得关注；"
        "优先提炼对业务、产品、技术判断有价值的信息，不要流水账，不要编造网页里没有的信息。\n"
        f"检索主题：{query}\n"
        f"网页标题：{title}\n"
        f"网页链接：{url}\n"
        f"网页正文：{text[:text_budget]}\n"
        '输出格式：{"summary":"...","why_matters":"..."}'
    )
    obj = _call_web_research_model(prompt, max_tokens=420, model=_web_research_page_model())
    if not isinstance(obj, dict):
        return None
    summary = str(obj.get("summary") or "").strip()
    if len(summary) < 12 or _looks_like_web_residue(summary):
        return None
    return obj


def _model_compose_final_brief(*, query: str, page_summaries: list[dict[str, str]]) -> dict[str, object] | None:
    if not _web_research_model_enabled():
        return None
    compact_sources = [
        {
            "index": idx + 1,
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "summary": item.get("summary", ""),
            "why_matters": item.get("why_matters", ""),
        }
        for idx, item in enumerate(page_summaries[:4])
    ]
    prompt = (
        "你是企业办公简报编辑。请把多个网页摘要整合成一份能直接发给同事的中文简报素材。\n"
        "要求：只输出 JSON；conclusion 用一句话概括整体趋势；highlights 数量不要超过来源数量；每条 highlight 保留 source_index；"
        "summary 和 why_matters 都必须是完整自然中文句子；不要输出 Markdown；不要删除来源对应关系；"
        "表达要像一个靠谱同事写的简报，抓重点、有人味、有判断，但不能夸张营销。\n"
        f"主题：{query}\n"
        f"来源摘要：{json.dumps(compact_sources, ensure_ascii=False)}\n"
        '输出格式：{"conclusion":"...","highlights":[{"source_index":1,"summary":"...","why_matters":"..."}]}'
    )
    obj = _call_web_research_model(prompt, max_tokens=900, model=_web_research_final_model())
    if not isinstance(obj, dict):
        return None
    if not str(obj.get("conclusion") or "").strip():
        return None
    return obj


def _call_web_research_model(prompt: str, *, max_tokens: int, model: str | None = None) -> dict[str, object] | None:
    api_key = _effective_dashscope_key()
    if not api_key:
        return None
    api_base = (
        os.getenv("JACHIN_WEB_RESEARCH_MODEL_API_BASE")
        or os.getenv("DASHSCOPE_API_BASE")
        or os.getenv("DASHSCOPE_API_BASE_CN")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    active_model = model or _web_research_final_model()
    timeout = float(os.getenv("JACHIN_WEB_RESEARCH_MODEL_TIMEOUT", "25") or 25)
    body = {
        "model": active_model,
        "messages": [
            {"role": "system", "content": "你只输出严格 JSON，不输出解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{api_base}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(120_000).decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        content = str(((parsed.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        return _extract_json_object(content)
    except Exception as exc:
        logger.info("[WebResearchBrief] model unavailable, fallback to rules: %s", exc)
        return None


def _web_research_model_enabled() -> bool:
    raw = os.getenv("JACHIN_WEB_RESEARCH_USE_MODEL")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on", "auto"}
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return bool(_effective_dashscope_key())


def _web_research_page_model() -> str:
    return (
        os.getenv("JACHIN_WEB_RESEARCH_PAGE_MODEL")
        or os.getenv("JACHIN_WEB_RESEARCH_MODEL")
        or "qwen-plus"
    ).strip()


def _web_research_final_model() -> str:
    explicit = (
        os.getenv("JACHIN_WEB_RESEARCH_FINAL_MODEL")
        or os.getenv("JACHIN_WEB_RESEARCH_MODEL")
        or os.getenv("LLM_COMPLEX_MODEL")
    )
    if explicit and explicit.strip():
        return explicit.strip()
    try:
        from core.config import settings

        value = str(getattr(settings, "LLM_COMPLEX_MODEL", "") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return "qwen-max"


def _safe_int_env(key: str, default: int) -> int:
    try:
        return max(800, int(os.getenv(key, str(default)) or default))
    except Exception:
        return default


def _effective_dashscope_key() -> str:
    for key in ("JACHIN_WEB_RESEARCH_MODEL_API_KEY", "DASHSCOPE_API_KEY_CN", "DASHSCOPE_API_KEY", "QWEN_AI_API_KEY", "QWEN_API_KEY"):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    try:
        from core.config import get_effective_qwen_api_key

        return (get_effective_qwen_api_key() or "").strip()
    except Exception:
        return ""


def _extract_json_object(text: str) -> dict[str, object] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    candidates = [raw]
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _normalize_model_highlights(highlights: object, page_summaries: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if isinstance(highlights, list):
        for raw in highlights:
            if not isinstance(raw, dict):
                continue
            try:
                source_index = int(raw.get("source_index") or len(normalized) + 1) - 1
            except Exception:
                source_index = len(normalized)
            if source_index < 0 or source_index >= len(page_summaries):
                source_index = len(normalized) if len(normalized) < len(page_summaries) else 0
            source = page_summaries[source_index]
            summary = _ensure_sentence(str(raw.get("summary") or source.get("summary") or ""))
            why = _ensure_sentence(str(raw.get("why_matters") or source.get("why_matters") or ""))
            if summary and not _looks_like_web_residue(summary):
                normalized.append(
                    {
                        "title": source["title"],
                        "url": source["url"],
                        "summary": _trim_to_word_boundary(summary, max_chars=190),
                        "why_matters": _trim_to_word_boundary(why, max_chars=120) if why else "",
                    }
                )
            if len(normalized) >= 4:
                break
    if normalized:
        return normalized
    return [
        {
            "title": item["title"],
            "url": item["url"],
            "summary": _ensure_sentence(item.get("summary", "")),
            "why_matters": _ensure_sentence(item.get("why_matters", "")),
        }
        for item in page_summaries[:4]
    ]


def _fallback_brief_conclusion(query: str, page_summaries: list[dict[str, str]]) -> str:
    if not page_summaries:
        return f"本次检索围绕“{query}”进行，但没有足够来源形成结论。"
    first = page_summaries[0].get("summary", "")
    return f"本次检索显示，“{query}”的主要动向集中在：{first}"


def _fallback_next_step(query: str, page_summaries: list[dict[str, str]]) -> str:
    if len(page_summaries) >= 2:
        return "建议优先核对前两个来源，再决定是否继续跟进原文细节。"
    return f"建议先打开来源链接核对“{query}”的原文细节。"


def _fallback_why_matters(query: str, text: str) -> str:
    if any(token in text for token in ("发布", "上线", "开源", "更新", "能力", "模型")):
        return "这类变化可能影响后续工具选型、产品方案或技术路线判断。"
    if any(token in text for token in ("融资", "合作", "收购", "商业", "企业")):
        return "这类变化反映了产业侧投入和商业化方向，值得持续观察。"
    return f"该信息可作为理解“{query}”最新变化的参考依据。"


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
    return rank_findings_by_source_quality(page_findings) + rank_findings_by_source_quality(search_findings)


def _select_findings_for_summary(findings: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked = rank_findings_by_source_quality([item for item in findings or [] if isinstance(item, dict)])
    if not ranked:
        return []
    non_degraded = [
        item
        for item in ranked
        if source_reputation_for_url(str(item.get("url") or "")).get("health") != "degraded"
    ]
    return non_degraded if non_degraded else ranked


def _source_health_counts(page_summaries: list[dict[str, str]]) -> dict[str, int]:
    counts = {"reliable": 0, "unproven": 0, "degraded": 0, "unknown": 0}
    for item in page_summaries or []:
        health = str(source_reputation_for_url(str(item.get("url") or "")).get("health") or "unknown")
        if health not in counts:
            health = "unknown"
        counts[health] += 1
    return counts


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
    text = _research_item_text(item)
    return _complete_sentence_excerpt(text, max_chars=max_chars)


def _research_item_text(item: dict[str, object]) -> str:
    text = str(item.get("text") or item.get("content") or "").strip()
    text = _clean_research_text(text)
    return _drop_noisy_prefix(text)


def _source_title(item: dict[str, object], *, query: str) -> str:
    title = _clean_research_text(str(item.get("title") or "").strip())
    if not title:
        title = _clean_research_text(str(item.get("url") or "来源").strip())
    title = _trim_to_word_boundary(title, max_chars=48).rstrip("。.!！?？")
    if not title or _looks_like_web_residue(title):
        return f"{query} 相关来源"
    return title


def _looks_like_web_residue(text: str) -> bool:
    low = str(text or "").lower()
    if any(marker in low for marker in ("%3c", "<defs", "</style", "function(", "undefined", "viewbox", "xmlns")):
        return True
    if re.search(r"<[a-z][^>]{0,80}>", low):
        return True
    if re.search(r"\[[^\]]+\]\(https?://", text or ""):
        return True
    symbolic = len(re.findall(r"[{}<>#=%\\|]", text or ""))
    return symbolic >= 6 and symbolic > len(text or "") * 0.08


def _ensure_sentence(text: str) -> str:
    text = _compact_ws(str(text or "").strip())
    if not text:
        return ""
    text = text.rstrip("，,；;：:")
    if text[-1] not in "。.!！?？":
        text += "。"
    return text


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
