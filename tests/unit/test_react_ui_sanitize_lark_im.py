from l3_node.react_ui_sanitize import sanitize_final_answer_for_lark_im


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


def test_lark_im_still_strips_react_scaffolding():
    raw = "Thought: 分析中\nFinal Answer: **结论** 如下"
    out = sanitize_final_answer_for_lark_im(raw)
    assert "Thought:" not in out
    assert "Final Answer:" not in out
    assert "**" not in out
    assert "结论" in out
