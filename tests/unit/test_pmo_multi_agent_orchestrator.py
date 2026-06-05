"""PMO 多 Agent Worker B/C 任务体与 FanOut 编排测试。"""
from __future__ import annotations

from l3_node.pmo_multi_agent_queries import (
    WORKER_B_AGENT_MAX_ITERATIONS,
    WORKER_B_AGENT_TASK,
    WORKER_B_TASK,
    WORKER_C_MAX_ITERATIONS,
    WORKER_C_TASK,
    build_worker_b_agent_task,
)
from l3_node.pmo_multi_agent_queries import (
    WORKER_D_AGENT_MAX_ITERATIONS,
    WORKER_D_TASK,
)
from l3_node.pmo_multi_agent_orchestrator import (
    PMO_WORKER_B_ROLE,
    PMO_WORKER_C_ROLE,
    PMO_WORKER_D_ROLE,
    PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS,
    _phase1_fanout_items,
)

_LEGACY_EPIC_AFFIRM = "Epic：json_extract(fields,'$.\"父记录\"[0].text') IS NULL"
_LEGACY_PERSON_EACH = "Person 字段（开发/人员看板）必须用 json_each 展开"
_LEGACY_DEV_STATUS_NESTED = (
    "开发表「状态」：json_extract(json_extract(fields,'$.\"状态\"'),'$[0].text')"
)


def test_worker_b_task_covers_personnel_and_sup():
    assert "vewCz1FFJi" in WORKER_B_TASK
    assert "vewpI8lyYw" in WORKER_B_TASK
    assert "personnel_tasks" in WORKER_B_TASK
    assert "B-S1" in WORKER_B_TASK and "B-4" in WORKER_B_TASK and "B-SUP" in WORKER_B_TASK


def test_worker_b_agent_task_host_first_b_tool():
    assert "宿主预取" in WORKER_B_AGENT_TASK
    assert "B-TOOL" in WORKER_B_AGENT_TASK
    assert "current_sprint" in WORKER_B_AGENT_TASK
    assert "禁止" in WORKER_B_AGENT_TASK and "B-S1" in WORKER_B_AGENT_TASK
    task = build_worker_b_agent_task(
        {"sprint_names_for_in": ["2026/06/01-Sprint", "2026/06/08-Sprint"]}
    )
    assert "2026/06/01-Sprint" in task
    assert "UNION ALL" not in task.split("B-SUP")[0]  # ReAct 段不含 B-4 UNION


def test_worker_c_covers_epics():
    assert "C-1" in WORKER_C_TASK and "C-3" in WORKER_C_TASK
    assert "current_sprint" in WORKER_C_TASK
    assert "epic_children" in WORKER_C_TASK
    assert WORKER_C_MAX_ITERATIONS >= 12


def test_phase1_fanout_worker_b_uses_host_seed():
    seed = {
        "current_sprint": "2026/06/01-Sprint",
        "personnel_tasks": [{"person": "Buck", "task": "x"}],
        "recent_sprints": [{"sprint": "2026/06/01-Sprint"}],
        "requirement_context": [{"requirement": "y"}],
        "sprint_names_for_in": ["2026/06/01-Sprint"],
    }
    items = _phase1_fanout_items(seed)
    assert len(items) == 4
    b = items[1]
    assert b["max_iterations"] == WORKER_B_AGENT_MAX_ITERATIONS
    assert "B-TOOL" in b["task"] or "B-SUP" in b["task"]
    assert b["context_data"]["personnel_tasks"][0]["person"] == "Buck"
    assert b["context_data"]["current_sprint"] == "2026/06/01-Sprint"
    assert "pmo_personnel_report" in b["context_data"]["说明"]


def test_worker_c_system_prompt_no_legacy_sql_rules():
    prefix = PMO_WORKER_C_ROLE["system_prefix"]
    assert "父记录双形态" in prefix
    assert "禁止" in prefix and "父记录[0].text IS NULL" in prefix
    assert _LEGACY_EPIC_AFFIRM not in prefix
    assert _LEGACY_PERSON_EACH not in prefix
    assert _LEGACY_DEV_STATUS_NESTED not in prefix
    assert "Step4·" not in prefix
    assert "禁止**单 Agent 旧称 Step3/Step4/Step5" in prefix


def test_worker_b_system_prompt_no_legacy_epic_or_person_rule():
    prefix = PMO_WORKER_B_ROLE["system_prefix"]
    assert "B-TOOL" in prefix or "pmo_personnel_report" in prefix
    assert "current_sprint" in prefix
    assert "宿主" in prefix or "预取" in prefix
    assert _LEGACY_EPIC_AFFIRM not in prefix
    assert _LEGACY_PERSON_EACH not in prefix
    assert _LEGACY_DEV_STATUS_NESTED not in prefix
    assert "core:pmo_personnel_report" in str(PMO_WORKER_B_ROLE.get("allowed_tools"))


def test_worker_d_covers_release_mapping():
    assert "D-TOOL" in WORKER_D_TASK
    assert "pmo_release_epic_mapping" in WORKER_D_TASK
    assert "markdown_section" in WORKER_D_TASK
    assert WORKER_D_AGENT_MAX_ITERATIONS <= 4


def test_phase1_fanout_worker_d_uses_host_seed():
    seed = {
        "completed_epics": [{"epic_name": "Laro GO", "priority": "P0"}],
        "completed_count": 1,
        "markdown_section": "### **📦 版本发布需求映射**",
        "completed_sql_ids": ["D-TOOL"],
    }
    items = _phase1_fanout_items(host_d_seed=seed)
    assert len(items) == 4
    d = items[3]
    assert d["max_iterations"] == WORKER_D_AGENT_MAX_ITERATIONS
    assert "D-TOOL" in d["task"]
    assert d["context_data"]["completed_count"] == 1
    assert "pmo_release_epic_mapping" in d["context_data"]["说明"]


def test_worker_d_system_prompt_has_d_tool():
    prefix = PMO_WORKER_D_ROLE["system_prefix"]
    assert "D-TOOL" in prefix or "pmo_release_epic_mapping" in prefix
    assert "core:pmo_release_epic_mapping" in str(PMO_WORKER_D_ROLE.get("allowed_tools"))


def test_pmo_worker_roles_raise_system_prefix_limit():
    assert PMO_WORKER_C_ROLE.get("system_prefix_max_chars") == PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS
    assert len(PMO_WORKER_C_ROLE["system_prefix"]) <= PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS
    assert PMO_WORKER_D_ROLE.get("system_prefix_max_chars") == PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS
