from __future__ import annotations

from pathlib import Path


def _gap_index() -> dict:
    return {
        "verified_outcomes": [],
        "valued_outcomes": [],
        "session_evidence_digests": [
            {
                "session_id": "session-1",
                "title": "改进工作简报",
                "project_name": "jachin-system-main",
                "project_path": r"D:\Projects\jachi\jachin-system-main",
                "user_goal": "让日报能说明真实工作成果",
                "git": {
                    "changed_files": [
                        {"path": "l3_node/work_ledger.py", "status": "M"},
                        {
                            "path": "clients/desktop/src/console/pages/WorkLedgerPanel.tsx",
                            "status": "M",
                        },
                    ],
                    "diff_stat": "2 files changed, 80 insertions(+)",
                    "diff_patch": "+def generate_instant_work_brief(...):",
                },
                "file_snippets": [],
                "risk_candidates": [],
                "manual_notes": [],
                "ai_work_traces": [],
            }
        ],
    }


def test_gap_detector_only_queries_changed_projects_without_semantic_evidence():
    from l3_node.work_ledger_codex import detect_brief_evidence_gaps

    gaps = detect_brief_evidence_gaps(_gap_index())
    assert len(gaps) == 1
    assert gaps[0]["project_name"] == "jachin-system-main"
    assert "accomplishment_meaning" in gaps[0]["gap_keys"]
    assert "next_steps" in gaps[0]["gap_keys"]

    complete = _gap_index()
    complete["verified_outcomes"] = [
        {
            "session_id": "session-1",
            "canonical_summary": "即时简报已通过真实生成验证",
        }
    ]
    row = complete["session_evidence_digests"][0]
    row["manual_notes"] = ["用户确认简报已经可读"]
    row["risk_candidates"] = [{"path": "x.py", "line": 1, "text": "待验证"}]
    row["ai_work_traces"] = [
        {
            "buckets": {
                "actions": ["已接入即时简报"],
                "decisions": ["仅使用证据"],
                "failures": ["视觉链路尚未 live 验证"],
                "next_steps": ["运行 live smoke"],
            }
        }
    ]
    assert detect_brief_evidence_gaps(complete) == []


def test_dynamic_prompt_tracks_evidence_and_changes_hash():
    from l3_node.work_ledger_codex import (
        build_codex_work_plan_prompt,
        detect_brief_evidence_gaps,
    )

    gap = detect_brief_evidence_gaps(_gap_index())[0]
    prompt, meta = build_codex_work_plan_prompt(gap)
    assert "工作计划协作者" in prompt
    assert "l3_node/work_ledger.py" in prompt
    assert r"D:\Projects\jachi\jachin-system-main" in prompt
    assert "完成边界" in prompt
    assert meta["conversation_name"] == "工作计划"

    changed = dict(gap)
    changed["diff_patch"] = "+different implementation"
    _, changed_meta = build_codex_work_plan_prompt(changed)
    assert changed_meta["prompt_hash"] != meta["prompt_hash"]


def test_codex_visual_click_uses_model_coordinate_not_window_ratio(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    clicks: list[tuple[int, int]] = []

    class FakeWin:
        def active_rect(self):
            return ("Codex", 100, 50, 1000, 700)

        def active_title(self):
            return "Codex"

    class FakeIO:
        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

        def click(self, x, y, wait=0.2):
            clicks.append((x, y))

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.win = FakeWin()
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "_ensure_codex_foreground",
        lambda timeout=3.0: {"ok": True, "detail": "foreground"},
    )
    monkeypatch.setattr(
        automation,
        "_codex_accessibility_snapshot",
        lambda **kwargs: {"ok": True, "controls": []},
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_qwen_vision_codex_ui",
        lambda *args, **kwargs: {
            "ok": True,
            "detail": "located",
            "result": {"center_x": 217, "center_y": 333, "found": True},
        },
    )

    result = automation._codex_visual_click_target(
        action="locate_composer",
        project_name="jachin-system-main",
    )
    assert result["ok"]
    assert result["local_point"] == {"x": 217, "y": 333}
    assert clicks == [(317, 383)]


def test_codex_visual_click_uses_verified_snapshot_when_active_rect_flickers(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    clicks: list[tuple[int, int]] = []

    class FakeWin:
        def active_rect(self):
            return None

        def active_title(self):
            return "Codex"

    class FakeIO:
        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

        def click(self, x, y, wait=0.2):
            clicks.append((x, y))

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.win = FakeWin()
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "_ensure_codex_foreground",
        lambda timeout=3.0: {
            "ok": True,
            "detail": "foreground",
            "environment_guard": {
                "active": {
                    "title": "Codex",
                    "rect": {
                        "left": 100,
                        "top": 50,
                        "width": 1000,
                        "height": 700,
                    },
                }
            },
        },
    )
    monkeypatch.setattr(
        automation,
        "_codex_accessibility_snapshot",
        lambda **kwargs: {"ok": True, "controls": []},
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_qwen_vision_codex_ui",
        lambda *args, **kwargs: {
            "ok": True,
            "detail": "located",
            "result": {"center_x": 217, "center_y": 333, "found": True},
        },
    )

    result = automation._codex_visual_click_target(
        action="locate_composer",
        project_name="jachin-system-main",
    )

    assert result["ok"]
    assert clicks == [(317, 383)]


def test_codex_context_verification_requires_two_consistent_samples(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    class FakeWin:
        def active_title(self):
            return "Codex"

        def active_snapshot(self):
            return {
                "title": "Codex",
                "process": "ChatGPT.exe",
                "process_path": (
                    r"C:\Program Files\WindowsApps"
                    r"\OpenAI.Codex_test_x64__package"
                    r"\app\ChatGPT.exe"
                ),
            }

    class FakeIO:
        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.win = FakeWin()
    automation.env = os_tasks.EnvironmentVerifier(automation.win)
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "_ensure_codex_foreground",
        lambda timeout=3.0: {
            "ok": True,
            "detail": "foreground",
            "after": "Codex",
        },
    )
    monkeypatch.setattr(
        automation,
        "_codex_accessibility_snapshot",
        lambda **kwargs: {
            "ok": True,
            "project_matches": [{"name": "jachin-system-main"}],
            "conversation_matches": [{"name": "工作计划"}],
        },
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_qwen_vision_codex_ui",
        lambda *args, **kwargs: {
            "ok": True,
            "detail": "codex_context_verified",
            "result": {
                "visible_project": "jachin-system-main",
                "visible_conversation": "工作计划",
                "selected_match": True,
                "composer_visible": True,
            },
        },
    )

    result = automation._codex_verify_work_plan_context(
        project_name="jachin-system-main",
        conversation_name="工作计划",
        label="stable_context",
        sample_delay=0,
    )
    assert result["ok"]
    assert result["verified_samples"] == 2
    assert len(result["samples"]) == 2


def test_codex_context_verification_rejects_missing_visible_identity(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    class FakeWin:
        def active_title(self):
            return "Codex"

    class FakeIO:
        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.win = FakeWin()
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "_ensure_codex_foreground",
        lambda timeout=3.0: {"ok": True, "after": "Codex"},
    )
    monkeypatch.setattr(
        automation,
        "_codex_accessibility_snapshot",
        lambda **kwargs: {"ok": True, "controls": []},
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_qwen_vision_codex_ui",
        lambda *args, **kwargs: {
            "ok": True,
            "detail": "codex_context_verified",
            "result": {
                "visible_project": "",
                "visible_conversation": "",
                "selected_match": True,
                "composer_visible": True,
            },
        },
    )

    result = automation._codex_verify_work_plan_context(
        project_name="jachin-system-main",
        conversation_name="工作计划",
        label="missing_identity",
        sample_delay=0,
    )

    assert result["ok"] is False
    assert result["verified_samples"] == 0


def test_local_ocr_selects_exact_work_plan_row_under_project():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    rows = [
        {
            "text": "jachin-system-main",
            "cx": 100,
            "cy": 371,
            "score": 0.956,
        },
        {
            "text": "评估 Windows MCP 实现",
            "cx": 116,
            "cy": 399,
            "score": 0.94,
        },
        {
            "text": "工作计划",
            "cx": 68,
            "cy": 431,
            "score": 0.993,
        },
        {
            "text": "了解项目用途",
            "cx": 83,
            "cy": 523,
            "score": 0.91,
        },
    ]

    result = _select_codex_ocr_target(
        rows,
        action="locate_conversation",
        project_name="jachin-system-main",
        conversation_name="工作计划",
    )

    assert result["ok"]
    assert result["result"]["center_x"] == 68
    assert result["result"]["center_y"] == 431
    assert result["result"]["visible_conversation"] == "工作计划"


def test_local_ocr_rejects_conversation_without_project_anchor():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    result = _select_codex_ocr_target(
        [
            {
                "text": "工作计划",
                "cx": 68,
                "cy": 431,
                "score": 0.99,
            }
        ],
        action="locate_conversation",
        project_name="jachin-system-main",
        conversation_name="工作计划",
    )

    assert not result["ok"]
    assert result["detail"] == "codex_local_ocr_project_not_found"


def test_local_ocr_verifies_selected_work_plan_context_and_composer():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    rows = [
        {
            "text": "工作计划",
            "cx": 330,
            "cy": 60,
            "score": 0.93,
        },
        {
            "text": "jachin-system-main",
            "cx": 90,
            "cy": 370,
            "score": 0.97,
        },
        {
            "text": "工作计划",
            "cx": 69,
            "cy": 432,
            "score": 1.0,
        },
        {
            "text": "随心输入",
            "cx": 450,
            "cy": 730,
            "score": 1.0,
        },
    ]

    result = _select_codex_ocr_target(
        rows,
        action="verify_context",
        project_name="jachin-system-main",
        conversation_name="工作计划",
    )

    assert result["ok"]
    assert result["result"]["project_match"]
    assert result["result"]["conversation_match"]
    assert result["result"]["selected_match"]
    assert result["result"]["composer_visible"]


def test_local_ocr_ignores_single_letter_false_project_match():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    rows = [
        {"text": "S", "cx": 486, "cy": 129, "score": 0.94},
        {"text": "工作计划", "cx": 526, "cy": 204, "score": 0.91},
        {
            "text": "jachin-system-main",
            "cx": 284,
            "cy": 515,
            "score": 0.98,
        },
        {"text": "工作计划", "cx": 264, "cy": 576, "score": 1.0},
        {"text": "[JACHIN_REF:jcx-current]", "cx": 714, "cy": 616, "score": 0.99},
    ]

    result = _select_codex_ocr_target(
        rows,
        action="verify_context",
        project_name="jachin-system-main",
        conversation_name="工作计划",
    )

    assert result["ok"]
    assert result["result"]["visible_project"] == "jachin-system-main"
    assert result["result"]["selected_match"]


def test_local_ocr_accepts_filled_composer_with_distorted_marker():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    rows = [
        {"text": "工作计划", "cx": 380, "cy": 60, "score": 0.95},
        {
            "text": "jachin-system-main",
            "cx": 90,
            "cy": 370,
            "score": 0.98,
        },
        {"text": "工作计划", "cx": 69, "cy": 432, "score": 1.0},
        {
            "text": "1. 回复第一行必须原样输出 [ACHIN_REF:jcx-current]",
            "cx": 600,
            "cy": 790,
            "score": 0.96,
        },
    ]

    result = _select_codex_ocr_target(
        rows,
        action="verify_context",
        project_name="jachin-system-main",
        conversation_name="工作计划",
    )

    assert result["ok"]
    assert result["result"]["composer_visible"]


def test_local_ocr_locates_reply_copy_from_last_text_bounds():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    result = _select_codex_ocr_target(
        [
            {
                "text": "jachin-system-main",
                "cx": 90,
                "cy": 370,
                "score": 0.98,
                "x1": 20,
                "y1": 360,
                "x2": 160,
                "y2": 380,
            },
            {
                "text": "3. 已完成价值链冒烟验证，全部断言通过；",
                "cx": 600,
                "cy": 690,
                "score": 0.99,
                "x1": 340,
                "y1": 680,
                "x2": 860,
                "y2": 699,
            },
            {
                "text": "依据：work_ledger_value_chain_smoke.json。",
                "cx": 600,
                "cy": 718,
                "score": 0.99,
                "x1": 365,
                "y1": 708,
                "x2": 860,
                "y2": 727,
            },
        ],
        action="locate_latest_reply_copy",
        project_name="jachin-system-main",
        conversation_name="[JACHIN_REF:jcx-current]",
        canvas_width=1048,
        canvas_height=900,
    )

    assert result["ok"]
    assert result["result"]["anchor_strategy"] == "last_assistant_text_footer"
    assert result["result"]["center_x"] == 348
    assert result["result"]["center_y"] == 750


def test_local_ocr_refuses_reply_copy_while_feedback_dialog_obscures_view():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    result = _select_codex_ocr_target(
        [
            {
                "text": "提交反馈",
                "cx": 520,
                "cy": 280,
                "score": 0.99,
                "x1": 450,
                "y1": 260,
                "x2": 590,
                "y2": 300,
            },
            {
                "text": "填写详情（选填）",
                "cx": 520,
                "cy": 430,
                "score": 0.98,
                "x1": 400,
                "y1": 410,
                "x2": 640,
                "y2": 450,
            },
            {
                "text": "旧回复的最后一行。",
                "cx": 600,
                "cy": 718,
                "score": 0.99,
                "x1": 340,
                "y1": 708,
                "x2": 860,
                "y2": 727,
            },
        ],
        action="locate_latest_reply_copy",
        project_name="jachin-system-main",
        conversation_name="[JACHIN_REF:jcx-current]",
        canvas_width=1048,
        canvas_height=900,
    )

    assert not result["ok"]
    assert (
        result["detail"]
        == "codex_transient_feedback_dialog_obstructing_reply"
    )


def test_local_ocr_verifies_reply_context_from_exact_invocation_marker():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    marker = "[JACHIN_REF:jcx-current]"
    result = _select_codex_ocr_target(
        [
            {
                "text": "jachin-system-main",
                "cx": 90,
                "cy": 370,
                "score": 0.98,
            },
            {
                "text": marker,
                "cx": 455,
                "cy": 558,
                "score": 0.99,
            },
        ],
        action="verify_reply_context",
        project_name="jachin-system-main",
        conversation_name=marker,
        canvas_width=1048,
        canvas_height=900,
    )

    assert result["ok"]
    assert (
        result["detail"]
        == "codex_reply_context_verified_by_invocation_marker"
    )
    assert result["result"]["invocation_marker_match"]


def test_copy_recovers_conversation_context_before_reading_clipboard(
    monkeypatch,
):
    import sys
    import types

    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    marker = "[JACHIN_REF:jcx-current]"
    clipboard = {"text": ""}
    fake_pyperclip = types.SimpleNamespace(
        copy=lambda text: clipboard.__setitem__("text", str(text)),
        paste=lambda: clipboard["text"],
    )
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)
    monkeypatch.setattr(
        os_tasks,
        "_import_uia",
        lambda: (None, "uiautomation_not_available"),
    )

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    context_checks = iter(
        [
            {"ok": False, "detail": "codex_local_ocr_context_not_verified"},
            {"ok": True, "detail": "codex_context_verified_stable"},
        ]
    )
    automation._ensure_codex_foreground = lambda timeout=0: {
        "ok": True,
        "detail": "codex_already_foreground",
    }
    automation._codex_verify_work_plan_context = (
        lambda **kwargs: next(context_checks)
    )
    automation._codex_navigate_work_plan_context = lambda **kwargs: {
        "ok": True,
        "detail": "codex_conversation_selected_directly",
    }
    automation.io = types.SimpleNamespace(
        hotkey=lambda *args, **kwargs: clipboard.__setitem__(
            "text",
            (
                f"{marker}\n"
                "已经基于真实证据完成三项工作，并保留了文件路径和测试结果。"
                "第一项完成完整复制验证，第二项完成发布门禁验证，第三项完成价值链冒烟验证。"
            ),
        )
    )

    result = automation._codex_copy_latest_response(
        project_name="jachin-system-main",
        required_marker=marker,
        conversation_name="工作计划",
    )

    assert result["ok"]
    assert result["detail"] == "copied_by_native_shortcut"
    methods = [attempt["method"] for attempt in result["attempts"]]
    assert "copy_context_recovery_navigation" in methods
    assert "copy_context_recovery_verify" in methods


def test_local_ocr_locates_filled_composer_from_footer_control():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    result = _select_codex_ocr_target(
        [
            {"text": "工作计划", "cx": 330, "cy": 60, "score": 0.95},
            {"text": "已有草稿内容", "cx": 700, "cy": 650, "score": 0.99},
            {"text": "5.6 Sol 高", "cx": 1028, "cy": 783, "score": 0.96},
        ],
        action="locate_composer",
        project_name="jachin-system-main",
        conversation_name="工作计划",
        canvas_width=1280,
        canvas_height=820,
    )

    assert result["ok"]
    assert result["result"]["anchor_strategy"] == "footer_control"
    assert 500 < result["result"]["center_x"] < 950
    assert 540 < result["result"]["center_y"] < 783


def test_codex_context_fast_guard_never_calls_remote_vision(tmp_path, monkeypatch):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    class FakeWin:
        def active_title(self):
            return "ChatGPT"

    class FakeIO:
        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.win = FakeWin()
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "_ensure_codex_foreground",
        lambda timeout=3.0: {"ok": True, "after": "ChatGPT"},
    )
    monkeypatch.setattr(
        automation,
        "_codex_accessibility_snapshot",
        lambda **kwargs: {"ok": False, "controls": []},
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_local_ocr_codex_ui",
        lambda *args, **kwargs: {
            "ok": False,
            "detail": "local_context_missing",
            "result": {},
        },
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_qwen_vision_codex_ui",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("remote vision must not run in the atomic submit guard")
        ),
    )

    result = automation._codex_verify_work_plan_context(
        project_name="jachin-system-main",
        conversation_name="工作计划",
        label="fast_guard",
        samples=1,
        sample_delay=0,
        allow_remote_fallback=False,
    )

    assert not result["ok"]
    assert result["required_samples"] == 1
    assert result["samples"][0]["vision"]["detail"] == "local_context_missing"


def test_local_ocr_context_requires_selected_conversation_header():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _select_codex_ocr_target,
    )

    result = _select_codex_ocr_target(
        [
            {
                "text": "jachin-system-main",
                "cx": 90,
                "cy": 370,
                "score": 0.97,
            },
            {
                "text": "工作计划",
                "cx": 69,
                "cy": 432,
                "score": 1.0,
            },
            {
                "text": "随心输入",
                "cx": 450,
                "cy": 730,
                "score": 1.0,
            },
        ],
        action="verify_context",
        project_name="jachin-system-main",
        conversation_name="工作计划",
    )

    assert not result["ok"]
    assert not result["result"]["selected_match"]


def test_codex_reply_schema_is_strict_for_reports_and_generic_for_questions():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _codex_reply_schema_for_prompt,
    )

    assert (
        _codex_reply_schema_for_prompt(
            "请整理项目进展、当前风险和下一步计划。"
        )
        == "work_plan"
    )
    assert (
        _codex_reply_schema_for_prompt(
            "请只读确认当前窗口是否恢复，并给出一条本地证据。"
        )
        == "generic"
    )


def test_codex_work_plan_mouse_failsafe_becomes_terminal_evidence(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks
    from l3_node import codex_invocation_manager

    class FakeManager:
        def __init__(self):
            self.released = []

        def get(self, invocation_id):
            return {
                "invocation_id": invocation_id,
                "status": "running",
                "stage": "locate_composer",
                "history": [
                    {
                        "at": "2026-07-24T00:00:00+00:00",
                        "stage": "locate_composer",
                        "status": "running",
                        "detail": "locating composer",
                    }
                ],
            }

        def release(self, invocation_id, **kwargs):
            self.released.append((invocation_id, kwargs))
            return {
                "invocation_id": invocation_id,
                "status": kwargs["status"],
                "stage": kwargs["stage"],
                "detail": kwargs["detail"],
                "history": [
                    {
                        "at": "2026-07-24T00:00:01+00:00",
                        "stage": kwargs["stage"],
                        "status": kwargs["status"],
                        "detail": kwargs["detail"],
                    }
                ],
            }

    manager = FakeManager()
    monkeypatch.setattr(
        codex_invocation_manager,
        "get_codex_invocation_manager",
        lambda: manager,
    )
    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    monkeypatch.setattr(
        automation,
        "_codex_work_plan_query_impl",
        lambda **kwargs: (_ for _ in ()).throw(
            os_tasks.MouseFailSafeInterrupt(
                action="screenshot_active_window",
                position=(0, 0),
                screen=(1920, 1080),
            )
        ),
    )

    result = automation.codex_work_plan_query(
        project_name="Jachin",
        project_path=r"D:\Projects\jachi\jachin-system-main",
        prompt="只读检查。",
        invocation_id="jcx-failsafe-test",
    )

    assert not result.ok
    assert result.detail == "mouse_failsafe_triggered"
    assert result.evidence["side_effect_status"] == (
        "interrupted_by_user_safety_corner"
    )
    assert result.evidence["invocation_manager_final"]["status"] == "cancelled"
    assert Path(result.evidence["evidence_path"]).exists()
    assert manager.released


def test_desktop_screenshot_is_read_only_when_pointer_is_at_safety_edge(
    tmp_path,
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    class FakeImage:
        def save(self, path):
            Path(path).write_bytes(b"png")

    class FakePyAutoGui:
        def position(self):
            return (0, 0)

        def size(self):
            return (1920, 1080)

        def screenshot(self, region=None):
            return FakeImage()

    io = os_tasks.DesktopIO.__new__(os_tasks.DesktopIO)
    io.pyautogui = FakePyAutoGui()
    io.win = type(
        "FakeWin",
        (),
        {
            "active_rect": lambda self: ("Codex", 10, 10, 800, 600),
            "active_title": lambda self: "Codex",
        },
    )()

    path = io.screenshot_active_window(tmp_path, "read_only")

    assert Path(path).exists()


def test_codex_context_verification_rejects_changed_conversation(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    class FakeWin:
        def active_title(self):
            return "Codex"

        def active_snapshot(self):
            return {
                "title": "Codex",
                "process": "ChatGPT.exe",
                "process_path": (
                    r"C:\Program Files\WindowsApps"
                    r"\OpenAI.Codex_test_x64__package"
                    r"\app\ChatGPT.exe"
                ),
            }

    class FakeIO:
        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

    responses = iter(
        [
            {
                "ok": True,
                "detail": "codex_context_verified",
                "result": {
                    "visible_project": "jachin-system-main",
                    "visible_conversation": "工作计划",
                    "selected_match": True,
                    "composer_visible": True,
                },
            },
            {
                "ok": True,
                "detail": "codex_context_verified",
                "result": {
                    "visible_project": "jachin-system-main",
                    "visible_conversation": "其他会话",
                    "selected_match": True,
                    "composer_visible": True,
                },
            },
        ]
    )
    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.win = FakeWin()
    automation.env = os_tasks.EnvironmentVerifier(automation.win)
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "_ensure_codex_foreground",
        lambda timeout=3.0: {"ok": True, "after": "Codex"},
    )
    monkeypatch.setattr(
        automation,
        "_codex_accessibility_snapshot",
        lambda **kwargs: {"ok": True, "controls": []},
    )
    monkeypatch.setattr(
        os_tasks,
        "_call_qwen_vision_codex_ui",
        lambda *args, **kwargs: next(responses),
    )

    result = automation._codex_verify_work_plan_context(
        project_name="jachin-system-main",
        conversation_name="工作计划",
        label="changed_context",
        sample_delay=0,
    )
    assert not result["ok"]
    assert result["verified_samples"] == 1


def test_codex_navigation_uses_project_expand_before_search(monkeypatch):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    actions: list[str] = []

    class FakeIO:
        def hotkey(self, *keys, wait=0.1):
            raise AssertionError("search should not be used")

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.io = FakeIO()

    def click_target(**kwargs):
        action = kwargs["action"]
        actions.append(action)
        if actions == ["locate_conversation"]:
            return {"ok": False, "detail": "conversation_hidden"}
        return {"ok": True, "detail": "clicked"}

    monkeypatch.setattr(automation, "_codex_visual_click_target", click_target)
    result = automation._codex_navigate_work_plan_context(
        project_name="jachin-system-main",
        conversation_name="工作计划",
        label="navigate",
    )

    assert result["ok"]
    assert result["selected_path"] == "project_expand"
    assert actions == [
        "locate_conversation",
        "locate_project",
        "locate_conversation",
    ]


def test_codex_navigation_uses_visual_search_after_hidden_sidebar(
    monkeypatch,
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    actions: list[str] = []

    class FakeIO:
        def __init__(self):
            self.hotkeys: list[tuple[str, ...]] = []
            self.pasted: list[str] = []

        def hotkey(self, *keys, wait=0.1):
            self.hotkeys.append(tuple(keys))

        def paste(self, text, wait=0.2):
            self.pasted.append(text)

        def press(self, key, presses=1, wait=0.15):
            raise AssertionError("escape should not be needed after success")

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.io = FakeIO()

    results = iter([False, False, True, True])

    def click_target(**kwargs):
        actions.append(kwargs["action"])
        ok = next(results)
        return {"ok": ok, "detail": "clicked" if ok else "not_visible"}

    monkeypatch.setattr(automation, "_codex_visual_click_target", click_target)
    result = automation._codex_navigate_work_plan_context(
        project_name="jachin-system-main",
        conversation_name="工作计划",
        label="navigate_search",
    )

    assert result["ok"]
    assert result["selected_path"] == "sidebar_search"
    assert actions == [
        "locate_conversation",
        "locate_project",
        "locate_search",
        "locate_conversation",
    ]
    assert automation.io.hotkeys == [("ctrl", "a")]
    assert automation.io.pasted == ["工作计划"]


def test_context_mismatch_stops_before_prompt_is_pasted(tmp_path, monkeypatch):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks
    from l3_node import codex_invocation_manager

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_CODEX_RECOVERY_DISABLED", "1")
    monkeypatch.setattr(codex_invocation_manager, "_DEFAULT_MANAGER", None)

    class FakeIO:
        def __init__(self):
            self.pasted = []

        def paste(self, text, wait=0.2):
            self.pasted.append(text)

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "ensure_app",
        lambda *args, **kwargs: os_tasks.TaskResult(
            "open_app", True, "opened", {}
        ),
    )
    monkeypatch.setattr(
        automation,
        "_codex_visual_click_target",
        lambda **kwargs: {"ok": True, "detail": "clicked"},
    )
    monkeypatch.setattr(
        automation,
        "_codex_verify_work_plan_context",
        lambda **kwargs: {"ok": False, "detail": "wrong_conversation"},
    )

    result = automation.codex_work_plan_query(
        project_name="jachin-system-main",
        project_path=r"D:\Projects\jachi\jachin-system-main",
        prompt="请解释今天的改动",
    )
    assert not result.ok
    assert result.detail == "codex_work_plan_context_mismatch"
    assert automation.io.pasted == []


def test_codex_query_records_manifest_driven_navigation_recovery(
    tmp_path,
    monkeypatch,
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks
    from l3_node import codex_invocation_manager

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.delenv("JACHIN_CODEX_RECOVERY_DISABLED", raising=False)
    monkeypatch.setattr(codex_invocation_manager, "_DEFAULT_MANAGER", None)
    monkeypatch.setattr(os_tasks.time, "sleep", lambda _seconds: None)

    class FakeIO:
        def __init__(self):
            self.pasted: list[str] = []

        def paste(self, text, wait=0.2):
            self.pasted.append(text)

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "ensure_app",
        lambda *args, **kwargs: os_tasks.TaskResult(
            "open_app", True, "opened", {}
        ),
    )

    strategies: list[str] = []

    def navigate(**kwargs):
        strategy = str(kwargs.get("strategy") or "")
        strategies.append(strategy)
        return {
            "ok": strategy == "expand_project_then_conversation",
            "detail": (
                "selected"
                if strategy == "expand_project_then_conversation"
                else "conversation_not_found"
            ),
            "selected_path": strategy,
        }

    monkeypatch.setattr(
        automation,
        "_codex_navigate_work_plan_context",
        navigate,
    )
    monkeypatch.setattr(
        automation,
        "_codex_verify_work_plan_context",
        lambda **kwargs: {"ok": False, "detail": "context_mismatch"},
    )

    result = automation.codex_work_plan_query(
        project_name="jachin-system-main",
        project_path=r"D:\Projects\jachi\jachin-system-main",
        prompt="请解释今天的改动",
    )

    assert not result.ok
    assert strategies[:2] == [
        "direct_conversation",
        "expand_project_then_conversation",
    ]
    decisions = result.evidence["recovery"]["decisions"]
    assert decisions[0]["strategy"] == "expand_project_then_conversation"
    assert decisions[0]["history_reasons"] == [
        "navigate_conversation:conversation_not_found"
    ]
    assert result.evidence["navigation_recovery_attempts"][0]["result"]["ok"]
    assert automation.io.pasted == []


def test_context_change_after_paste_clears_prompt_without_submit(
    tmp_path, monkeypatch
):
    from l3_client.local_mcps.windows_uia_mcp import os_tasks
    from l3_node import codex_invocation_manager

    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_CODEX_RECOVERY_DISABLED", "1")
    monkeypatch.setattr(codex_invocation_manager, "_DEFAULT_MANAGER", None)

    class FakeIO:
        def __init__(self):
            self.pasted: list[str] = []
            self.pressed: list[str] = []
            self.hotkeys: list[tuple[str, ...]] = []

        def paste(self, text, wait=0.2):
            self.pasted.append(text)

        def screenshot_active_window(self, out_dir, label):
            path = Path(out_dir) / f"{label}.png"
            path.write_bytes(b"fake")
            return str(path)

        def hotkey(self, *keys, wait=0.1):
            self.hotkeys.append(tuple(keys))

        def press(self, key, presses=1, wait=0.15):
            self.pressed.extend([key] * presses)

    automation = os_tasks.WindowsOSAutomation.__new__(
        os_tasks.WindowsOSAutomation
    )
    automation.out_dir = tmp_path
    automation.io = FakeIO()
    monkeypatch.setattr(
        automation,
        "ensure_app",
        lambda *args, **kwargs: os_tasks.TaskResult(
            "open_app", True, "opened", {}
        ),
    )
    monkeypatch.setattr(
        automation,
        "_codex_navigate_work_plan_context",
        lambda **kwargs: {"ok": True, "detail": "selected"},
    )
    monkeypatch.setattr(
        automation,
        "_codex_visual_click_target",
        lambda **kwargs: {"ok": True, "detail": "clicked"},
    )
    context_results = iter(
        [
            {"ok": True, "detail": "stable"},
            {"ok": False, "detail": "context_changed"},
        ]
    )
    monkeypatch.setattr(
        automation,
        "_codex_verify_work_plan_context",
        lambda **kwargs: next(context_results),
    )

    result = automation.codex_work_plan_query(
        project_name="jachin-system-main",
        project_path=r"D:\Projects\jachi\jachin-system-main",
        prompt="请解释今天的改动",
    )
    assert not result.ok
    assert result.detail == "codex_context_changed_before_submit"
    assert automation.io.pasted
    assert automation.io.hotkeys == [("ctrl", "a"), ("ctrl", "a")]
    assert automation.io.pressed == ["backspace", "backspace"]


def test_codex_invocation_contract_rejects_stale_reply():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _match_codex_invocation_answer,
        _prepare_codex_invocation_prompt,
    )

    prompt, invocation_id, marker = _prepare_codex_invocation_prompt(
        "请解释今天的改动",
        "jcx-current",
    )
    assert marker in prompt
    assert invocation_id == "jcx-current"

    matched = _match_codex_invocation_answer(
        f"{marker}\n1. 本次改动补充了工作链关联校验。",
        invocation_id,
    )
    assert matched["ok"]
    assert marker not in matched["clean_answer"]

    stale = _match_codex_invocation_answer(
        "[JACHIN_REF:jcx-previous]\n1. 这是上一次任务的回答。",
        invocation_id,
    )
    assert not stale["ok"]
    assert stale["clean_answer"] == ""


def test_consultation_deduplicates_same_prompt_hash(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import append_evidence, start_session
    from l3_node.work_ledger_codex import (
        build_codex_work_plan_prompt,
        consult_codex_for_brief,
        detect_brief_evidence_gaps,
    )

    project = tmp_path / "project"
    project.mkdir()
    session = start_session(
        title="Codex collaboration",
        project_path=str(project),
        auto_collect=False,
    )["session"]
    index = _gap_index()
    row = index["session_evidence_digests"][0]
    row["session_id"] = session["session_id"]
    row["project_name"] = session["project_name"]
    row["project_path"] = session["project_path"]
    gap = detect_brief_evidence_gaps(index)[0]
    _, meta = build_codex_work_plan_prompt(gap)
    append_evidence(
        session["session_id"],
        source="codex_work_plan_consultation",
        summary="already asked",
        payload={
            "ok": True,
            "prompt_hash": meta["prompt_hash"],
            "answer": "已有回答",
        },
    )

    class MustNotRun:
        def codex_work_plan_query(self, **kwargs):
            raise AssertionError("deduplicated query must not reach Codex")

    result = consult_codex_for_brief(
        index,
        automation_factory=lambda: MustNotRun(),
    )
    assert result["ok"]
    assert result["results"][0]["deduplicated"]


def test_work_chain_plans_start_checkpoint_and_end_day_scenarios():
    from l3_node.work_ledger_codex import plan_codex_work_chain

    session = {
        "session_id": "work-1",
        "project_name": "Jachin",
        "project_path": r"D:\Projects\jachi\jachin-system-main",
        "title": "优化架构",
        "user_goal": "看看这个架构应该怎么改",
    }
    start_plan = plan_codex_work_chain(
        session,
        [
            {
                "source": "work_session",
                "payload": {"user_goal": session["user_goal"]},
            }
        ],
        phase="task_start",
    )
    start_ids = {item["scenario_id"] for item in start_plan["requests"]}
    assert "task_alignment" in start_ids
    assert "decision_support" in start_ids

    checkpoint_plan = plan_codex_work_chain(
        session,
        [
            {
                "source": "work_checkpoint",
                "payload": {
                    "fingerprint": "abc",
                    "changed_files": [{"path": "core.py", "status": "M"}],
                    "risk_candidates": [{"path": "core.py", "text": "TODO"}],
                },
            }
        ],
        phase="checkpoint",
    )
    checkpoint_ids = {
        item["scenario_id"] for item in checkpoint_plan["requests"]
    }
    assert "progress_explanation" in checkpoint_ids
    assert "failure_diagnosis" in checkpoint_ids

    end_plan = plan_codex_work_chain(
        session,
        [
            {
                "source": "git_snapshot",
                "payload": {
                    "changed_files": [{"path": "core.py", "status": "M"}],
                },
            }
        ],
        phase="end_day",
    )
    end_ids = {item["scenario_id"] for item in end_plan["requests"]}
    assert "completion_review" in end_ids
    assert "continuation_handoff" in end_ids


def test_work_chain_plan_is_deduplicated_and_scenario_result_closes_request(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    project = tmp_path / "project"
    project.mkdir()
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import TaskResult
    from l3_node.work_ledger import start_session
    from l3_node.work_ledger_codex import (
        consult_codex_for_scenario,
        get_codex_work_chain_state,
        record_codex_work_chain_plan,
    )

    session = start_session(
        title="架构方案",
        project_path=str(project),
        user_goal="这个架构应该怎么选择",
        auto_collect=False,
    )["session"]
    second = record_codex_work_chain_plan(
        session["session_id"],
        phase="task_start",
    )
    assert second["new_request_count"] == 0
    state = get_codex_work_chain_state(session["session_id"])
    pending = next(
        item for item in state["requests"] if item["status"] == "pending"
    )
    captured: dict = {}

    class FakeAutomation:
        def codex_work_plan_query(self, **kwargs):
            captured.update(kwargs)
            invocation_id = kwargs["invocation_id"]
            return TaskResult(
                "windows_codex_work_plan_query",
                True,
                "codex_work_plan_reply_ready",
                {
                    "answer": "1. 结论：采用能力契约。\n2. 证据：当前目标包含架构取舍。\n3. 不确定：尚未运行测试。\n4. 建议：先做烟测。",
                    "answer_source": "qwen_vision",
                    "answer_validation": {"ok": True},
                    "invocation_match": {
                        "ok": True,
                        "invocation_id": invocation_id,
                        "marker_found": True,
                    },
                    "evidence_path": str(tmp_path / "codex.evidence.json"),
                },
            )

    result = consult_codex_for_scenario(
        session["session_id"],
        pending["request_key"],
        automation_factory=lambda: FakeAutomation(),
    )
    assert result["ok"]
    assert captured["context_digest"]
    assert captured["context_stats"]["within_budget"]
    assert result["context_pack"]["digest"] == captured["context_digest"]
    closed = get_codex_work_chain_state(session["session_id"])
    completed = next(
        item
        for item in closed["requests"]
        if item["request_key"] == pending["request_key"]
    )
    assert completed["status"] == "completed"


def test_work_chain_state_keeps_only_latest_request_per_scenario(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JACHIN_WORK_LEDGER_HOME", str(tmp_path / "ledger"))
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.work_ledger import append_evidence, start_session
    from l3_node.work_ledger_codex import get_codex_work_chain_state

    session = start_session(
        title="Repeated diagnostics",
        project_path=str(tmp_path),
        user_goal="Diagnose repeated failures.",
        auto_collect=False,
    )["session"]
    for request_key in ("old-request", "new-request"):
        append_evidence(
            session["session_id"],
            source="codex_work_chain_plan",
            summary="diagnosis",
            payload={
                "requests": [
                    {
                        "scenario_id": "failure_diagnosis",
                        "request_key": request_key,
                        "label": "失败诊断与恢复建议",
                        "phase": "checkpoint",
                        "priority": 90,
                        "status": "pending",
                    }
                ]
            },
            trust_level="system_observed",
        )

    state = get_codex_work_chain_state(session["session_id"])

    assert state["request_count"] == 1
    assert state["pending_count"] == 1
    assert state["requests"][0]["request_key"] == "new-request"


def test_codex_consultation_enters_llm_digest_as_explanatory_evidence():
    from l3_node.work_ledger_llm import build_evidence_digest

    digest = build_evidence_digest(
        {
            "session_id": "work-1",
            "title": "整理工作链",
            "project_name": "Jachin",
            "project_path": r"D:\Projects\jachi\jachin-system-main",
        },
        [
            {
                "source": "codex_work_plan_consultation",
                "summary": "Codex 已完成改动含义与工作进展协作",
                "trust_level": "system_observed",
                "payload": {
                    "scenario_id": "progress_explanation",
                    "answer": "1. 结论：工作链新增了证据驱动的 Codex 协作节点。",
                    "answer_validation": {"ok": True},
                },
            }
        ],
    )

    consultations = digest["codex_work_plan_consultations"]
    assert len(consultations) == 1
    assert consultations[0]["scenario_id"] == "progress_explanation"
    assert consultations[0]["trust_level"] == "system_observed"
    assert consultations[0]["answer_validation"]["ok"] is True
    assert digest["fact_fusion_policy"]["final_author"] == "jachin"
    assert digest["fact_fusion_policy"]["codex_direct_quote_allowed"] is False


def test_instant_brief_quality_gate_rejects_codex_verbatim_copy():
    from l3_node.work_ledger_llm import validate_instant_brief_output

    codex_sentence = "本次新增了工作链协作节点，可以根据证据缺口自动选择是否询问 Codex。"
    index = {
        "recent_codex_consultations": [{"answer": codex_sentence}],
        "recent_notes": [{"summary": "已记录协作链调整"}],
        "session_evidence_digests": [],
    }
    quality = validate_instant_brief_output(
        {
            "brief": (
                "## 完成与推进\n"
                f"1. {codex_sentence}\n"
                "## 涉及项目与模块\n"
                "1. 工作台与工作记录模块。\n"
                "## 风险与未完成\n"
                "1. 仍需完成真实桌面验证。\n"
                "## 下一步计划\n"
                "1. 运行视觉链路烟测。\n"
                "## 依据边界\n"
                "1. 依据来自当前工作记录。"
            )
        },
        index,
    )

    assert not quality["ok"]
    assert any(
        issue.startswith("brief_copies_codex_verbatim:")
        for issue in quality["issues"]
    )


def test_daily_and_weekly_quality_gates_reject_codex_verbatim_copy():
    from l3_node.work_ledger_llm import (
        validate_refined_outputs,
        validate_weekly_report_outputs,
    )

    codex_sentence = "失败主要发生在上下文验证阶段，下一步应先核对目标会话再重新执行。"
    evidence = [
        {
            "source": "codex_work_plan_consultation",
            "payload": {"answer": codex_sentence},
        }
    ]
    daily_quality = validate_refined_outputs(
        {
            "daily_report": (
                "## 完成与推进\n"
                "1. 整理了工作链证据。\n"
                "## 涉及模块\n"
                "1. 工作记录模块。\n"
                "## 风险与未完成\n"
                f"1. {codex_sentence}\n"
                "## 下一步\n"
                "1. 运行真实验证。"
            ),
            "continuation_prompt": "1. 先读取真实文件和 Git 状态。",
            "lark_brief": "1. 已整理工作链证据。\n2. 下一步运行真实验证。",
        },
        evidence,
    )
    assert not daily_quality["ok"]
    assert any(
        issue.startswith("work_outputs_copy_codex_verbatim:")
        for issue in daily_quality["issues"]
    )

    weekly_quality = validate_weekly_report_outputs(
        {
            "weekly_report": (
                "## 本周进展\n"
                "1. 已整理工作链证据。\n"
                "## 风险与未完成\n"
                f"1. {codex_sentence}\n"
                "## 下一步\n"
                "1. 运行真实验证。\n"
                "## 依据边界\n"
                "1. 依据来自 Work Ledger。"
            )
        },
        {"sessions": [], "recent_codex_consultations": [{"answer": codex_sentence}]},
    )
    assert not weekly_quality["ok"]
    assert any(
        issue.startswith("weekly_report_copies_codex_verbatim:")
        for issue in weekly_quality["issues"]
    )
