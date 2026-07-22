import asyncio


def test_run_agent_stop_control_cancels_previous_registered_task(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.agent_core as agent_core
    from l3_node.primitives.agent_tasks.agent_cancel import (
        register_cancel_event,
        unregister_cancel_event,
    )
    from l3_node.voice_task_handle_registry import (
        register_voice_task_handle,
        unregister_voice_task_handle,
    )

    previous_run_id = "previous-stop-target"
    ev = asyncio.Event()
    try:
        register_cancel_event(previous_run_id, ev)
        register_voice_task_handle(
            previous_run_id,
            channel="websocket_terminal",
            session_id="chat-stop-session",
            aliases=["ui-stop-target"],
            title="open calculator",
        )

        reply = asyncio.run(
            agent_core.run_agent(
                "停止",
                object(),
                max_iterations=1,
                implicit_attribution={"session_id": "chat-stop-session"},
                implicit_signals={"desktop_companion": True},
            )
        )

        assert ev.is_set()
        assert "已停止当前正在执行的任务" in reply
    finally:
        unregister_cancel_event(previous_run_id)
        unregister_voice_task_handle(previous_run_id)


def test_run_agent_stop_control_without_active_task_is_not_chitchat(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.agent_core as agent_core

    reply = asyncio.run(
        agent_core.run_agent(
            "停止",
            object(),
            max_iterations=1,
            implicit_attribution={"session_id": "chat-stop-empty"},
            implicit_signals={"desktop_companion": True},
        )
    )

    assert "当前没有找到正在执行的任务" in reply
    assert "聊天" not in reply
