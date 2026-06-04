"""PMO 大需求 workflow_status 推断。"""
from __future__ import annotations

from l3_node.pmo_workflow_stage import (
    infer_epic_workflow_completion_pct,
    infer_epic_workflow_status,
    infer_task_workflow_step,
    is_forbidden_simple_demand_status,
    is_workflow_placeholder_child,
    rank_to_workflow_completion_pct,
)


def test_infer_task_dev_in_progress():
    rank, phase, step = infer_task_workflow_step(
        {
            "department": "开发",
            "progress": "开发中",
            "status": "—",
            "task": "FB外跳-程序开发",
        }
    )
    assert phase == "开发/验收"
    assert step == "技术开发"
    assert rank >= 45


def test_infer_task_submit_test_env():
    rank, phase, step = infer_task_workflow_step(
        {
            "department": "开发",
            "progress": "提交测试环境",
            "status": "🔵 按时完成",
            "task": "游戏加载-优化",
        }
    )
    assert step == "环境部署"
    assert rank >= 60


def test_epic_fb_jump_not_simple_pending():
    children = [
        {
            "department": "开发",
            "progress": "",
            "status": "",
            "task": "FB外跳-程序开发",
            "parent_epic": "FB外跳",
        }
    ]
    epic = {"epic_name": "FB外跳", "progress": "", "status": ""}
    label = infer_epic_workflow_status(epic, children, completion_pct=0)
    assert "待开始" not in label
    assert "进行中" not in label
    assert "·" in label
    assert "开发/验收" in label or "立项/评审" in label


def test_epic_all_done_goes_release_phase():
    children = [
        {
            "department": "开发",
            "progress": "已完成",
            "status": "🔵 按时完成",
            "task": "游戏加载-优化",
            "actual_delivery_date_iso": "2026-06-02",
        }
    ]
    epic = {"epic_name": "游戏加载优化"}
    label = infer_epic_workflow_status(epic, children, completion_pct=100)
    assert "已完成" not in label or "·" in label
    assert "上线发布" in label or "闭环" in label or "复盘" in label


def test_completion_pct_from_workflow_rank_not_task_count():
    """1/3 子任务终态 ≠ 33%；应按泳道 rank。"""
    children = [
        {
            "department": "开发",
            "progress": "已完成",
            "status": "🔵 按时完成",
            "task": "游戏BUG-A",
            "actual_delivery_date_iso": "2026-06-02",
        },
        {"department": "开发", "progress": "开发中", "status": "—", "task": "游戏BUG-B"},
        {"department": "开发", "progress": "开发中", "status": "—", "task": "游戏BUG-C"},
    ]
    epic = {"epic_name": "游戏BUG"}
    by_count = round(100 * 1 / 3)
    by_flow = infer_epic_workflow_completion_pct(epic, children)
    assert by_flow != by_count
    assert by_flow >= rank_to_workflow_completion_pct(45)


def test_all_terminal_children_completion_is_100():
    children = [
        {
            "department": "开发",
            "progress": "已完成",
            "status": "🔵 按时完成",
            "actual_delivery_date_iso": "2026-06-02",
        }
    ]
    assert infer_epic_workflow_completion_pct({"epic_name": "游戏加载优化"}, children) == 100


def test_epic_completion_aligns_with_status_rank():
    children = [
        {"department": "开发", "progress": "开发中", "task": "FB外跳-程序开发"},
    ]
    epic = {"epic_name": "FB外跳"}
    pct = infer_epic_workflow_completion_pct(epic, children)
    assert pct == rank_to_workflow_completion_pct(50)


def test_forbidden_simple_status():
    assert is_forbidden_simple_demand_status("🟡 待开始")
    assert is_forbidden_simple_demand_status("进行中")
    assert not is_forbidden_simple_demand_status("🔵 开发/验收 · 技术开发")


def test_placeholder_child_detected():
    assert is_workflow_placeholder_child(
        {"task": "前端开发", "department": "产品", "progress": "", "status": None}
    )


def test_laro_go_epic_not_stuck_at_requirement_review():
    """案例：空「前端开发」占位 + 已闭环子任务 → 不得显示立项/评审·需求评审。"""
    children = [
        {
            "department": "产品",
            "task": "前端开发",
            "progress": "",
            "status": None,
            "parent_epic": "Laro GO 游戏加载优化",
        },
        {
            "department": "产品",
            "task": "Laro GO 游戏加载优化-进度条平滑逻辑",
            "progress": "开发中",
            "status": "🔵 按时完成",
            "actual_delivery_date_iso": "2026-06-03",
            "parent_epic": "Laro GO 游戏加载优化",
        },
        {
            "department": "产品",
            "task": "Laro GO 游戏加载优化-Reload 按钮与后台设置链接",
            "progress": "开发中",
            "status": "🔵 按时完成",
            "actual_delivery_date_iso": "2026-06-03",
            "parent_epic": "Laro GO 游戏加载优化",
        },
    ]
    epic = {"epic_name": "Laro GO 游戏加载优化", "sprint": "2026/06/01-Sprint"}
    pct = infer_epic_workflow_completion_pct(epic, children)
    label = infer_epic_workflow_status(epic, children, completion_pct=pct)
    assert "需求评审" not in label
    assert "立项/评审" not in label or "环境部署" in label or "开发/验收" in label
    assert pct >= 60
