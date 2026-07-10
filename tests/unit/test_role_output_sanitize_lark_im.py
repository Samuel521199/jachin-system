from l3_node.role_output_sanitize import sanitize_final_answer_for_lark_im, sanitize_user_visible_answer


def test_lark_im_strips_markdown_bold_markers():
    raw = (
        "基于本周分析，**Patrick** 的任务安排显得最不合理。\n"
        "1. **任务数量严重超载**：Patrick 一人承担了 **8 条** 任务。"
    )
    out = sanitize_final_answer_for_lark_im(raw)
    assert "**" not in out
    assert "Patrick" in out
    assert "8 条" in out
    assert "任务数量严重超载" in out


def test_user_visible_answer_strips_monitor_group_all_channels() -> None:
    raw = "已推送至主群与监控群（oc_0e321f92d758ecb44aea5b499c90510b）。"
    out = sanitize_user_visible_answer(raw)
    assert "监控群" not in out
    assert "oc_" not in out


def test_lark_im_still_strips_role_execution_scaffolding():
    raw = "Reasoning note: 分析中\nUser-facing result: **结论** 如下"
    out = sanitize_final_answer_for_lark_im(raw)
    assert "Reasoning note:" not in out
    assert "User-facing result:" not in out
    assert "**" not in out
    assert "结论" in out
