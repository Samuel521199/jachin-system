"""PMO：Final Answer 谎称已发飞书 的拦截正则（agent_core._pmo_final_answer_falsely_claims_lark_sent）。"""

from l3_node.agent_core import _pmo_final_answer_falsely_claims_lark_sent


def test_claims_lark_via_notifier_phrase_user_hit():
    s = (
        "我已按要求执行了PMO-Copilot SKILL的分支A流程，拉取了§1.1中的全部种子链接并进行了汇总。"
        "相关数据已通过mcp:atom_lark_notifier发送至飞书主群和监控群。"
    )
    assert _pmo_final_answer_falsely_claims_lark_sent(s) is True


def test_claims_lark_spaced_notifier_name():
    s = "汇总完成，已通过 atom_lark_notifier 推送到主群。"
    assert _pmo_final_answer_falsely_claims_lark_sent(s) is True


def test_honest_not_called_excluded():
    s = "拉表成功，但本轮未调用 mcp:atom_lark_notifier，未向飞书推送。"
    assert _pmo_final_answer_falsely_claims_lark_sent(s) is False


def test_honest_send_failed_excluded():
    s = "mcp:atom_lark_notifier 已发送失败，status: error，请检查机器人入群。"
    assert _pmo_final_answer_falsely_claims_lark_sent(s) is False
