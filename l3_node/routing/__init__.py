"""L3 入站路由与领域插件挂载点（register_inbound_plugin / apply_registered_plugins）。"""
from __future__ import annotations

from l3_node.routing.plugins import apply_registered_plugins, register_inbound_plugin

__all__ = ["apply_registered_plugins", "register_inbound_plugin"]
