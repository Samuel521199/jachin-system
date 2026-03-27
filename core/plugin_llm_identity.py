"""
Skill / MCP 侧 LLM 身份：统一使用 L3（或进程级）已注入的百炼 Key 与主模型策略。

禁止依赖插件目录 .env 覆盖 DASHSCOPE_API_KEY / LLM_MODEL；密钥优先来自：
1) 进程环境（L3 启动时已加载的根 .env / L2 注入）
2) L3 运行时的 agent_ref.engine → SecurityContext（内存 Key）
3) core 凭据链（nexus_config / credential_loader）

模型名：与 core.llm_provider 推理主模型、经济型降级模型一致；百炼 OpenAI 兼容接口使用裸模型名（无 dashscope/ 前缀）。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_GEMINI_FALLBACK_MODEL = "gemini-2.0-flash"


def _litellm_id_to_openai_compat_model(litellm_id: str) -> str:
    s = (litellm_id or "").strip()
    if s.lower().startswith("dashscope/"):
        return s.split("/", 1)[1]
    return s


def ensure_plugin_dashscope_key_in_env() -> Optional[str]:
    """
    确保 os.environ 中有 DASHSCOPE_API_KEY（若本进程已有则直接返回）。
    返回明文 Key 或 None（调用方禁止记录日志）。
    """
    k = (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip()
    if k:
        os.environ.setdefault("DASHSCOPE_API_KEY", k)
        return os.environ.get("DASHSCOPE_API_KEY", k)
    try:
        from l3_node.agent_ref import engine_ref

        eng = engine_ref.get("engine")
        ctx = getattr(eng, "ctx", None) if eng is not None else None
        if ctx is not None:
            mem = ctx.get_key("dashscope")
            if mem and str(mem).strip():
                mem = str(mem).strip()
                os.environ["DASHSCOPE_API_KEY"] = mem
                return mem
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[plugin_llm_identity] L3 engine Key 读取跳过: %s", e)

    try:
        from core.brain.llm.credential_loader import get_dashscope_key

        ck = get_dashscope_key()
        if ck and str(ck).strip():
            ck = str(ck).strip()
            os.environ.setdefault("DASHSCOPE_API_KEY", ck)
            return ck
    except Exception as e:
        logger.debug("[plugin_llm_identity] credential_loader 跳过: %s", e)

    return None


def plugin_reasoning_model_openai_compat() -> str:
    """与 L2/L3 主推理模型一致（OpenAI 兼容 URL 用裸名）。"""
    from core.llm_provider import _get_model_name, _normalize_model_for_litellm, _resolve_model_with_fallback

    raw = _get_model_name()
    resolved = _resolve_model_with_fallback(raw)
    litellm_id = _normalize_model_for_litellm(resolved)
    return _litellm_id_to_openai_compat_model(litellm_id)


def plugin_brain_filter_model_openai_compat() -> str:
    """小脑粗筛：使用全局定义的经济型模型（仍为上层策略，非插件 .env）。"""
    from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

    return _litellm_id_to_openai_compat_model(DASHSCOPE_ECON_FALLBACK_MODEL)


def plugin_gemini_fallback_model() -> str:
    """无百炼 Key 时 Gemini 回退模型（固定值，不由插件环境变量覆盖）。"""
    return _GEMINI_FALLBACK_MODEL


def get_plugin_gemini_key() -> Optional[str]:
    """仅进程环境 / 凭据链，不加载插件目录 .env。"""
    k = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if k:
        return k
    return None
