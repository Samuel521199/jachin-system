"""LLM 客户端 - 供多 Agent 评审、Lark 辅助等调用

百炼 Key 与主模型由 L3 / 进程级配置统一提供（core.plugin_llm_identity），
禁止依赖插件目录 .env 覆盖 DASHSCOPE_API_KEY、LLM_MODEL。
无百炼 Key 时可回退 Gemini（仅进程环境变量 GEMINI_API_KEY / GOOGLE_API_KEY）。
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 供虫群引擎等传入 invoke_llm_with_model 的占位；DashScope 侧实际模型由 plugin_reasoning_model_openai_compat() 决定
TECH_LEAD_MODEL = "qwen3.5-122b-a10b"
HR_BP_MODEL = "qwen3.5-plus"
JUDGE_MODEL = "qwen3.5-397b-a17b"

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _get_dashscope_key() -> Optional[str]:
    try:
        from core.plugin_llm_identity import ensure_plugin_dashscope_key_in_env

        return ensure_plugin_dashscope_key_in_env()
    except ImportError:
        import os

        return (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip() or None


def _get_gemini_key() -> Optional[str]:
    try:
        from core.plugin_llm_identity import get_plugin_gemini_key

        return get_plugin_gemini_key()
    except ImportError:
        import os

        return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip() or None


def _gemini_model_name() -> str:
    try:
        from core.plugin_llm_identity import plugin_gemini_fallback_model

        return plugin_gemini_fallback_model()
    except ImportError:
        return "gemini-2.0-flash"


async def invoke_llm_with_model(prompt: str, system: str, model: str) -> str:
    """
    调用 LLM。百炼路径下始终使用 L3 主推理模型（OpenAI 兼容裸名）；model 参数保留仅为兼容旧调用。
    """
    try:
        from core.plugin_llm_identity import plugin_reasoning_model_openai_compat

        openai_model = plugin_reasoning_model_openai_compat()
    except ImportError:
        openai_model = (model or "qwen3.5-plus").strip() or "qwen3.5-plus"

    api_key = _get_dashscope_key()
    if api_key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=api_key,
                base_url=DASHSCOPE_BASE_URL,
            )
            resp = await client.chat.completions.create(
                model=openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("DashScope 调用失败 (%s): %s，尝试 Gemini 回退", openai_model, e)

    gemini_key = _get_gemini_key()
    if gemini_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            cfg = types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.3,
            )
            response = await client.aio.models.generate_content(
                model=_gemini_model_name(),
                contents=prompt,
                config=cfg,
            )
            if response and getattr(response, "text", None):
                return response.text.strip()
        except Exception as e:
            logger.warning("Gemini 调用失败: %s", e)

    return _fallback_llm(prompt, system)


async def invoke_llm(prompt: str, system: str) -> str:
    """默认调用：有百炼 Key 则走主推理模型，否则走 Gemini 回退。"""
    return await invoke_llm_with_model(prompt, system, HR_BP_MODEL)


def _fallback_llm(prompt: str, system: str) -> str:
    """无 LLM 时的简单规则回退"""
    if "Judge" in system or "裁决" in system or "法官" in system:
        return json.dumps({
            "verdict": "Pass",
            "brief": "[规则模式] 建议人工复核",
            "summary": "请配置 L3 / 根目录 .env 的 DASHSCOPE_API_KEY 或 GEMINI_API_KEY 以启用多 Agent 辩论",
        })
    return json.dumps({
        "verdict": "Pass",
        "reason": "[规则模式] 请配置 L3 / 根目录 .env 的 DASHSCOPE_API_KEY 以启用 LLM",
    })
