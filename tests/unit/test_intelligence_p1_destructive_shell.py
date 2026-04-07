"""destructive_shell_requires_task_plan 与 docker prune 启发式。"""
from __future__ import annotations

import pytest


def test_assert_shell_docker_prune_blocked_when_gate_on(monkeypatch) -> None:
    monkeypatch.setattr(
        "l3_node.intelligence_p1.get_intel_p1_config",
        lambda: {"destructive_shell_requires_task_plan": True},
    )
    monkeypatch.setattr(
        "l3_node.task_planning.task_plan_is_substantial",
        lambda *a, **k: False,
    )
    from l3_node.intelligence_p1 import assert_shell_exec_allowed

    with pytest.raises(ValueError, match="task_plan"):
        assert_shell_exec_allowed("docker system prune -f")


def test_assert_shell_docker_prune_allowed_with_substantial_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        "l3_node.intelligence_p1.get_intel_p1_config",
        lambda: {"destructive_shell_requires_task_plan": True},
    )
    monkeypatch.setattr(
        "l3_node.task_planning.task_plan_is_substantial",
        lambda *a, **k: True,
    )
    from l3_node.intelligence_p1 import assert_shell_exec_allowed

    assert_shell_exec_allowed("docker image prune -f")


def test_assert_shell_git_prune_not_task_plan_gated(monkeypatch) -> None:
    monkeypatch.setattr(
        "l3_node.intelligence_p1.get_intel_p1_config",
        lambda: {"destructive_shell_requires_task_plan": True},
    )
    monkeypatch.setattr(
        "l3_node.task_planning.task_plan_is_substantial",
        lambda *a, **k: False,
    )
    from l3_node.intelligence_p1 import assert_shell_exec_allowed

    assert_shell_exec_allowed("git prune")
