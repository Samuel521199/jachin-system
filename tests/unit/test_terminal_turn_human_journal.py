"""终端 turn 日志「人类可读」分区。"""
from __future__ import annotations

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
    tlog.log_react_iteration_start(1, "t1", context={"max_iterations": 8})
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
        action_input_len=50,
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
    assert "第 1 轮" in text
    assert "模型在想什么" in text
    assert "core:db_query" in text
    assert "工具返回了什么" in text
    assert "本轮会话复盘" in text
    assert "共 1 轮" in text
    assert "张三手头有 2 项任务" in text
    assert "[ReAct 第 1 轮]" in text


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
