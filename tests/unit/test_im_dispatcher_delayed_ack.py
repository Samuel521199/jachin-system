"""飞书 IM 延时安抚：寒暄不触发，默认 40s。"""
from __future__ import annotations

from l3_node.im_channels.dispatcher import (
    _im_ack_delay_sec,
    _should_send_delayed_ack,
)


def test_greeting_skips_delayed_ack() -> None:
    assert _should_send_delayed_ack("你好") is False
    assert _should_send_delayed_ack("你好呀") is False


def test_real_task_enables_delayed_ack() -> None:
    assert _should_send_delayed_ack("帮我查一下本周项目进度") is True


def test_hash_star_skips_run_agent_delayed_ack() -> None:
    assert _should_send_delayed_ack("#*#项目进度怎么样") is False


def test_ack_delay_default_at_least_40() -> None:
    assert _im_ack_delay_sec() >= 40.0
