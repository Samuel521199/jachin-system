from __future__ import annotations


def _claim_by_type(fusion: dict, claim_type: str) -> dict:
    return next(
        claim
        for claim in fusion["claims"]
        if claim["claim_type"] == claim_type
    )


def test_claim_fusion_accepts_file_change_supported_by_git_evidence():
    from l3_node.work_ledger_codex_claims import build_codex_claim_fusion

    fusion = build_codex_claim_fusion(
        "1. 修改 l3_node/work_ledger_codex.py，接入逐条声明融合。",
        [
            {
                "evidence_id": "git-1",
                "source": "git_snapshot",
                "summary": "Git 工作区包含声明融合改动",
                "trust_level": "system_observed",
                "payload": {
                    "changed_files": [
                        {
                            "path": "l3_node/work_ledger_codex.py",
                            "status": "modified",
                        }
                    ]
                },
            }
        ],
        invocation_id="inv-file",
    )

    claim = _claim_by_type(fusion, "file_change")
    assert claim["disposition"] == "accepted_fact"
    assert claim["supporting_evidence"][0]["source"] == "git_snapshot"
    assert claim["can_support_completion"] is False


def test_completion_claim_needs_verified_or_user_confirmed_evidence():
    from l3_node.work_ledger_codex_claims import build_codex_claim_fusion

    fusion = build_codex_claim_fusion(
        "已完成工作账本的声明融合能力。",
        [
            {
                "evidence_id": "git-2",
                "source": "git_snapshot",
                "summary": "工作账本声明融合文件存在未提交修改",
                "trust_level": "system_observed",
                "payload": {"changed_files": ["work_ledger_codex_claims.py"]},
            }
        ],
        invocation_id="inv-completion",
    )

    claim = _claim_by_type(fusion, "completion")
    assert claim["disposition"] == "supported_interpretation"
    assert claim["can_support_completion"] is False
    assert "completion_requires_user_or_verified_evidence" in claim["unknown_reasons"]


def test_user_confirmed_evidence_can_promote_completion_claim():
    from l3_node.work_ledger_codex_claims import build_codex_claim_fusion

    fusion = build_codex_claim_fusion(
        "已完成工作账本的声明融合能力。",
        [
            {
                "evidence_id": "note-1",
                "source": "manual_note",
                "summary": "用户确认已完成工作账本声明融合能力",
                "trust_level": "user_confirmed",
                "payload": {"note": "工作账本声明融合已经完成"},
            }
        ],
        invocation_id="inv-confirmed",
    )

    claim = _claim_by_type(fusion, "completion")
    assert claim["disposition"] == "accepted_fact"
    assert claim["can_support_completion"] is True


def test_counter_evidence_rejects_conflicting_codex_claim():
    from l3_node.work_ledger_codex_claims import build_codex_claim_fusion

    fusion = build_codex_claim_fusion(
        "测试通过：工作账本声明融合质量门已经验证通过。",
        [
            {
                "evidence_id": "test-1",
                "source": "test_result",
                "summary": "工作账本声明融合质量门测试失败",
                "trust_level": "user_confirmed",
                "payload": {"status": "failed"},
            }
        ],
        invocation_id="inv-conflict",
    )

    claim = _claim_by_type(fusion, "verification")
    assert claim["disposition"] == "rejected_conflict"
    assert claim["counter_evidence"][0]["source"] == "test_result"
    assert fusion["conflicts"][0]["claim_id"] == claim["claim_id"]


def test_unknown_claim_enters_confirmation_queue_and_recommendation_stays_scoped():
    from l3_node.work_ledger_codex_claims import build_codex_claim_fusion

    fusion = build_codex_claim_fusion(
        "\n".join(
            [
                "系统已经自动修复所有历史工作记录。",
                "下一步建议运行真实桌面烟测。",
            ]
        ),
        [],
        invocation_id="inv-unknown",
    )

    dispositions = {
        claim["claim_type"]: claim["disposition"]
        for claim in fusion["claims"]
    }
    assert dispositions["completion"] == "unknown_requires_confirmation"
    assert dispositions["recommendation"] == "recommendation"
    assert len(fusion["confirmation_queue"]) == 1


def test_output_quality_gate_blocks_unknown_and_conflicting_codex_claims():
    from l3_node.work_ledger_llm import (
        validate_instant_brief_output,
        validate_refined_outputs,
        validate_weekly_report_outputs,
    )

    blocked_text = "系统已经自动修复所有历史工作记录并完成全部验证。"
    fusion = {
        "claims": [
            {
                "claim_id": "claim-blocked-1",
                "text": blocked_text,
                "disposition": "unknown_requires_confirmation",
            }
        ]
    }
    evidence = [
        {
            "source": "codex_work_plan_consultation",
            "payload": {
                "answer": "Codex 的其他解释性内容。",
                "claim_fusion": fusion,
            },
        }
    ]
    daily = validate_refined_outputs(
        {
            "daily_report": (
                "## 完成与推进\n"
                f"1. {blocked_text}\n"
                "## 风险与未完成\n"
                "1. 仍需真实验证。\n"
                "## 下一步\n"
                "1. 运行烟测。"
            ),
            "continuation_prompt": "1. 读取本机证据后继续。",
            "lark_brief": "1. 已整理工作证据。\n2. 下一步运行真实烟测。",
        },
        evidence,
    )
    assert any(
        issue == "work_outputs_use_disallowed_codex_claim:claim-blocked-1"
        for issue in daily["issues"]
    )

    weekly = validate_weekly_report_outputs(
        {
            "weekly_report": (
                "## 本周进展\n"
                f"1. {blocked_text}\n"
                "## 风险与下一步\n"
                "1. 仍需真实验证。"
            )
        },
        {
            "sessions": [],
            "recent_codex_consultations": [
                {"answer": "其他解释。", "claim_fusion": fusion}
            ],
        },
    )
    assert any(
        issue == "weekly_report_uses_disallowed_codex_claim:claim-blocked-1"
        for issue in weekly["issues"]
    )

    instant = validate_instant_brief_output(
        {
            "brief": (
                "## 完成与推进\n"
                f"1. {blocked_text}\n"
                "## 涉及项目与模块\n"
                "1. 工作账本。\n"
                "## 风险与未完成\n"
                "1. 仍需真实验证。\n"
                "## 下一步计划\n"
                "1. 运行烟测。"
            )
        },
        {
            "recent_codex_consultations": [
                {"answer": "其他解释。", "claim_fusion": fusion}
            ],
            "session_evidence_digests": [],
        },
    )
    assert any(
        issue == "brief_uses_disallowed_codex_claim:claim-blocked-1"
        for issue in instant["issues"]
    )


def test_evidence_digest_preserves_claim_fusion_for_final_composer():
    from l3_node.work_ledger_llm import build_evidence_digest

    fusion = {
        "schema_version": 1,
        "claims": [
            {
                "claim_id": "claim-accepted-1",
                "text": "修改了声明融合模块。",
                "disposition": "accepted_fact",
            }
        ],
    }
    digest = build_evidence_digest(
        {"session_id": "work-claim", "title": "声明融合"},
        [
            {
                "source": "codex_work_plan_consultation",
                "trust_level": "system_observed",
                "payload": {
                    "answer": "修改了声明融合模块。",
                    "claim_fusion": fusion,
                },
            }
        ],
    )

    assert digest["codex_work_plan_consultations"][0]["claim_fusion"] == fusion
