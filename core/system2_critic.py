"""
System 2 慢思考 — Critic & Self-Correction

在 BI 战报等核心输出前，通过独立 Critic 角色进行内部审核与自我反思。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CRITIC_PROMPT = """你是一位极其严苛的质量审核员（Critic）。请对以下稿件进行「无情挑刺」。

【审核约束】
{constraints}

【原稿】
{draft_content}

【输出格式】
第一行必须且只能输出：PASS 或 FAIL
- PASS：稿件完全符合约束，无需修改
- FAIL：稿件存在问题，需修改

若为 FAIL，从第二行起列出具体修改建议（每条一行，简洁明确）。例如：
FAIL
1. 字数超限，需压缩至 500 字以内
2. 缺少明确的行动指令（如「请于明日 9 点前完成」）
3. 存在废话、套话，需删除"""

_DEFAULT_REFINER_PROMPT = """请根据 Critic 的修改建议，对原稿进行重写。保留原意与核心内容，严格按建议修正。

【Critic 修改建议】
{suggestions}

【原稿】
{draft_content}

【输出】
请直接输出修订后的完整稿件，不要输出「修订版：」等前缀。"""


def _parse_critic_output(text: str) -> tuple[bool, list[str]]:
    """
    解析 Critic 输出。返回 (passed, suggestions)。
    """
    text = (text or "").strip()
    if not text:
        return False, ["Critic 无输出"]
    first_line = text.split("\n")[0].strip().upper()
    if "PASS" in first_line or "通过" in text[:20]:
        return True, []
    # FAIL
    lines = text.split("\n")[1:]
    suggestions = []
    for line in lines:
        line = line.strip()
        # 去除序号（1. 2. - * 等）
        line = re.sub(r"^[\d\-*\.\)\s]+", "", line).strip()
        if line and len(line) > 3:
            suggestions.append(line)
    return False, suggestions or ["Critic 未给出具体建议"]


class ReflectionEngine:
    """
    System 2 反思引擎：Critic 挑刺 -> 通过则返回 / 否则 Refiner 重写 -> 循环直至通过或达上限。
    """

    def __init__(
        self,
        llm_engine: Any | None = None,
        critic_model: str | None = None,
    ) -> None:
        """
        llm_engine: 可选，需有 async generate_response(messages, ...)。不传则使用 core.llm_provider。
        critic_model: Critic 使用的模型，不传则使用默认。
        """
        self._engine = llm_engine
        self._critic_model = critic_model

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        from core.llm_provider import LiteLLMEngine

        return LiteLLMEngine(model_name=self._critic_model)

    async def _call_llm(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """统一 LLM 调用。"""
        engine = self._get_engine()
        result = await engine.generate_response(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if isinstance(result, dict):
            return (result.get("content") or "").strip()
        return (result or "").strip()

    def _build_constraints_text(self, constraints: dict[str, Any] | list[str] | str) -> str:
        """将 constraints 格式化为字符串。"""
        if isinstance(constraints, str):
            return constraints
        if isinstance(constraints, list):
            return "\n".join(f"- {c}" for c in constraints)
        if isinstance(constraints, dict):
            return "\n".join(f"- {k}: {v}" for k, v in constraints.items())
        return str(constraints)

    async def critique_and_refine(
        self,
        draft_content: str,
        constraints: dict[str, Any] | list[str] | str,
        *,
        max_retries: int = 3,
    ) -> str:
        """
        执行 Critic 审核与自我修正循环。

        Args:
            draft_content: 待审核的初稿
            constraints: 审核约束，如 {"字数": "≤500字", "禁止废话": True} 或 字符串/列表
            max_retries: 最大重写次数，防止死循环

        Returns:
            最终通过的稿件
        """
        constraints_text = self._build_constraints_text(constraints)
        current_draft = (draft_content or "").strip()
        if not current_draft:
            logger.warning("[System2 Critic] draft_content 为空，直接返回")
            return ""

        for attempt in range(max_retries + 1):
            # 步骤 A: Critic 挑刺
            critic_prompt = _DEFAULT_CRITIC_PROMPT.format(
                constraints=constraints_text,
                draft_content=current_draft,
            )
            logger.info("[System2 Critic] 第 %d 轮审核，稿件长度 %d 字符", attempt + 1, len(current_draft))
            critic_output = await self._call_llm(critic_prompt, temperature=0.2, max_tokens=1024)

            passed, suggestions = _parse_critic_output(critic_output)

            if passed:
                logger.info("[System2 Critic] 审核通过，无需重写")
                return current_draft

            # 步骤 C: 根据建议重写
            logger.info(
                "[System2 Critic] 拦截成功，正在根据 Critic 意见重写... 建议数: %d",
                len(suggestions),
            )
            suggestions_text = "\n".join(suggestions) if suggestions else "按约束修正"
            refiner_prompt = _DEFAULT_REFINER_PROMPT.format(
                suggestions=suggestions_text,
                draft_content=current_draft,
            )
            new_draft = await self._call_llm(refiner_prompt, temperature=0.4, max_tokens=4096)
            if not new_draft:
                logger.warning("[System2 Critic] Refiner 返回为空，保留原稿")
                break
            current_draft = new_draft
            if attempt >= max_retries - 1:
                logger.warning(
                    "[System2 Critic] 已达 max_retries=%d 上限，返回最后一版",
                    max_retries,
                )
                break

        return current_draft
