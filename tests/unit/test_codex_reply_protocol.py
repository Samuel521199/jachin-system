from l3_client.local_mcps.windows_uia_mcp.codex_reply_protocol import (
    advance_wait_state,
    select_reply,
    validate_reply_candidate,
)


MARKER = "[JACHIN_REF:jcx-current]"
ANSWER = (
    f"{MARKER}\n"
    "1. 最新进展：完成工作账本的 Context Pack 构建，并修改 work_ledger_codex.py。\n"
    "2. 证据：相关单元测试已经通过，Git diff 中可以看到新增协议模块。\n"
    "3. 当前风险：还没有执行真实桌面烟测。\n"
    "4. 下一步：补充回复提取和失败恢复测试。"
)


def test_wait_state_requires_marker_stability_and_inactive_generation():
    first = advance_wait_state(
        {},
        ocr_text=ANSWER,
        controls=[{"name": "Copy response"}],
        invocation_marker=MARKER,
        elapsed_seconds=4,
    )
    assert first["status"] == "reply_observed"
    assert not first["complete"]

    second = advance_wait_state(
        first,
        ocr_text=ANSWER,
        controls=[{"name": "Copy response"}],
        invocation_marker=MARKER,
        elapsed_seconds=8,
    )
    assert second["status"] == "complete"
    assert second["completion_signals"]["invocation_marker_found"]
    assert second["completion_signals"]["reply_stable"]

    active = advance_wait_state(
        second,
        ocr_text=ANSWER + "\n正在生成",
        controls=[{"name": "Stop generating"}],
        invocation_marker=MARKER,
        elapsed_seconds=16,
    )
    assert active["status"] == "generating"
    assert not active["complete"]


def test_wait_state_does_not_treat_answer_word_running_as_ui_activity():
    text = (
        f"{MARKER}\n"
        "The evidence record previously used status running, but the current "
        "Codex response is now complete and ready for verification."
    )
    first = advance_wait_state(
        None,
        ocr_text=text,
        controls=[],
        invocation_marker=MARKER,
    )
    second = advance_wait_state(
        first,
        ocr_text=text,
        controls=[],
        invocation_marker=MARKER,
    )

    assert first["active"] is False
    assert second["complete"] is True


def test_wait_state_tolerates_ocr_marker_punctuation_only_after_reply():
    marker = "[JACHIN_REF:jcx-ocr-1]"
    prompt_only = advance_wait_state(
        None,
        ocr_text=(
            "用户请求\n"
            "[JACHIN_REF;jcx-ocr-1]\n"
            "请检查当前项目并给出证据。"
        ),
        invocation_marker=marker,
        minimum_reply_chars=20,
        stable_samples_required=1,
    )
    assert not prompt_only["marker_found"]
    assert prompt_only["marker_occurrences"] == 1

    answer_text = (
        "用户请求\n"
        "[JACHIN_REF;jcx-ocr-1]\n"
        "请检查当前项目并给出证据。\n"
        "助手回复\n"
        "[JACHIN_REF;jcx-ocr-1]\n"
        "本次只读检查已完成，并给出本地测试证据。"
    )
    observed = advance_wait_state(
        prompt_only,
        ocr_text=answer_text,
        invocation_marker=marker,
        minimum_reply_chars=20,
        stable_samples_required=1,
    )
    complete = advance_wait_state(
        observed,
        ocr_text=answer_text,
        invocation_marker=marker,
        minimum_reply_chars=20,
        stable_samples_required=1,
    )
    assert complete["complete"]
    assert complete["marker_match_mode"] == "ocr_tolerant"
    assert complete["marker_occurrences"] == 2


def test_wait_state_keeps_marker_seen_when_long_reply_scrolls_it_offscreen():
    marker = "[JACHIN_REF:jcx-long-reply]"
    marker_view = (
        f"{marker}\n"
        "第一节已经生成，后续还会继续输出更多基于本地证据的完整内容。"
    )
    observed = advance_wait_state(
        None,
        ocr_text=marker_view,
        invocation_marker=marker,
        minimum_reply_chars=20,
    )
    assert observed["marker_visible"]
    assert observed["marker_seen"]

    scrolled_view = (
        "第七节 当前风险：相关 UI 依赖仍需恢复。\n"
        "第八节 下一步：完成异常矩阵并记录真实 Evidence。"
    )
    scrolled = advance_wait_state(
        observed,
        ocr_text=scrolled_view,
        invocation_marker=marker,
        minimum_reply_chars=20,
    )
    completed = advance_wait_state(
        scrolled,
        ocr_text=scrolled_view,
        invocation_marker=marker,
        minimum_reply_chars=20,
    )

    assert not completed["marker_visible"]
    assert completed["marker_seen"]
    assert completed["marker_match_mode"] == "exact"
    assert completed["complete"]


def test_wait_state_can_defer_marker_until_full_reply_extraction():
    text = (
        "第七节 当前风险：相关 UI 依赖仍需恢复。\n"
        "第八节 下一步：完成异常矩阵并记录真实 Evidence。"
    )
    state: dict = {}
    for elapsed in (4, 12, 20, 28):
        state = advance_wait_state(
            state,
            ocr_text=text,
            invocation_marker="[JACHIN_REF:jcx-offscreen]",
            elapsed_seconds=elapsed,
            minimum_reply_chars=20,
            allow_deferred_marker_completion=True,
            deferred_marker_min_seconds=20,
            deferred_marker_stable_samples=3,
        )

    assert state["complete"]
    assert not state["marker_seen"]
    assert state["marker_validation_deferred"]
    assert state["completion_signals"][
        "invocation_marker_validation_deferred"
    ]


def test_wait_state_does_not_defer_marker_without_explicit_opt_in():
    text = "回复内容已经稳定，但没有当前调用的关联标记。"
    state: dict = {}
    for elapsed in (4, 12, 20, 28):
        state = advance_wait_state(
            state,
            ocr_text=text,
            invocation_marker="[JACHIN_REF:jcx-missing]",
            elapsed_seconds=elapsed,
            minimum_reply_chars=20,
        )

    assert not state["complete"]
    assert state["status"] == "waiting"


def test_wait_state_surfaces_permission_and_generation_errors():
    permission = advance_wait_state(
        {},
        ocr_text=f"{MARKER}\n需要批准后继续读取文件。",
        controls=[{"name": "Approval required"}],
        invocation_marker=MARKER,
    )
    assert permission["status"] == "permission_required"

    failed = advance_wait_state(
        {},
        ocr_text="Network error. Failed to generate response.",
        invocation_marker=MARKER,
    )
    assert failed["status"] == "generation_error"


def test_wait_state_ignores_permission_language_inside_older_answer():
    old_answer = "\n".join(
        [
            "旧回复中讨论了需要确认和请求批准的产品流程。",
            *[f"旧回复正文第 {index} 行。" for index in range(20)],
            "正在生成新的项目总结。",
            "当前回复仍在继续。",
        ]
    )
    state = advance_wait_state(
        {},
        ocr_text=old_answer,
        controls=[],
        invocation_marker=MARKER,
    )

    assert not state["permission_required"]
    assert state["status"] != "permission_required"


def test_wait_state_detects_permission_in_bottom_action_area_without_uia():
    text = "\n".join(
            [
                *[f"页面历史内容第 {index} 行。" for index in range(20)],
                "Codex 需要运行命令读取 Git 状态。",
                "需要批准",
            ]
    )
    state = advance_wait_state(
        {},
        ocr_text=text,
        controls=[],
        invocation_marker=MARKER,
    )

    assert state["permission_required"]
    assert state["status"] == "permission_required"


def test_wait_state_does_not_treat_static_ask_for_approval_mode_as_dialog():
    state = advance_wait_state(
        {},
        ocr_text="\n".join(
            [
                MARKER,
                "回答已经完成，可以复制。",
                "随心输入",
                "请求批准",
            ]
        ),
        controls=[],
        invocation_marker=MARKER,
        minimum_reply_chars=20,
    )

    assert not state["permission_required"]
    assert state["status"] != "permission_required"


def test_wait_state_accepts_correlated_copy_control_as_completion_signal():
    state = advance_wait_state(
        {"marker_seen": True, "marker_match_mode": "exact"},
        ocr_text="项目进展回答已经完整生成，包含成果、风险和下一步计划。",
        controls=[{"name": "Copy response"}],
        invocation_marker=MARKER,
        minimum_reply_chars=20,
    )

    assert state["complete"]
    assert state["completion_signals"]["copy_ready"]


def test_wait_state_does_not_treat_answer_discussion_as_page_state():
    text = "\n".join(
        [
            MARKER,
            "本次修复解决了请求批准误判、复制失败和生成失败后的降级问题。",
            "当前风险已经记录，下一步继续执行真实桌面验证。",
            "随心输入",
            "请求批准",
        ]
    )
    state = advance_wait_state(
        {"marker_seen": True, "marker_match_mode": "exact"},
        ocr_text=text,
        controls=[],
        invocation_marker=MARKER,
        minimum_reply_chars=20,
    )

    assert not state["permission_required"]
    assert not state["error_visible"]
    assert not state["copy_visible"]


def test_wait_state_accepts_codex_done_status_after_correlated_reply():
    state = advance_wait_state(
        {"marker_seen": True, "marker_match_mode": "exact"},
        ocr_text="\n".join(
            [
                "完整的项目分析已经生成，包含完成项、风险和下一步计划。",
                "已处理 2m33s >",
                "随心输入",
                "请求批准",
            ]
        ),
        controls=[],
        invocation_marker=MARKER,
        minimum_reply_chars=20,
    )

    assert state["done_visible"]
    assert state["complete"]
    assert state["completion_signals"]["done_status_visible"]


def test_reply_validation_rejects_prompt_echo_wrong_marker_and_truncation():
    prompt = "请读取 Git diff 并给出项目进展、风险和下一步。"
    echo = validate_reply_candidate(
        f"{MARKER}\n{prompt}",
        source="clipboard",
        prompt=prompt,
        invocation_marker=MARKER,
        schema="work_plan",
    )
    assert not echo["ok"]
    assert "prompt_echo" in echo["issues"]

    stale = validate_reply_candidate(
        ANSWER.replace("jcx-current", "jcx-previous"),
        source="clipboard",
        prompt=prompt,
        invocation_marker=MARKER,
        schema="work_plan",
    )
    assert not stale["ok"]
    assert "invocation_marker_mismatch" in stale["issues"]

    truncated = validate_reply_candidate(
        f"{MARKER}\n1. 最新进展：完成 Context Pack。\n2. 证据：Git diff 已记录。\n3. 下一步：",
        source="clipboard",
        prompt=prompt,
        invocation_marker=MARKER,
        schema="work_plan",
    )
    assert not truncated["ok"]
    assert "reply_truncated_suffix" in truncated["issues"]


def test_reply_selection_prefers_correlated_clipboard_and_rejects_conflict():
    selection = select_reply(
        [
            {"source": "qwen_vision", "text": ANSWER},
            {"source": "clipboard", "text": ANSWER},
            {"source": "ocr_fallback", "text": "unrelated screen text"},
        ],
        prompt="请分析工作进展。",
        invocation_marker=MARKER,
        schema="work_plan",
    )
    assert selection["ok"]
    assert selection["source"] == "clipboard"
    assert MARKER not in selection["answer"]

    conflicting_answer = (
        f"{MARKER}\n"
        "1. 最新进展：删除了所有工作账本模块。\n"
        "2. 证据：另一个完全不同的文件发生变化。\n"
        "3. 风险：结果与复制文本冲突。\n"
        "4. 下一步：停止发布并人工检查。"
    )
    conflict = select_reply(
        [
            {"source": "clipboard", "text": ANSWER},
            {"source": "qwen_vision", "text": conflicting_answer},
        ],
        prompt="请分析工作进展。",
        invocation_marker=MARKER,
        schema="work_plan",
    )
    assert not conflict["ok"]
    assert conflict["conflicts"]
    assert conflict["validation"]["issues"] == ["reply_source_conflict"]


def test_reply_selection_never_accepts_unmarked_visual_or_ocr_fallback():
    selection = select_reply(
        [
            {
                "source": "qwen_vision",
                "text": ANSWER.replace(f"{MARKER}\n", ""),
            },
            {
                "source": "ocr_fallback",
                "text": "侧边栏内容和旧回复，不属于当前调用。",
            },
        ],
        prompt="请分析工作进展。",
        invocation_marker=MARKER,
        schema="work_plan",
    )
    assert not selection["ok"]
    issues = {
        issue
        for row in selection["candidates"]
        for issue in row["validation"]["issues"]
    }
    assert "invocation_marker_missing" in issues


def test_reply_selection_never_uses_screen_ocr_as_complete_reply():
    selection = select_reply(
        [
            {
                "source": "ocr_fallback",
                "text": ANSWER,
            },
        ],
        prompt="请分析工作进展。",
        invocation_marker=MARKER,
        schema="work_plan",
    )

    assert not selection["ok"]
    assert selection["source"] == ""
    assert (
        "untrusted_final_reply_source"
        in selection["candidates"][0]["validation"]["issues"]
    )


def test_reply_validation_enforces_explicit_requested_minimum_length():
    validation = validate_reply_candidate(
        ANSWER,
        source="clipboard",
        prompt="请输出不少于 2500 个中文字符的完整报告。",
        invocation_marker=MARKER,
        schema="work_plan",
    )

    assert not validation["ok"]
    assert validation["requested_minimum_length"] == 2500
    assert "requested_minimum_length_not_met" in validation["issues"]
