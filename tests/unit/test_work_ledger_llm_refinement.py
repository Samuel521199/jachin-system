from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def test_work_ledger_llm_refinement_writes_enhanced_outputs(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(project, "init")
    (project / "feature.py").write_text("# TODO: add test\nprint('x')\n", encoding="utf-8")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "1")

    from l3_node import work_ledger_llm

    def fake_refine(**kwargs):
        return {
            "ok": True,
            "model": "fake-qwen",
            "elapsed_ms": 12,
            "outputs": {
                "daily_report": "完成与推进\n1. 完成 Work Ledger 证据采集整理。\n涉及模块\n1. 修改 feature.py。\n风险与未完成\n1. 质量门控仍需真实链路验证。\n下一步\n1. 补充真实链路测试。",
                "continuation_prompt": "请继续本地项目任务，先读取 feature.py 和 Git 状态。",
                "lark_brief": "【Work Ledger｜工作汇报】\n完成与推进\n1. 已增强证据采集与日报整理。\n风险与未完成\n1. 质量门控仍需真实链路验证。\n下一步\n1. 补充质量门控真实链路测试。",
            },
            "quality": {"ok": True, "issues": [], "warnings": []},
        }

    monkeypatch.setattr(work_ledger_llm, "llm_refinement_enabled", lambda: True)
    monkeypatch.setattr(work_ledger_llm, "refine_work_outputs_with_llm", fake_refine)

    from l3_node.work_ledger import generate_work_outputs, start_session

    detail = start_session(title="LLM 输出增强", project_path=str(project), created_from="pytest")
    outputs = generate_work_outputs(detail["session"]["session_id"])

    enhanced_report = Path(outputs["enhanced_daily_report"])
    enhanced_prompt = Path(outputs["enhanced_continuation_prompt"])
    lark_brief = Path(outputs["lark_brief"])
    quality = Path(outputs["llm_quality_report"])
    assert enhanced_report.is_file()
    assert enhanced_prompt.is_file()
    assert lark_brief.is_file()
    assert quality.is_file()
    assert "feature.py" in enhanced_report.read_text(encoding="utf-8")
    assert "Git 状态" in enhanced_prompt.read_text(encoding="utf-8")
    assert "Work Ledger" in lark_brief.read_text(encoding="utf-8")


def test_work_ledger_llm_quality_gate_rejects_unknown_paths():
    from l3_node.work_ledger_llm import validate_refined_outputs

    evidence = [
        {
            "source": "git_snapshot",
            "payload": {"changed_files": [{"path": "feature.py"}]},
        }
    ]
    result = validate_refined_outputs(
        {
            "daily_report": "今天修改了 feature.py 和 imaginary.ts。",
            "continuation_prompt": "继续看 feature.py。",
            "lark_brief": "已完成 feature.py 相关整理。",
        },
        evidence,
    )
    assert not result["ok"]
    assert any("unknown_file_paths" in issue for issue in result["issues"])


def test_work_ledger_weekly_llm_editor_writes_enhanced_report(tmp_path, monkeypatch):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Weekly\n", encoding="utf-8")
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "1")

    from l3_node import work_ledger_llm

    def fake_weekly(**kwargs):
        return {
            "ok": True,
            "model": "fake-qwen",
            "elapsed_ms": 8,
            "outputs": {
                "weekly_report": "本周围绕 Work Ledger 推进工作，依据来自 Work Ledger session 和用户确认输出。风险：仍需补真实验收。下一步：接入更自然的聊天入口。",
            },
            "quality": {"ok": True, "issues": [], "warnings": []},
        }

    monkeypatch.setattr(work_ledger_llm, "llm_refinement_enabled", lambda: True)
    monkeypatch.setattr(work_ledger_llm, "refine_weekly_report_with_llm", fake_weekly)

    from l3_node.work_ledger import add_manual_note, generate_multi_day_weekly_report, start_session

    detail = start_session(title="Weekly LLM", project_path=str(project), user_goal="Generate enhanced weekly report.")
    add_manual_note(str(detail["session"]["session_id"]), "User confirmed: weekly output should be readable.")

    result = generate_multi_day_weekly_report(7)

    enhanced_path = Path(result["enhanced_path"])
    quality_path = Path(result["quality_report_path"])
    assert enhanced_path.is_file()
    assert quality_path.is_file()
    assert "Work Ledger" in enhanced_path.read_text(encoding="utf-8")


def test_instant_brief_llm_editor_replaces_baseline_with_concrete_report(
    tmp_path,
    monkeypatch,
):
    ledger_home = tmp_path / "work_ledger"
    kernel_home = tmp_path / "kernel"
    project = tmp_path / "project"
    project.mkdir()
    (project / "feature.py").write_text(
        "def generate_brief():\n    return 'evidence first'\n",
        encoding="utf-8",
    )
    _git(project, "init")

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(kernel_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "1")

    from l3_node import work_ledger_llm

    concrete = (
        "# 今日工作简报\n\n"
        "## 一、完成与推进\n\n"
        "1. 为即时简报接入代码证据整理，使报告能够说明具体改动而不是复述文件状态。\n\n"
        "## 二、涉及项目与模块\n\n"
        "1. feature.py：补充基于证据生成简报的实现入口。\n\n"
        "## 三、风险与未完成\n\n"
        "1. 当前只验证了生成入口，尚未记录真实用户采纳结果。\n\n"
        "## 四、下一步计划\n\n"
        "1. 补充质量门禁回归测试，验证空话和未知文件会被拒绝。\n\n"
        "## 依据边界\n\n"
        "1. 结论来自 Work Ledger 保存的 Git diff 和文件片段。"
    )

    def fake_instant(**kwargs):
        return {
            "ok": True,
            "model": "fake-qwen-max",
            "elapsed_ms": 16,
            "text": concrete,
            "outputs": {"brief": concrete},
            "quality": {"ok": True, "issues": [], "warnings": []},
        }

    monkeypatch.setattr(work_ledger_llm, "llm_refinement_enabled", lambda: True)
    monkeypatch.setattr(
        work_ledger_llm,
        "refine_instant_work_brief_with_llm",
        fake_instant,
    )

    from l3_node.work_ledger import generate_instant_work_brief, start_session

    start_session(
        title="即时简报质量升级",
        project_path=str(project),
        user_goal="让工作简报可以直接给人阅读。",
        auto_collect=True,
    )
    result = generate_instant_work_brief(1)

    assert result["generation_mode"] == "llm_evidence_editor"
    assert result["model"] == "fake-qwen-max"
    assert result["text"] == concrete
    assert Path(result["path"]).read_text(encoding="utf-8").strip() == concrete
    assert Path(result["baseline_path"]).is_file()
    assert Path(result["quality_report_path"]).is_file()


def test_instant_brief_quality_gate_rejects_status_only_report():
    from l3_node.work_ledger_llm import validate_instant_brief_output

    result = validate_instant_brief_output(
        {
            "brief": (
                "# 今日工作简报\n"
                "## 一、完成与推进\n1. 正在推进：2026-07-23 工作记录\n"
                "## 二、涉及项目与模块\n1. l3_node/work_ledger.py（M）\n"
                "## 三、风险与未完成\n1. 仍在进行：2026-07-23 工作记录\n"
                "## 四、下一步计划\n1. 继续推进。\n"
                "## 依据边界\n1. 来自证据。"
            )
        },
        {
            "recent_changed_files": [{"path": "l3_node/work_ledger.py"}],
            "session_evidence_digests": [],
        },
    )

    assert not result["ok"]
    assert "brief_uses_session_status_as_accomplishment" in result["issues"]
    assert "brief_exposes_raw_git_status_as_result" in result["issues"]


def test_instant_brief_accepts_structured_capability_sections(monkeypatch):
    from l3_node import work_ledger_llm

    response = {
        "brief": {
            "完成与推进": [
                "新增今日工作台导航，使用户可以从控制台进入工作账本。"
            ],
            "涉及项目与模块": [
                "桌面控制台与工作账本交互。"
            ],
            "风险与未完成": ["尚未记录真实用户验收结果。"],
            "下一步计划": ["执行控制台入口的真实点击验收。"],
            "依据边界": "结论来自 Git diff，未记录事项不作完成判断。",
        }
    }
    monkeypatch.setattr(
        work_ledger_llm,
        "_call_dashscope",
        lambda *args, **kwargs: __import__("json").dumps(
            response,
            ensure_ascii=False,
        ),
    )

    result = work_ledger_llm.refine_instant_work_brief_with_llm(
        index={
            "window_days": 1,
            "recent_changed_files": [
                {"path": "clients/desktop/src/console/Sidebar.tsx"}
            ],
            "session_evidence_digests": [
                {
                    "git": {
                        "diff_patch": (
                            "diff --git a/clients/desktop/src/console/Sidebar.tsx "
                            "b/clients/desktop/src/console/Sidebar.tsx\n"
                            "+今日工作台"
                        )
                    }
                }
            ],
        },
        baseline_brief="# 今日工作简报",
    )

    assert result["ok"]
    assert "## 一、完成与推进" in result["text"]
    assert "桌面控制台与工作账本交互" in result["text"]
    assert "Sidebar.tsx" not in result["text"]


def test_instant_brief_normalizes_model_escaped_newlines(monkeypatch):
    from l3_node import work_ledger_llm

    escaped = (
        "# 今日工作简报 \\\\n"
        "## 一、完成与推进 \\\\n1. 新增简报证据编辑器。 \\\\n"
        "## 二、涉及项目与模块 \\\\n1. 工作账本与简报生成。 \\\\n"
        "## 三、风险与未完成 \\\\n1. 尚未记录用户采纳结果。 \\\\n"
        "## 四、下一步计划 \\\\n1. 执行真实简报验收。 \\\\n"
        "## 依据边界 \\\\n1. 结论来自 Git diff 证据。"
    )
    monkeypatch.setattr(
        work_ledger_llm,
        "_call_dashscope",
        lambda *args, **kwargs: __import__("json").dumps(
            {"brief": escaped},
            ensure_ascii=False,
        ),
    )

    result = work_ledger_llm.refine_instant_work_brief_with_llm(
        index={
            "window_days": 1,
            "recent_changed_files": [{"path": "work_ledger.py"}],
            "session_evidence_digests": [
                {"git": {"diff_patch": "+新增简报证据编辑器"}}
            ],
        },
        baseline_brief="# 今日工作简报",
    )

    assert result["ok"]
    assert "\\n" not in result["text"]
    assert result["text"].count("1. ") >= 5


def test_instant_brief_requires_trace_when_verified_codex_claims_are_available():
    from l3_node.work_ledger_llm import validate_instant_brief_output

    index = {
        "recent_codex_consultations": [
            {
                "ok": True,
                "answer": "已完成工作账本成果提炼。",
                "prompt_hash": "prompt-1",
                "claim_fusion": {
                    "claims": [
                        {
                            "claim_id": "claim-fact",
                            "text": "已完成工作账本成果提炼。",
                            "disposition": "accepted_fact",
                        }
                    ]
                },
            }
        ],
        "session_evidence_digests": [],
    }
    brief = (
        "# 工作简报\n"
        "## 一、完成与推进\n1. 完成工作账本成果提炼，使报告按业务能力呈现。\n"
        "## 二、涉及项目与模块\n1. 工作账本与复盘能力。\n"
        "## 三、风险与未完成\n1. 尚需真实用户验收。\n"
        "## 四、下一步计划\n1. 执行真实简报验收。\n"
        "## 依据边界\n1. 结论来自本地证据与经核验的 Codex 解释。"
    )

    missing_trace = validate_instant_brief_output({"brief": brief}, index)
    assert not missing_trace["ok"]
    assert "codex_fusion_not_consumed" in missing_trace["issues"]

    traced = validate_instant_brief_output(
        {
            "brief": brief,
            "fusion_trace": {
                "used_claim_ids": ["claim-fact"],
                "used_interpretation_ids": [],
                "used_recommendation_ids": [],
                "ignored_claim_ids": [],
            },
        },
        index,
    )
    assert traced["ok"]


def test_codex_brief_execution_state_reports_verified_fusion():
    from l3_node.work_ledger import _build_codex_brief_execution_state

    state = _build_codex_brief_execution_state(
        requested=True,
        wait_budget_seconds=300,
        consultation={"results": []},
        fusion={
            "successful_reply_count": 1,
            "usable_claim_count": 3,
        },
        fusion_trace={
            "used_claim_ids": ["fact-1"],
            "used_interpretation_ids": ["interpretation-1"],
            "used_recommendation_ids": [],
        },
        generation_mode="llm_evidence_editor",
    )

    assert state["status"] == "fused"
    assert not state["degraded"]
    assert state["used_claim_count"] == 2
    assert state["wait_budget_seconds"] == 300


def test_codex_brief_execution_state_degrades_after_permission_deadline():
    from l3_node.work_ledger import _build_codex_brief_execution_state

    state = _build_codex_brief_execution_state(
        requested=True,
        wait_budget_seconds=300,
        consultation={
            "reason": "codex_consultation_no_verified_answer",
            "results": [
                {
                    "completion_state": {
                        "status": "permission_required",
                        "elapsed_seconds": 299.8,
                    }
                }
            ],
        },
        fusion={
            "successful_reply_count": 0,
            "usable_claim_count": 0,
        },
        fusion_trace={},
        generation_mode="evidence_baseline",
    )

    assert state["status"] == "degraded"
    assert state["degraded"]
    assert state["reason"] == "codex_permission_not_approved_before_deadline"
    assert state["fallback_strategy"] == "deterministic_local_evidence_baseline"
    assert state["waited_seconds"] == 299.8


def test_codex_brief_execution_state_never_hides_unconsumed_verified_reply():
    from l3_node.work_ledger import _build_codex_brief_execution_state

    state = _build_codex_brief_execution_state(
        requested=True,
        wait_budget_seconds=120,
        consultation={"results": []},
        fusion={
            "successful_reply_count": 1,
            "usable_claim_count": 2,
        },
        fusion_trace={},
        generation_mode="evidence_baseline",
    )

    assert state["status"] == "degraded"
    assert state["reason"] == "verified_codex_reply_not_consumed_by_final_composer"


def test_instant_brief_degrades_to_local_evidence_when_codex_consultation_fails(
    tmp_path, monkeypatch
):
    ledger_home = tmp_path / "work_ledger"
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Offline fallback\n", encoding="utf-8")
    _git(project, "init")
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(ledger_home))
    monkeypatch.setenv("JACHIN_WORK_LEDGER_LLM_ENABLED", "0")

    from l3_node import work_ledger_codex
    from l3_node.work_ledger import generate_instant_work_brief, start_session

    def fail_consultation(*args, **kwargs):
        raise TimeoutError("Codex did not reply before the deadline")

    monkeypatch.setattr(
        work_ledger_codex,
        "consult_codex_for_brief",
        fail_consultation,
    )
    start_session(
        title="Codex timeout fallback",
        project_path=str(project),
        user_goal="Generate an evidence-only brief if Codex is unavailable.",
        auto_collect=True,
    )

    result = generate_instant_work_brief(1, consult_codex=True)

    assert result["generation_mode"] == "evidence_baseline"
    assert result["codex_consultation"]["reason"] == "consultation_failed:TimeoutError"
    assert result["codex_execution"]["status"] == "degraded"
    assert result["codex_execution"]["fallback_strategy"] == (
        "deterministic_local_evidence_baseline"
    )
    assert result["generated_at"].endswith("+08:00")
    assert f"生成时间：{result['generated_at']}" in result["text"]
    assert result["text"].strip()
    assert Path(result["path"]).is_file()


def test_instant_brief_retries_with_quality_feedback(monkeypatch):
    from l3_node import work_ledger_llm

    rejected = {
        "brief": (
            "# 工作简报\n"
            "## 一、完成与推进\n1. 修改 work_ledger.py（M）。\n"
            "## 二、涉及项目与模块\n1. work_ledger.py。\n"
            "## 三、风险与未完成\n1. 尚需验证。\n"
            "## 四、下一步计划\n1. 继续测试。\n"
            "## 依据边界\n1. 来自 Git 证据。"
        ),
        "fusion_trace": {
            "used_claim_ids": [],
            "used_interpretation_ids": [],
            "used_recommendation_ids": [],
            "ignored_claim_ids": [],
        },
    }
    repaired = {
        "brief": (
            "# 工作简报\n"
            "## 一、完成与推进\n1. 优化工作账本成果提炼，使简报按能力和结果呈现。\n"
            "## 二、涉及项目与模块\n1. 工作账本与复盘能力。\n"
            "## 三、风险与未完成\n1. 尚需真实用户验收。\n"
            "## 四、下一步计划\n1. 执行真实简报验收。\n"
            "## 依据边界\n1. 结论来自 Git 证据。"
        ),
        "fusion_trace": {
            "used_claim_ids": [],
            "used_interpretation_ids": [],
            "used_recommendation_ids": [],
            "ignored_claim_ids": [],
        },
    }
    responses = iter((rejected, repaired))
    monkeypatch.setattr(
        work_ledger_llm,
        "_call_dashscope",
        lambda *args, **kwargs: __import__("json").dumps(
            next(responses),
            ensure_ascii=False,
        ),
    )

    result = work_ledger_llm.refine_instant_work_brief_with_llm(
        index={
            "window_days": 1,
            "recent_changed_files": [{"path": "work_ledger.py"}],
            "session_evidence_digests": [
                {"git": {"diff_patch": "+优化工作账本成果提炼"}}
            ],
        },
        baseline_brief="# 工作简报",
    )

    assert result["ok"]
    assert len(result["attempts"]) == 2
    assert not result["attempts"][0]["ok"]
    assert result["attempts"][1]["ok"]
    assert "work_ledger.py" not in result["text"]
