"""
语义路由器 (Intent Router) — 安全指令协议 Layer 2

将用户实时文本流分类为 CHAT（闲聊）或 COMMAND（系统指令）。
- CHAT：仅 LLM 回复，禁止调用 FileTool / ShellTool。
- COMMAND：带「系统指令」/「Jachin Execute」等前缀，送 Agent 执行器；高风险需二次确认。
"""

import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

IntentType = Literal["CHAT", "COMMAND"]
RiskLevel = Literal["low", "medium", "high"]

# 命令前缀（不区分大小写），用于识别「系统指令」
COMMAND_PREFIXES = (
    "系统指令",
    "系统指令，",
    "系统指令 ",
    "jachin execute",
    "jachin execute,",
    "jachin execute ",
    "execute",
    "execute,",
    "execute ",
)

# 高风险关键词（命中则 risk_level = high，需二次确认）
HIGH_RISK_KEYWORDS = (
    "删除", "删掉", "remove", "delete", "rm ", "drop ",
    "格式化", "format", "格盘",
    "清空", "清空所有", "clear all", "truncate",
    "关机", "重启", "shutdown", "reboot",
    "执行", "运行脚本", "run script", "exec ",
)


@dataclass
class RoutedIntent:
    """路由结果"""
    intent_type: IntentType
    risk_level: RiskLevel
    stripped_text: str  # 去掉前缀后的文本，供执行器或 LLM 使用


class IntentRouter:
    """
    语义路由器：根据安全指令协议对用户输入分类。
    """

    def __init__(
        self,
        command_prefixes: tuple[str, ...] = COMMAND_PREFIXES,
        high_risk_keywords: tuple[str, ...] = HIGH_RISK_KEYWORDS,
    ):
        self.command_prefixes = command_prefixes
        self.high_risk_keywords = high_risk_keywords
        # 编译前缀正则：最长匹配优先
        self._prefix_pattern = re.compile(
            "|".join(re.escape(p) for p in sorted(command_prefixes, key=len, reverse=True)),
            re.IGNORECASE,
        )

    def route(self, text: str) -> RoutedIntent:
        """
        对单条用户文本分类。

        - 若以命令前缀开头 -> COMMAND，并计算 risk_level（命中高危词则 high）。
        - 否则 -> CHAT，risk_level 恒为 low，且不允许调用 FileTool/ShellTool。
        """
        if not text or not text.strip():
            return RoutedIntent(intent_type="CHAT", risk_level="low", stripped_text=text or "")

        raw = text.strip()
        lower = raw.lower()

        # 检测命令前缀（最长匹配）
        prefix_match = self._prefix_pattern.match(raw)
        if prefix_match:
            prefix = prefix_match.group(0)
            stripped = raw[len(prefix):].strip()
            risk = self._risk_level(stripped)
            logger.info("IntentRouter: COMMAND prefix=%r risk=%s", prefix, risk)
            return RoutedIntent(intent_type="COMMAND", risk_level=risk, stripped_text=stripped)

        return RoutedIntent(intent_type="CHAT", risk_level="low", stripped_text=raw)

    def _risk_level(self, text: str) -> RiskLevel:
        """根据内容判断风险等级（仅对 COMMAND 有效）"""
        if not text:
            return "low"
        lower = text.lower()
        for kw in self.high_risk_keywords:
            if kw.lower() in lower or kw in text:
                return "high"
        return "medium"
