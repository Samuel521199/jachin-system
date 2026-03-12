"""LLM 客户端 - 供多 Agent 评审调用

支持阿里百炼（DashScope）与 Gemini：
- 阿里百炼：DASHSCOPE_API_KEY，按 Agent 分配不同千问模型
- Gemini：GEMINI_API_KEY，单模型回退
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# 三大评审官模型（qwen3.5 系列），可通过 .env 覆盖
# 专家 A: 1220 亿参数，逻辑纵深，技术拆解
# 专家 B: 闭源主力，长文本语义、情商均衡
# 专家 C: 近 4000 亿参数，指令遵循、纯净 JSON
TECH_LEAD_MODEL = "qwen3.5-122b-a10b"
HR_BP_MODEL = "qwen3.5-plus"
JUDGE_MODEL = "qwen3.5-397b-a17b"

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _load_dotenv():
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        load_dotenv(env_path)
    except ImportError:
        pass


def _get_dashscope_key() -> Optional[str]:
    _load_dotenv()
    return os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIBABA_API_KEY")


def _get_gemini_key() -> Optional[str]:
    _load_dotenv()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


async def invoke_llm_with_model(prompt: str, system: str, model: str) -> str:
    """
    按指定模型调用 LLM。
    优先使用阿里百炼（DASHSCOPE_API_KEY），否则回退到 Gemini。
    """
    # 阿里百炼
    api_key = _get_dashscope_key()
    if api_key:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=DASHSCOPE_BASE_URL,
            )
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"DashScope 调用失败 ({model}): {e}，尝试 Gemini 回退")

    # Gemini 回退
    gemini_key = _get_gemini_key()
    if gemini_key:
        try:
            _load_dotenv()
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemini_key)
            cfg = types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.3,
            )
            response = await client.aio.models.generate_content(
                model=os.environ.get("HR_PLUGIN_LLM_MODEL", "gemini-2.0-flash"),
                contents=prompt,
                config=cfg,
            )
            if response and getattr(response, "text", None):
                return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini 调用失败: {e}")

    return _fallback_llm(prompt, system)


async def invoke_llm(prompt: str, system: str) -> str:
    """
    调用 LLM（默认模型）。
    若配置了 DASHSCOPE_API_KEY，使用 qwen-plus；否则用 Gemini。
    """
    if _get_dashscope_key():
        return await invoke_llm_with_model(prompt, system, HR_BP_MODEL)
    return await invoke_llm_with_model(
        prompt, system,
        os.environ.get("HR_PLUGIN_LLM_MODEL", "gemini-2.0-flash"),
    )


def _fallback_llm(prompt: str, system: str) -> str:
    """无 LLM 时的简单规则回退"""
    if "Judge" in system or "裁决" in system or "法官" in system:
        return json.dumps({
            "verdict": "Pass",
            "brief": "[规则模式] 建议人工复核",
            "summary": "请在 .env 中添加 DASHSCOPE_API_KEY 或 GEMINI_API_KEY 启用多 Agent 辩论",
        })
    return json.dumps({
        "verdict": "Pass",
        "reason": "[规则模式] 请在 .env 中添加 DASHSCOPE_API_KEY 启用多 Agent 辩论",
    })
