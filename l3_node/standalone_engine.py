"""
独立 LiteLLM 引擎工厂（不启动 WebSocket / 不占 L3 单实例锁）。

供 PMO Copilot、BI 脚本、deferred_task 等一次性任务使用；
避免 ``from l3_node.__main__ import ...`` 触发 ``__main__`` 模块级日志初始化。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def create_engine_standalone():
    """仅用环境变量创建引擎，不连接 L2。有 DASHSCOPE 时默认 qwen3.5-plus，降级用 flash。"""
    try:
        logger.debug("create_engine_standalone: importing LiteLLMEngine...")
        from l3_node.llm_client import LiteLLMEngine, SecurityContext

        ctx = SecurityContext()
        if os.environ.get("OPENAI_API_KEY"):
            ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
        if os.environ.get("DASHSCOPE_API_KEY"):
            ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
        fallback = None
        default_model = "gpt-4o-mini"
        if ctx.get_key("dashscope"):
            try:
                from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

                fallback = [DASHSCOPE_ECON_FALLBACK_MODEL]
            except ImportError:
                fallback = ["dashscope/qwen3.5-flash"]
            default_model = os.environ.get("LLM_MODEL", "qwen3.5-plus")
        _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
        engine = LiteLLMEngine(
            security_context=ctx,
            model_name=os.environ.get("L3_MODEL", default_model),
            fallback_models=fallback,
            timeout=_timeout,
            max_attempts=2,
        )
        logger.debug("create_engine_standalone: register_host_services...")
        from core.wasm_runner import register_host_services
        from l3_node.l2_url_util import normalize_l2_base_url

        register_host_services(
            llm_engine=engine, l2_base_url=normalize_l2_base_url(os.environ.get("L2_BASE_URL"))
        )
        logger.debug("create_engine_standalone: done")
        return engine
    except Exception as e:
        logger.exception("create_engine_standalone FAILED: %s", e)
        raise


# 历史别名（``l3_node.__main__._create_engine_standalone``）
_create_engine_standalone = create_engine_standalone
