import asyncio

from l3_node.primitives.agent_tasks.agent_cancel import (
    register_cancel_event,
    unregister_cancel_event,
)
from l3_node.voice_task_handle_registry import (
    cancel_resolved_voice_targets,
    register_voice_task_handle,
    resolve_voice_task_handles,
    unregister_voice_task_handle,
)


def test_resolves_frontend_task_alias_to_run_id():
    run_id = "run-real-001"
    try:
        register_voice_task_handle(
            run_id,
            channel="desktop_voice_companion",
            session_id="session-a",
            aliases=["ui-task-001"],
            title="发送 Lark 简报",
        )

        resolution = resolve_voice_task_handles(
            target_task_id="ui-task-001",
            voice_context={"voice_active_task_context": {"active_tasks": [{"id": "ui-task-001"}]}},
            session_id="session-a",
            channel="desktop_voice_companion",
        )

        assert resolution.selected == run_id
        assert run_id in resolution.candidates
        assert any(step["via"] == "alias" for step in resolution.evidence["resolution_steps"])
    finally:
        unregister_voice_task_handle(run_id)


def test_cancel_resolved_voice_target_uses_real_run_id():
    run_id = "run-real-002"
    ev = asyncio.Event()
    try:
        register_cancel_event(run_id, ev)
        register_voice_task_handle(run_id, aliases=["ui-task-002"])
        resolution = resolve_voice_task_handles(target_task_id="ui-task-002")

        result = cancel_resolved_voice_targets(resolution)

        assert result["ok"] is True
        assert ev.is_set()
        assert result["attempts"][0]["run_id"] == run_id
    finally:
        unregister_cancel_event(run_id)
        unregister_voice_task_handle(run_id)


def test_resolve_excludes_current_run_and_falls_back_to_previous():
    previous_run_id = "run-real-previous"
    current_run_id = "run-real-current"
    try:
        register_voice_task_handle(
            previous_run_id,
            channel="websocket_terminal",
            session_id="session-stop",
            aliases=["previous-ui-task"],
            title="open calculator",
        )
        register_voice_task_handle(
            current_run_id,
            channel="websocket_terminal",
            session_id="session-stop",
            aliases=["current-ui-task"],
            title="停止",
        )

        resolution = resolve_voice_task_handles(
            session_id="session-stop",
            channel="websocket_terminal",
            exclude_run_ids=[current_run_id],
        )

        assert resolution.selected == previous_run_id
        assert current_run_id not in resolution.candidates
        assert any(step["via"] == "latest_registered_run" for step in resolution.evidence["resolution_steps"])
    finally:
        unregister_voice_task_handle(previous_run_id)
        unregister_voice_task_handle(current_run_id)
