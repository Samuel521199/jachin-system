"""
Hook 回放执行器（路线图 §3.2.4 · P2）

在 `hook_events.sqlite3`（`JACHIN_PERSIST_HOOKS=1`）基础上：
  1. 按时间正序重建单次 run 的 Hook 时间线；
  2. 推断续跑建议（DAG 待办、策略链、失败子任务、ExecutionBrief）；
  3. 可选应用 `apply_dag_resume` 并产出可直接交给 `run_agent` 的续跑意图。

环境变量
--------
JACHIN_HOOK_REPLAY_ENABLE=1              开启回放 API / 逻辑（默认关，仅诊断可读 Q）
JACHIN_HOOK_REPLAY_AUTO_ON_BRIEF=1     run 以 ExecutionBrief 结束时自动 probe+apply DAG（默认关）
JACHIN_HOOK_REPLAY_AUTO_RUN=1          回放后自动再调一次 run_agent（默认关）
JACHIN_HOOK_REPLAY_AUTO_RUN_MAX_DEPTH=1  续跑链最大深度（默认 1，防 Brief 死循环）
JACHIN_HOOK_REPLAY_EVENT_LIMIT=300     单次回放最多读取事件数（默认 300）
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

ReplayWorkOrder = Literal[
    "none",
    "dag_resume",
    "retry_with_strategy",
    "review_brief",
    "inspect_timeline",
]


def replay_enabled() -> bool:
    return (os.environ.get("JACHIN_HOOK_REPLAY_ENABLE") or "").strip().lower() in (
        "1", "true", "yes",
    )


def replay_auto_on_brief_enabled() -> bool:
    return (os.environ.get("JACHIN_HOOK_REPLAY_AUTO_ON_BRIEF") or "").strip().lower() in (
        "1", "true", "yes",
    )


def replay_auto_run_enabled() -> bool:
    return (os.environ.get("JACHIN_HOOK_REPLAY_AUTO_RUN") or "").strip().lower() in (
        "1", "true", "yes",
    )


def _auto_run_max_depth() -> int:
    try:
        return max(0, min(3, int(os.environ.get("JACHIN_HOOK_REPLAY_AUTO_RUN_MAX_DEPTH") or "1")))
    except ValueError:
        return 1


def is_hook_replay_followup_attribution(implicit_attribution: dict[str, Any] | None) -> bool:
    if not implicit_attribution or not isinstance(implicit_attribution, dict):
        return False
    return bool(implicit_attribution.get("hook_replay_followup"))


def _followup_depth(implicit_attribution: dict[str, Any] | None) -> int:
    if not implicit_attribution or not isinstance(implicit_attribution, dict):
        return 0
    try:
        return int(implicit_attribution.get("hook_replay_followup_depth") or 0)
    except (TypeError, ValueError):
        return 0


def _event_limit() -> int:
    try:
        return max(20, min(1000, int(os.environ.get("JACHIN_HOOK_REPLAY_EVENT_LIMIT") or "300")))
    except ValueError:
        return 300


def _summarize_hook_event(ev: dict[str, Any]) -> str:
    hook = str(ev.get("hook") or "")
    meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
    if hook == "on_intent_received":
        prev = (ev.get("intent_preview") or "")[:120]
        return f"意图接收：{prev}"
    if hook == "on_task_decompose":
        n = meta.get("_task_decompose_sub_count")
        roles = meta.get("_task_decompose_roles_preview")
        return f"任务拆解 sub_tasks={n} roles={roles}"
    if hook == "on_task_node_start":
        return (
            f"子任务开始 #{meta.get('delegate_sub_task_index')} "
            f"role={meta.get('delegate_sub_task_role')}"
        )
    if hook == "on_task_node_done":
        err = str(meta.get("task_node_error") or "").strip()
        if err:
            return f"子任务结束（异常）：{err[:160]}"
        prev = str(meta.get("task_node_result_preview") or "")[:80]
        return f"子任务完成 preview={prev}"
    if hook == "before_tool_exec":
        return f"工具将执行：{meta.get('executed_tool') or meta.get('path') or '?'}"
    if hook == "after_tool_exec":
        return f"工具已执行：{meta.get('executed_tool') or meta.get('path') or '?'}"
    if hook == "on_retry":
        return f"重试：{meta.get('_retry_reason') or 'unknown'}"
    if hook == "on_strategy_shift":
        return (
            f"策略切换 → {meta.get('_resilience_strategy') or '?'} "
            f"（{meta.get('_resilience_strategy_hint', '')[:80]}）"
        )
    if hook == "on_execution_brief":
        return f"ExecutionBrief：{meta.get('_execution_brief_reason') or 'unknown'}"
    if hook == "on_memory_commit":
        return "回合记忆写入"
    if hook == "on_experience_learned":
        return "Experience 沉淀"
    return hook or "event"


@dataclass
class HookReplayTimelineItem:
    seq: int
    event_id: int
    ts: float
    hook: str
    summary: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookReplayResult:
    ok: bool
    run_id: str
    event_count: int
    timeline: list[HookReplayTimelineItem]
    recommended_action: ReplayWorkOrder
    resume_intent: str
    strategies_tried: list[str]
    retry_reasons: list[str]
    failed_sub_tasks: list[dict[str, Any]]
    tools_executed: list[str]
    dag_probe: dict[str, Any] = field(default_factory=dict)
    dag_applied: bool = False
    followup_scheduled: bool = False
    followup_run_id: str = ""
    message: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timeline"] = [asdict(t) for t in self.timeline]
        return d


def _load_events(run_id: str) -> list[dict[str, Any]]:
    from l3_node.engine.persistent_hook_log import (
        hooks_db_available,
        read_hook_events_chronological,
    )

    if not hooks_db_available():
        return []
    return read_hook_events_chronological(run_id, limit=_event_limit())


def _analyze_timeline(events: list[dict[str, Any]]) -> HookReplayResult:
    run_id = (events[0].get("run_id") if events else "") or ""
    timeline: list[HookReplayTimelineItem] = []
    strategies: list[str] = []
    retries: list[str] = []
    failed_sub: list[dict[str, Any]] = []
    tools: list[str] = []
    had_brief = False
    brief_reason = ""

    for i, ev in enumerate(events):
        meta = ev.get("meta") if isinstance(ev.get("meta"), dict) else {}
        summary = _summarize_hook_event(ev)
        timeline.append(
            HookReplayTimelineItem(
                seq=i + 1,
                event_id=int(ev.get("id") or 0),
                ts=float(ev.get("ts") or 0),
                hook=str(ev.get("hook") or ""),
                summary=summary,
                meta=meta,
            )
        )
        if ev.get("hook") == "on_strategy_shift":
            st = str(meta.get("_resilience_strategy") or "").strip()
            if st and st not in strategies:
                strategies.append(st)
        if ev.get("hook") == "on_retry":
            rr = str(meta.get("_retry_reason") or "").strip()
            if rr and rr not in retries:
                retries.append(rr)
        if ev.get("hook") == "on_task_node_done" and meta.get("task_node_error"):
            failed_sub.append({
                "index": meta.get("delegate_sub_task_index"),
                "role": meta.get("delegate_sub_task_role"),
                "error": str(meta.get("task_node_error"))[:300],
            })
        if ev.get("hook") in ("before_tool_exec", "after_tool_exec"):
            tid = str(meta.get("executed_tool") or meta.get("path") or "").strip()
            if tid and tid not in tools:
                tools.append(tid)
        if ev.get("hook") == "on_execution_brief":
            had_brief = True
            brief_reason = str(meta.get("_execution_brief_reason") or "")[:200]

    action: ReplayWorkOrder = "inspect_timeline"
    resume_parts: list[str] = []

    dag_probe: dict[str, Any] = {}
    try:
        from l3_node.task_engine.dag_resume import probe_dag_resume

        pr = probe_dag_resume(run_id)
        dag_probe = pr.to_dict()
        if pr.ok and pr.pending_nodes:
            action = "dag_resume"
            resume_parts.append(pr.resume_intent)
    except Exception as e:
        dag_probe = {"ok": False, "error": str(e)}

    if had_brief and action != "dag_resume":
        action = "review_brief"
        resume_parts.append(
            f"【Hook 回放·ExecutionBrief】上次 run 以有界退出结束（{brief_reason or 'unknown'}）。"
            "请根据下列策略与失败子任务调整方案后继续，禁止同参死循环。"
        )

    if strategies or retries:
        if action == "inspect_timeline":
            action = "retry_with_strategy"
        if strategies:
            resume_parts.append(
                "【Hook 回放·策略链】已尝试策略："
                + " → ".join(strategies)
                + "。请采用下一档策略或产出 ExecutionBrief。"
            )
        if retries:
            resume_parts.append(
                "【Hook 回放·重试原因】" + "；".join(retries[:6])
            )

    if failed_sub:
        lines = [
            f"- #{f.get('index')} role={f.get('role')}: {f.get('error')}"
            for f in failed_sub[:8]
        ]
        resume_parts.append("【Hook 回放·失败子任务】\n" + "\n".join(lines))

    if not resume_parts and timeline:
        first_intent = (events[0].get("intent_preview") or "").strip()
        if first_intent:
            resume_parts.append(
                f"【Hook 回放】续跑此前任务（原意图摘要）：{first_intent[:400]}"
            )

    resume_intent = "\n\n".join(p for p in resume_parts if p).strip()

    return HookReplayResult(
        ok=bool(events),
        run_id=run_id,
        event_count=len(events),
        timeline=timeline,
        recommended_action=action if events else "none",
        resume_intent=resume_intent,
        strategies_tried=strategies,
        retry_reasons=retries,
        failed_sub_tasks=failed_sub,
        tools_executed=tools,
        dag_probe=dag_probe,
        message=(
            f"回放 {len(events)} 条 Hook 事件，建议动作={action}"
            if events
            else "无 Hook 事件（需 JACHIN_PERSIST_HOOKS=1 且 run 已落盘）"
        ),
    )


def probe_hook_replay(run_id: str) -> HookReplayResult:
    """只读回放：重建时间线 + 续跑建议，不修改 active.json。"""
    rid = (run_id or "").strip()
    if not rid:
        return HookReplayResult(
            ok=False,
            run_id="",
            event_count=0,
            timeline=[],
            recommended_action="none",
            resume_intent="",
            strategies_tried=[],
            retry_reasons=[],
            failed_sub_tasks=[],
            tools_executed=[],
            error="run_id required",
        )
    events = _load_events(rid)
    result = _analyze_timeline(events)
    result.run_id = rid
    if not events:
        result.ok = False
        result.error = "no_hook_events"
    return result


def apply_hook_replay(
    run_id: str,
    *,
    apply_dag_resume: bool = True,
) -> HookReplayResult:
    """
    回放 + 可选应用 DAG 续跑（重置 pending 节点）。
    自动 run_agent 见 schedule_hook_replay_followup_run / JACHIN_HOOK_REPLAY_AUTO_RUN。
    """
    result = probe_hook_replay(run_id)
    if not result.ok:
        return result
    if not apply_dag_resume or result.recommended_action != "dag_resume":
        return result
    try:
        from l3_node.task_engine.dag_resume import apply_dag_resume

        applied = apply_dag_resume(run_id)
        result.dag_applied = bool(applied.ok and applied.pending_nodes)
        if applied.resume_intent:
            result.resume_intent = applied.resume_intent
        result.dag_probe = applied.to_dict()
        if result.dag_applied:
            result.message += "；已 apply_dag_resume"
    except Exception as e:
        result.error = str(e)
        logger.warning("[HookReplay] apply_dag_resume failed: %s", e)
    return result


@dataclass
class HookReplayFollowupContext:
    """回放后续跑 run_agent 所需上下文（须在同进程 asyncio 循环内调度）。"""

    parent_run_id: str
    user_input: str
    final_answer: str
    session_messages: list[dict[str, Any]] | None = None
    implicit_attribution: dict[str, Any] | None = None
    on_chunk: Callable[[str], Awaitable[None]] | None = None


def schedule_hook_replay_followup_run(
    result: HookReplayResult,
    *,
    engine: Any,
    followup: HookReplayFollowupContext,
) -> bool:
    """
    使用回放合成的 resume_intent 再调一次 run_agent（新 run_id）。
    返回是否已 create_task。
    """
    if not replay_auto_run_enabled():
        return False
    intent = (result.resume_intent or "").strip()
    if not intent:
        return False
    if is_hook_replay_followup_attribution(followup.implicit_attribution):
        return False
    depth = _followup_depth(followup.implicit_attribution)
    if depth >= _auto_run_max_depth():
        logger.info(
            "[HookReplay] skip auto_run: followup depth %d >= max %d",
            depth,
            _auto_run_max_depth(),
        )
        return False
    if engine is None:
        return False

    async def _run_followup() -> None:
        from l3_node.agent_core import run_agent

        msgs: list[dict[str, Any]] = list(followup.session_messages or [])
        prior_ans = (followup.final_answer or "").strip()
        if prior_ans:
            msgs.append({"role": "assistant", "content": prior_ans[:4000]})
        msgs.append({"role": "user", "content": intent[:8000]})

        new_att: dict[str, Any] = dict(followup.implicit_attribution or {})
        new_att["hook_replay_followup"] = True
        new_att["hook_replay_parent_run_id"] = (followup.parent_run_id or "")[:64]
        new_att["hook_replay_followup_depth"] = depth + 1
        new_att.setdefault("channel", new_att.get("channel") or "hook_replay_followup")

        logger.info(
            "[HookReplay] auto_run start parent=%s depth=%d intent_len=%d",
            (followup.parent_run_id or "")[:12],
            depth + 1,
            len(intent),
        )
        try:
            answer = await run_agent(
                intent,
                engine,
                _session_messages=msgs,
                implicit_attribution=new_att,
                _delegate_depth=0,
                on_chunk=followup.on_chunk,
            )
        except Exception as e:
            logger.warning("[HookReplay] auto_run failed parent=%s: %s", followup.parent_run_id[:12], e)
            return

        cid = str(new_att.get("lark_chat_id") or new_att.get("chat_id") or "").strip()
        ch = str(new_att.get("channel") or "")
        if cid and ch in ("lark_im_dispatcher", "websocket", "hook_replay_followup"):
            try:
                from l3_node.ws_server import _push_reply_to_lark

                asyncio.create_task(_push_reply_to_lark(cid, str(answer or "").strip()))
            except Exception as e:
                logger.debug("[HookReplay] lark push skipped: %s", e)
        if cid and followup.session_messages is not None:
            try:
                from l3_node.lark_session import save_lark_session

                save_lark_session(cid, msgs)
            except Exception:
                pass
        logger.info(
            "[HookReplay] auto_run done parent=%s answer_len=%d",
            (followup.parent_run_id or "")[:12],
            len(str(answer or "")),
        )

    try:
        asyncio.get_running_loop().create_task(
            _run_followup(),
            name=f"hook_replay_followup_{(followup.parent_run_id or '')[:8]}",
        )
        result.followup_scheduled = True
        return True
    except RuntimeError:
        logger.debug("[HookReplay] no running loop for auto_run")
        return False


def try_hook_replay_after_execution_brief(
    run_id: str,
    *,
    final_answer: str = "",
    engine: Any = None,
    user_input: str = "",
    session_messages: list[dict[str, Any]] | None = None,
    implicit_attribution: dict[str, Any] | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    apply_dag: bool = True,
    schedule_auto_run: bool | None = None,
) -> HookReplayResult | None:
    """
    run 以 ExecutionBrief 结束时：回放 → 可选 apply DAG → 可选自动再跑 run_agent。
    schedule_auto_run 默认跟随 JACHIN_HOOK_REPLAY_AUTO_RUN；续跑链上 run 自动跳过。
    """
    if is_hook_replay_followup_attribution(implicit_attribution):
        return None
    if not replay_auto_on_brief_enabled() and not replay_auto_run_enabled():
        return None
    if "[ExecutionBrief]" not in (final_answer or ""):
        return None
    rid = (run_id or "").strip()
    if not rid:
        return None
    try:
        if replay_auto_on_brief_enabled() and apply_dag:
            result = apply_hook_replay(rid, apply_dag_resume=True)
        else:
            result = probe_hook_replay(rid)

        _do_run = schedule_auto_run if schedule_auto_run is not None else replay_auto_run_enabled()
        if _do_run and engine is not None:
            followup = HookReplayFollowupContext(
                parent_run_id=rid,
                user_input=user_input or "",
                final_answer=final_answer or "",
                session_messages=session_messages,
                implicit_attribution=implicit_attribution,
                on_chunk=on_chunk,
            )
            schedule_hook_replay_followup_run(result, engine, followup=followup)

        logger.info(
            "[HookReplay] auto_on_brief run_id=%s action=%s dag_applied=%s events=%d followup=%s",
            rid[:12],
            result.recommended_action,
            result.dag_applied,
            result.event_count,
            result.followup_scheduled,
        )
        return result
    except Exception as e:
        logger.debug("[HookReplay] auto_on_brief failed: %s", e)
        return None


async def run_hook_replay_with_optional_followup(
    run_id: str,
    *,
    mode: Literal["probe", "apply"] = "probe",
    apply_dag_resume: bool = True,
    engine: Any = None,
    followup: HookReplayFollowupContext | None = None,
    schedule_auto_run: bool = False,
) -> HookReplayResult:
    """HTTP / 编排统一入口：probe 或 apply，并可调度续跑。"""
    if mode == "apply":
        result = apply_hook_replay(run_id, apply_dag_resume=apply_dag_resume)
    else:
        result = probe_hook_replay(run_id)
    if schedule_auto_run and followup and engine is not None:
        schedule_hook_replay_followup_run(result, engine, followup=followup)
    return result
