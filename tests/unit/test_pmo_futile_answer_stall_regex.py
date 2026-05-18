"""PMO：禁止「先列目录再读」类 Final Answer — _pmo_final_answer_looks_like_futile_plan_only。"""

from l3_node.agent_core import _pmo_final_answer_looks_like_futile_plan_only


def test_stall_list_dir_after_pmo_pull_user_case():
    s = (
        "我需要先确认pmo_lark_pull目录中的实际文件名，以便正确读取产品任务需求完成度文件。"
        "让我先列出该目录下的所有文件。"
    )
    assert _pmo_final_answer_looks_like_futile_plan_only(s) is True


def test_not_stall_normal_completion():
    # 过长 / 非 stall
    assert (
        _pmo_final_answer_looks_like_futile_plan_only(
            "本轮已按 §1.4 通过 notifier 推送宏观看板，Observation 中 status 为 success。"
        )
        is False
    )


def test_not_stall_unrelated_i_need_first():
    assert _pmo_final_answer_looks_like_futile_plan_only("我需要先吃饭再去开会。") is False
