"""im_channels.should_handle_chat — 默认节点 vs 白名单"""
from l3_node.im_channels.base import InboundIMChannel


class _Stub(InboundIMChannel):
    id = "stub"

    def start(self, config, on_message):
        pass


def test_default_node_handles_all_even_with_explicit_bindings() -> None:
    ch = _Stub()
    cfg = {"chat_ids": ["oc_abc123"], "exclusive_sessions": False}
    assert ch.should_handle_chat(cfg, "oc_abc123") is True
    assert ch.should_handle_chat(cfg, "oc_other999") is True


def test_exclusive_whitelist_only_listed() -> None:
    ch = _Stub()
    cfg = {"chat_ids": ["oc_abc123"], "exclusive_sessions": True}
    assert ch.should_handle_chat(cfg, "oc_abc123") is True
    assert ch.should_handle_chat(cfg, "oc_other999") is False


def test_empty_chat_ids_always_handle() -> None:
    ch = _Stub()
    assert ch.should_handle_chat({"chat_ids": [], "exclusive_sessions": True}, "oc_x") is True
