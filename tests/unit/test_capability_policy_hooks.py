from pathlib import Path


def test_workspace_writeback_policy_detects_intent_and_observations():
    from l3_node.capability_policies.workspace_writeback import (
        observation_suggests_workspace_read_ok,
        observation_suggests_workspace_write_ok,
        user_intent_requests_workspace_writeback,
    )

    assert user_intent_requests_workspace_writeback([
        {"role": "user", "content": "请读取 D:/tmp/report.md，总结后覆盖源文件"},
    ])
    assert not user_intent_requests_workspace_writeback([
        {"role": "user", "content": "请总结一下这个文件，不要修改"},
    ])
    assert observation_suggests_workspace_read_ok("core:fs_read", "hello")
    assert not observation_suggests_workspace_read_ok("core:fs_read", "ENOENT not found")
    assert observation_suggests_workspace_write_ok("core:fs_write", "fs_write", '{"ok":true}')
    assert not observation_suggests_workspace_write_ok("core:fs_write", "fs_write", '{"ok":false}')


def test_sqlite_grounding_policy_detects_fake_query_claims():
    from l3_node.capability_policies.sqlite_grounding import (
        final_answer_claims_sqlite_was_queried,
        final_answer_is_honest_sqlite_capability_denial,
        user_text_requests_workspace_sqlite_verification,
    )

    assert user_text_requests_workspace_sqlite_verification("查一下工作区 test_db.sqlite 里哪些水果缺货")
    assert final_answer_claims_sqlite_was_queried("根据 `test_db.sqlite` 数据库的查询结果，苹果缺货。")
    assert final_answer_is_honest_sqlite_capability_denial(
        "当前可见工具列表未包含 SQLite read_query，我无法真实查询数据库。"
    )


def test_hr_recruitment_policy_claim_detection():
    from l3_node.capability_policies.hr_recruitment import (
        answer_claims_job_published,
        answer_claims_unmanned_scheduler_running,
        build_job_published_without_tool_prompt,
    )

    assert answer_claims_job_published("职位已在 Boss 上架，JOB_123")
    assert answer_claims_unmanned_scheduler_running("无人值守调度已启动，正在运行中。")
    assert "mcp:atom_post_job_boss" in build_job_published_without_tool_prompt()


def test_agent_core_no_long_inline_policy_bodies():
    root = Path(__file__).resolve().parents[2]
    text = (root / "l3_node" / "agent_core.py").read_text(encoding="utf-8")

    assert "def _final_answer_claims_sqlite_was_queried" not in text
    assert "def _hr_answer_claims_job_published" not in text
    assert "你的 WorkOrder 未通过逻辑审查" not in text
    assert "当前问题依赖数据库中的**可核验事实**" not in text
    assert "用户要求将总结/提炼后的内容**写回源文件**" not in text
