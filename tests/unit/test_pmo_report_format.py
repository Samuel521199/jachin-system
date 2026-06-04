"""PMO 战报 📊 表 5 列 SSOT 校验。"""
from __future__ import annotations

import json

from l3_node.agent_core import (
    _PMO_MD_SECTION_DEMAND,
    _pmo_branch_a_notifier_markdown_is_complete,
    _pmo_notifier_markdown_missing_sections,
)
from l3_node.pmo_report_format import (
    pmo_demand_table_column_issues,
    pmo_demand_table_header_line,
)


def test_demand_table_valid_five_columns():
    mc = """**📊 需求进度全览**
| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- |
| Epic A | 05/01→05/25 | Ethan | [▓▓░░] 20% | 🔵 进行中 |
"""
    assert pmo_demand_table_column_issues(mc, _PMO_MD_SECTION_DEMAND) == []
    assert pmo_demand_table_header_line(mc, _PMO_MD_SECTION_DEMAND).count("|") >= 6


def test_demand_table_rejects_risk_and_priority_columns():
    mc = """**📊 需求进度全览**
| 需求名称 | 优先级 | 状态 | 进度条 | 风险说明 |
| --- | --- | --- | --- | --- |
| Epic A | P0 | 🔵 | [▓▓] | 某风险 |
"""
    issues = pmo_demand_table_column_issues(mc, _PMO_MD_SECTION_DEMAND)
    assert any("优先级" in i for i in issues)
    assert any("风险说明" in i for i in issues)


def test_notifier_complete_requires_five_column_header():
    good = json.dumps(
        {
            "markdown_content": """**📊 需求进度全览**
| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- |
| a | b | c | d | e |

**👥 人员任务矩阵**
| 人员 | 任务 | 状态 |
| --- | --- | --- |
| p | t | s |

**📦 版本发布需求映射**
| v | n | r |
| --- | --- | --- |
| x | y | z |
"""
        },
        ensure_ascii=False,
    )
    assert _pmo_branch_a_notifier_markdown_is_complete(good)

    bad = json.dumps(
        {
            "markdown_content": """**📊 需求进度全览**
| 需求名称 | 优先级 | 状态 | 进度条 | 风险说明 |
| --- | --- | --- | --- | --- |
| a | P0 | s | [▓] | risk |

**👥 人员任务矩阵**
| 人员 | 任务 | 状态 |
| --- | --- | --- |
| p | t | s |

**📦 版本发布需求映射**
| v | n | r |
| --- | --- | --- |
| x | y | z |
"""
        },
        ensure_ascii=False,
    )
    assert not _pmo_branch_a_notifier_markdown_is_complete(bad)
    missing = _pmo_notifier_markdown_missing_sections(bad)
    assert any("需求名称|时间跨度" in m for m in missing)
