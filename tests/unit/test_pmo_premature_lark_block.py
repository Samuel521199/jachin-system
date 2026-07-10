"""PMO：禁止分支 A 在试错阶段推送半成品 Lark 卡片。"""
from __future__ import annotations

import json
from unittest.mock import patch

from l3_node.pmo_agent_policy import _pmo_branch_a_blocked_premature_lark_observation
from l3_node.engine.hooks_pipeline import PipelineContext


def test_blocks_when_three_tables_missing() -> None:
    ctx = PipelineContext("", metadata={"_implicit_channel": "pmo_copilot_cli"})
    ut = "请按分支 A 拉表并推送宏观看板"
    inp = json.dumps(
        {"title": "t", "markdown_content": "**🎯**\n👥 x\n⚠️ y\n", "chat_id": "oc_x"},
        ensure_ascii=False,
    )
    with patch("l3_node.agent_core._pmo_branch_a_requires_bi_pull", lambda c: True):
        obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    assert obs is not None
    d = json.loads(obs)
    assert d.get("error") == "pmo_premature_notifier_blocked"


def test_blocks_false_sync_claim_when_bi_ok() -> None:
    ctx = PipelineContext(
        "",
        metadata={
            "_implicit_channel": "pmo_copilot_cli",
            "_pmo_bi_project_context_ok": True,
            "_gw_inject_stored": "",
        },
    )
    mc = """**📊 需求进度全览**
| a | b | c | d | e |
| --- | --- | --- | --- | --- |
| x | y | z | 🟢 [▓░░] 10% | s |

---

**👥 人员任务矩阵**
| A | B | C |
| --- | --- | --- |
| p | 【P0】 q | ✅ |

---

**📦 版本发布需求映射**
| A | B | C |
| --- | --- | --- |
| x | y | z |
"""
    inp = json.dumps(
        {"title": "数据不完整报告", "markdown_content": mc + "- 开发计划核心版本需求表未成功同步\n"},
        ensure_ascii=False,
    )
    with patch("l3_node.agent_core._pmo_branch_a_requires_bi_pull", lambda c: True):
        obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    assert obs is not None
    d = json.loads(obs)
    assert d.get("error") == "pmo_false_sync_claim_blocked"


def test_allows_when_bi_ok_and_no_false_sync_claim() -> None:
    ctx = PipelineContext(
        "",
        metadata={
            "_implicit_channel": "pmo_copilot_cli",
            "_pmo_bi_project_context_ok": True,
        },
    )
    mc = """**📊 需求进度全览**
| a | b | c | d | e |
| --- | --- | --- | --- | --- |
| x | y | z | 🟢 [▓░░] 10% | s |

---

**👥 人员任务矩阵**
| A | B | C |
| --- | --- | --- |
| p | 【P0】 q | ✅ |

---

**📦 版本发布需求映射**
| A | B | C |
| --- | --- | --- |
| x | y | z |
"""
    inp = json.dumps({"title": "【K11 · PMO】", "markdown_content": mc}, ensure_ascii=False)
    with patch("l3_node.agent_core._pmo_branch_a_requires_bi_pull", lambda c: True):
        obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    assert obs is None
