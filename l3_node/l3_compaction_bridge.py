"""
将 core 神盾 Compaction（含阶段 A 锚点 / 审计）注册到 L3 的 global_hooks。

L3 与 core 使用独立的 HookRegistry，故需显式桥接。
"""
from __future__ import annotations

from core.compaction_hook import compaction_before_llm_think
from l3_node.engine.hooks_pipeline import HOOK_BEFORE_LLM_THINK, global_hooks


_registered = False


def register_l3_compaction_hook() -> None:
    global _registered
    if _registered:
        return
    global_hooks.register(HOOK_BEFORE_LLM_THINK, compaction_before_llm_think)
    _registered = True


register_l3_compaction_hook()
