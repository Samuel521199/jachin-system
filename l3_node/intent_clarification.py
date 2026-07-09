"""
L3 通用「模糊意图 → 反问确认」框架。

在精确遥控 / 工具路由未命中时，由**业务域插件**注册 ``ClarificationRule``，按优先级检测；
命中则返回固定反问文案（通常不调 LLM），引导用户发送**明确短指令**。

设计入口：``docs/07_memory_first_main_agent_and_voice_app_agents.md``。
Cursor 规则：``.cursor/rules/085-l3-fuzzy-intent-clarification.mdc``
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

DEFAULT_MAX_TEXT_LEN = 100
DEFAULT_COOLDOWN_SEC = 12.0

# 冷却重复时通用提示（各域可在规则文档中列出推荐短指令）
DEFAULT_COOLDOWN_REPEAT_REPLY = (
    "💬 与上一条模糊确认过于接近。若需执行，请直接发送**明确短指令**；"
    "招聘场景常用：**分析简历**、**继续**、**停止**、**进度**。"
)


@dataclass(frozen=True)
class ClarificationRule:
    """
    单条模糊澄清规则。

    - ``rule_id``：日志与冷却键片段，宜为 ``域:意图``，如 ``hr_lark:analyze``。
    - ``priority``：越小越先评估；同一文本只命中**第一条**通过的规则。
    - ``test``：对整句用户文本返回 True 则应反问（插件内自行排除「已精确命中」的情况）。
    - ``reply``：直接回给用户的 Markdown/纯文本。
    """

    rule_id: str
    priority: int
    test: Callable[[str], bool]
    reply: str


_clarify_lock = threading.Lock()
_clarify_last_key: str | None = None
_clarify_last_mono: float = 0.0


def _cooldown_gate(channel_id: str, rule_id: str, text_cf: str, cooldown_sec: float, body: str) -> str:
    global _clarify_last_key, _clarify_last_mono
    key = f"{channel_id or 'global'}|{rule_id}|{text_cf}"
    now = time.monotonic()
    with _clarify_lock:
        if (
            key == (_clarify_last_key or "")
            and (now - _clarify_last_mono) < cooldown_sec
        ):
            return DEFAULT_COOLDOWN_REPEAT_REPLY
        _clarify_last_key = key
        _clarify_last_mono = now
    return body


def try_fuzzy_clarification(
    text: str,
    rules: Sequence[ClarificationRule],
    *,
    channel_id: str = "",
    max_text_len: int = DEFAULT_MAX_TEXT_LEN,
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
) -> str | None:
    """
    按优先级尝试规则；命中则返回反问文案（含冷却去重），否则 None。
    """
    t = (text or "").strip()
    if not t or len(t) > max_text_len:
        return None
    ordered = sorted(rules, key=lambda r: r.priority)
    for rule in ordered:
        try:
            if not rule.test(t):
                continue
        except Exception as e:
            logger.debug("[IntentClarify] rule %s test 异常: %s", rule.rule_id, e)
            continue
        out = _cooldown_gate(channel_id, rule.rule_id, t.casefold(), cooldown_sec, rule.reply)
        logger.info(
            "[IntentClarify] hit rule=%s channel=%s text=%r",
            rule.rule_id,
            (channel_id or "")[:24],
            t[:80],
        )
        return out
    return None


_default_rules_cache: list[ClarificationRule] | None = None


def default_l3_clarification_rules() -> list[ClarificationRule]:
    """默认安装的 L3 澄清规则集（当前含 HR·Lark；其他域在此汇总）。"""
    global _default_rules_cache
    if _default_rules_cache is None:
        from l3_node.intent_clarification_plugins.hr_recruitment_lark import hr_recruitment_lark_clarification_rules

        _default_rules_cache = list(hr_recruitment_lark_clarification_rules())
    return _default_rules_cache


def try_default_l3_fuzzy_clarification(
    text: str,
    *,
    channel_id: str = "",
    max_text_len: int = DEFAULT_MAX_TEXT_LEN,
    cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
) -> str | None:
    """使用 ``default_l3_clarification_rules()`` 的便捷入口。"""
    return try_fuzzy_clarification(
        text,
        default_l3_clarification_rules(),
        channel_id=channel_id,
        max_text_len=max_text_len,
        cooldown_sec=cooldown_sec,
    )
