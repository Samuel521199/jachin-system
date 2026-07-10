"""终端 turn 日志「人类可读」分区。"""
from __future__ import annotations

import contextvars
import os
import re
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def debug_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_TERMINAL_DEBUG_DIR", str(tmp_path))
    monkeypatch.setenv("JACHIN_TERMINAL_DEBUG_LOG", "1")
    monkeypatch.delenv("JACHIN_TERMINAL_DEBUG_OVERWRITE", raising=False)
    return tmp_path


def test_human_journal_round_and_recap(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn(
        "谁手头有什么任务？",
        extra={"run_id": "run-abc", "channel": "lark_im_dispatcher", "max_iterations": 8},
    )
    tlog.log_human_run_config(
        {
            "execution_tier": "complex",
            "max_iterations": 8,
            "channel": "lark_im_dispatcher",
            "run_id": "run-abc",
        }
    )
    tlog.log_role_execution_iteration_start(1, "t1", context={"max_iterations": 8})
    tlog.log_parsed_action_detail(
        1,
        {"type": "native", "tool": "core:db_query", "input": '{"sql":"SELECT 1"}'},
        "tool=core:db_query",
        thought_excerpt="需要先查人员任务表。",
        trace="t1",
    )
    tlog.log_tool_call_full(1, "core:db_query", '{"sql":"SELECT name FROM pmo_tasks LIMIT 5"}')
    tlog.log_tool_dispatch_summary(
        1,
        "t1",
        tool="core:db_query",
        mcp=False,
        elapsed_ms=42.5,
        output_len=1200,
        work_order_input_len=50,
        used_foreground_timeout=False,
        sync_timeout_sec=None,
    )
    tlog.log_observation_full(
        1,
        "core:db_query",
        '{"status":"ok","rows":[{"name":"张三"}]}',
        sent_to_llm_len=80,
    )
    tlog.log_parsed_action_detail(
        1,
        {"type": "answer", "content": "张三手头有 2 项任务。"},
        "answer",
        thought_excerpt="数据够了，直接回答。",
        trace="t1b",
    )
    tlog.finalize_top_level_turn(
        "张三手头有 2 项任务。",
        run_id="run-abc",
        channel="lark_im_dispatcher",
    )

    logs = list(debug_log_dir.glob("terminal_turn_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")

    assert "【人类可读】" in text
    assert "谁手头有什么任务" in text
    assert "Cognitive Kernel 主循环" in text
    assert "采用 RoleExecutionAgent 多轮推理" not in text
    assert "RoleExecutionAgent" in text
    assert "第 1 轮" in text
    assert "模型在想什么" in text
    assert "core:db_query" in text
    assert "工具返回了什么" in text
    assert "本轮会话复盘" in text
    assert "共 1 轮" in text
    assert "张三手头有 2 项任务" in text
    assert "[RoleExecutionAgent 第 1 轮]" in text


def test_turn_log_header_contains_user_message_anchor(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn("帮我打开微信", extra={"run_id": "header-user"})

    log = next(debug_log_dir.glob("terminal_turn_*.log"))
    raw = log.read_bytes()
    text = raw.decode("utf-8-sig")

    assert raw.startswith(b"\xef\xbb\xbf")
    assert "【本轮用户原话 / turn_user_text】" in text[:600]
    assert "帮我打开微信" in text[:800]
    assert "turn_user_text_json" in text[:800]


def test_late_user_message_is_logged_when_header_started_empty(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn("", extra={"run_id": "late-user"})
    tlog.ensure_turn_started("后面才拿到的用户问题", extra={"run_id": "late-user"})

    text = next(debug_log_dir.glob("terminal_turn_*.log")).read_text(encoding="utf-8-sig")
    assert "begin_turn 时尚未记录用户原话" in text
    assert "【本轮用户原话补记】" in text
    assert "后面才拿到的用户问题" in text


def test_contextless_internal_write_uses_active_turn_log(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn("读取 README", extra={"run_id": "active-turn"})

    worker_ctx = contextvars.Context()
    worker_ctx.run(tlog.append_section, "[worker] 内部执行详情", "role agent detail")

    logs = list(debug_log_dir.glob("terminal_turn_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8-sig")
    assert "[worker] 内部执行详情" in text
    assert "role agent detail" in text
    assert "orphan" not in logs[0].name


def test_ensure_turn_started_reuses_registered_run_log_across_context(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn("打开计算器", extra={"run_id": "same-run", "channel": "run_agent"})

    nested_ctx = contextvars.Context()
    nested_ctx.run(
        tlog.ensure_turn_started,
        "打开计算器",
        extra={"run_id": "same-run", "channel": "run_agent"},
    )
    nested_ctx.run(tlog.append_section, "[nested] 子流程", "same request")

    logs = list(debug_log_dir.glob("terminal_turn_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8-sig")
    assert "[nested] 子流程" in text
    assert "same request" in text
    assert text.count("terminal turn debug | log_path=") == 1


def test_lark_im_session_routing_in_journal(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    inbound = "oc_inbound_test_chat_001"
    tlog.begin_turn(
        "今天战报怎么样？",
        extra={
            "run_id": "run-lark",
            "channel": "lark_im_dispatcher",
            "lark_chat_id": inbound,
            "lark_reply_chat_id": inbound,
            "max_iterations": 6,
        },
    )
    tlog.log_lark_im_reply_dispatch(
        inbound_chat_id=inbound,
        reply_chat_id=inbound,
        ok=True,
        reply_preview="战报已推送。",
        run_id="run-lark",
    )
    tlog.finalize_top_level_turn(
        "战报已推送。",
        run_id="run-lark",
        channel="lark_im_dispatcher",
        extra={"lark_chat_id": inbound, "lark_reply_chat_id": inbound},
    )

    text = next(debug_log_dir.glob("terminal_turn_*.log")).read_text(encoding="utf-8")
    assert "【飞书会话路由】" in text
    assert "来源会话 chat_id" in text
    assert "回复目标 chat_id" in text
    assert inbound in text
    assert "[飞书 IM] 回推发送 (成功)" in text
    assert "【飞书回推执行】" in text


def test_append_final_idempotent(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn("你好", extra={"run_id": "r1"})
    tlog.append_final("final_answer", "你好，我是 Jachin。")
    tlog.append_final("final_answer", "你好，我是 Jachin。")

    text = next(debug_log_dir.glob("terminal_turn_*.log")).read_text(encoding="utf-8")
    assert len(re.findall(r"本轮会话复盘", text)) == 1


def test_cognitive_kernel_mainline_nodes_are_logged(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    class FakeObj:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return self._data

    ctx = FakeObj(
        {
            "envelope": {},
        }
    )
    ctx.envelope = FakeObj(
        {
            "turn_id": "ck-log",
            "source": "text",
            "channel": "run_agent",
            "raw_text": "open file README.md",
            "normalized_text": "open file README.md",
        }
    )
    ctx.state_snapshot = FakeObj(
        {
            "snapshot_id": "state-1",
            "active_window": {"title": "Codex"},
        }
    )
    ctx.memory_bundle = FakeObj(
        {
            "candidate_intents": ["open_file"],
            "candidate_task_domains": ["file_operation"],
            "confidence": 0.82,
        }
    )
    plan = FakeObj({})
    plan.review_summary = FakeObj(
        {
            "review_session_id": "review-1",
            "top_intent": "open_file",
            "task_type": "file_operation",
            "target": {"path": "README.md"},
            "confidence": 0.86,
        }
    )
    plan.decision_contract = FakeObj(
        {
            "decision_id": "decision-1",
            "selected_workflow": "windows_file_open",
            "execution_allowed": True,
            "risk_level": "low",
        }
    )
    plan.work_orders = [
        FakeObj(
            {
                "work_order_id": "wo-1",
                "role_agent": "FileExecutorAgent",
                "task": "open_file",
                "inputs": {"tool": "mcp:windows_file_open"},
            }
        )
    ]
    plan.closure = None
    plan.task_dag = None

    dispatch_result = FakeObj({})
    dispatch_result.observation = '{"ok": true}'
    dispatch_result.verification = FakeObj(
        {
            "verification_id": "verify-1",
            "work_order_id": "wo-1",
            "ok": True,
            "confidence": 0.82,
            "failure_reason": "",
        }
    )
    dispatch_result.recovery_plan = None
    closure = FakeObj(
        {
            "turn_id": "ck-log",
            "closure_type": "completed",
            "verification_status": "passed",
        }
    )

    tlog.begin_turn("open file README.md", extra={"run_id": "ck-log"})
    tlog.log_cognitive_mainline_context(ctx)
    tlog.log_cognitive_mainline_plan(plan)
    tlog.log_main_agent_effective_prompt(
        stage="cognitive_kernel_planning_context",
        system_prompt="",
        gateway_inject="gateway rules",
        cognitive_kernel_prompt_block="kernel context block",
        tools_count=1,
        messages_count=2,
        sent_to_llm=False,
        note="direct mainline",
    )
    tlog.log_role_agent_work_order_prompt(
        contract=plan.decision_contract,
        work_order=plan.work_orders[0],
        tool_id="mcp:windows_file_open",
        allowed_skills=["mcp:windows_file_open"],
        available_tools=[{"id": "mcp:windows_file_open"}],
        stage="direct_mainline_dispatch",
    )
    tlog.log_role_agent_execution_detail(
        phase="started",
        role_id="FileExecutorAgent",
        adapter_kind="file",
        work_order=plan.work_orders[0],
        context={"tool": "mcp:windows_file_open"},
        evidence={"expected_evidence": ["file_path"]},
    )
    tlog.log_cognitive_direct_execution(
        stage="executed",
        contract=plan.decision_contract,
        work_order=plan.work_orders[0],
        tool_id="mcp:windows_file_open",
        dispatch_result=dispatch_result,
        closure=closure,
        final_text="已完成文件操作：README.md。",
    )
    tlog.finalize_top_level_turn("已完成文件操作：README.md。", run_id="ck-log")

    text = next(debug_log_dir.glob("terminal_turn_*.log")).read_text(encoding="utf-8")
    assert "[Cognitive Kernel 主循环] 1. 输入/状态/记忆上下文" in text
    assert "[Cognitive Kernel 主循环] 2. 会审/裁决/工单计划" in text
    assert "[Cognitive Kernel 主循环] 3. 执行/验证/闭环 (executed)" in text
    assert "AgentInputEnvelope" in text
    assert "DecisionContract" in text
    assert "WorkOrder" in text
    assert "VerificationReport" in text
    assert "TurnClosure" in text
    assert "[Main Agent Prompt] 主 Agent 有效提示词/上下文" in text
    assert "[Role Agent Prompt] 主 Agent 下发给子 Agent 的任务/边界/工具范围" in text
    assert "[Role Agent Execution] FileExecutorAgent started" in text
    assert "memory_recall_breakdown" in text
    assert "role_reviews" in text


def test_direct_mainline_intro_and_recap_are_not_role_execution_misleading(debug_log_dir):
    from l3_node import terminal_turn_debug_log as tlog

    tlog.begin_turn("帮我打开微信", extra={"run_id": "ck-direct", "channel": "websocket_terminal"})
    tlog.log_cognitive_direct_execution(
        stage="executed",
        tool_id="mcp:windows_open_app",
        final_text="已打开 WeChat。",
    )
    tlog.finalize_top_level_turn("已打开 WeChat。", run_id="ck-direct")

    text = next(debug_log_dir.glob("terminal_turn_*.log")).read_text(encoding="utf-8")
    assert "先进入 Cognitive Kernel 主循环" in text
    assert "采用 RoleExecutionAgent 多轮推理" not in text
    assert "共 0 轮 RoleExecutionAgent 步骤；本轮由 Cognitive Kernel direct mainline 直接完成。" in text
