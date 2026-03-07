"""
Jachin Nexus v8.0 - 虫群大脑 (Cognitive Swarm via LiteLLM) + 流式神经

LiteLLM 万能模型库：model_name 透传，彻底清除 API 差异冗余。
支持 gpt-4o, qwen/qwen-max, ollama/qwen2.5 等任意 LiteLLM 兼容模型。
API Key 优先级：环境变量 > .env > ~/.jachin/nexus_config.json llm_keys
无 Key 时自动回退到 ollama/qwen2.5 本地模型。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

# 尽早加载 .env，确保 OPENAI_API_KEY 等被注入（IDE/子进程可能未继承系统环境变量）
# Windows 下 echo > .env 或记事本可能产生 UTF-16，需尝试多种编码；加载成功后转为 UTF-8 供 pydantic-settings 使用
def _load_dotenv_safe() -> None:
    """加载项目根目录 .env，确保 Layer2 daemon 无论从何目录启动都能读到 DASHSCOPE_API_KEY 等"""
    try:
        from dotenv import load_dotenv
        # 优先从 core 的父目录（项目根）加载，再尝试 cwd
        for _p in [Path(__file__).resolve().parent.parent, Path.cwd()]:
            _e = _p / ".env"
            if _e.exists():
                try:
                    load_dotenv(_e, encoding="utf-8")
                except UnicodeDecodeError:
                    load_dotenv(_e, encoding="utf-16")
                    try:
                        _e.write_text(_e.read_text(encoding="utf-16"), encoding="utf-8")
                    except Exception:
                        pass
                break
    except ImportError:
        pass

_load_dotenv_safe()

import litellm
from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"
_OLLAMA_FALLBACK = "ollama/qwen2.5"

_IGNITION_EMITTED = False


def _load_nexus_config() -> dict[str, Any]:
    """读取 ~/.jachin/nexus_config.json，兼容 UTF-8 / UTF-16（Windows 可能产生 UTF-16）"""
    if not _NEXUS_CONFIG.exists():
        return {}
    try:
        return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        try:
            return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-16"))
        except Exception:
            return {}
    except Exception:
        return {}


def _get_model_name(config: dict[str, Any] | None = None) -> str:
    """
    从配置读取 model_name，透传给 LiteLLM。
    示例：gpt-4o, qwen/qwen-max, ollama/qwen2.5
    当 QWEN_USE_OPENAI_KEY=1 或 llm.use_openai_key_for_qwen 时，默认使用 qwen/qwen-max。
    """
    cfg = config or _load_nexus_config()
    llm_cfg = cfg.get("llm") or {}
    if isinstance(llm_cfg, dict):
        name = (
            llm_cfg.get("model_name")
            or llm_cfg.get("cloud_model")
            or llm_cfg.get("edge_model")
            or cfg.get("model_name")
        )
        if name and str(name).strip():
            return str(name).strip()
    # 当 OPENAI_API_KEY 用于 Qwen 时，默认使用 qwen 模型
    use_for_qwen = os.environ.get("QWEN_USE_OPENAI_KEY", "").lower() in ("1", "true", "yes")
    if not use_for_qwen and isinstance(llm_cfg, dict):
        use_for_qwen = llm_cfg.get("use_openai_key_for_qwen", False)
    if use_for_qwen:
        return "dashscope/qwen3.5-flash"  # LiteLLM 要求 dashscope/ 前缀
    # 若已配置 DASHSCOPE/QWEN API Key，默认用通义千问，避免回退到未启动的 Ollama
    if os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY"):
        return "dashscope/qwen3.5-flash"
    try:
        from core.config import settings
        if getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(settings, "QWEN_API_KEY", None):
            return "dashscope/qwen3.5-flash"
    except ImportError:
        pass
    # 兜底：检查 nexus_config / credential_loader（可能尚未注入到 os.environ）
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        if get_dashscope_key():
            return "dashscope/qwen3.5-flash"
    except ImportError:
        pass
    llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
    if llm_keys.get("dashscope"):
        return "dashscope/qwen3.5-flash"
    return "gpt-4o-mini"  # LiteLLM 默认兜底


def _get_openai_key_from_sources() -> str | None:
    """多源读取 OpenAI Key：env > credential_loader > nexus_config 直接读取"""
    val = os.environ.get("OPENAI_API_KEY")
    if val and str(val).strip():
        return str(val).strip()
    try:
        from core.brain.llm.credential_loader import get_openai_key
        val = get_openai_key()
        if val:
            return val
    except ImportError:
        pass
    cfg = _load_nexus_config()
    llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
    val = llm_keys.get("openai")
    if val and str(val).strip():
        return str(val).strip()
    return None


def _has_dashscope_key() -> bool:
    """检测是否已配置通义千问 API Key（env / credential_loader / nexus_config）"""
    if os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY"):
        return True
    try:
        from core.config import settings
        if getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(settings, "QWEN_API_KEY", None):
            return True
    except ImportError:
        pass
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        if get_dashscope_key():
            return True
    except ImportError:
        pass
    cfg = _load_nexus_config()
    llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
    return bool(llm_keys.get("dashscope"))


def _inject_api_keys() -> None:
    """从多源注入 API Key 到环境变量，供 LiteLLM 读取"""
    val = _get_openai_key_from_sources()
    if val and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = val
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        val = get_dashscope_key()
        if val and not os.environ.get("DASHSCOPE_API_KEY"):
            os.environ["DASHSCOPE_API_KEY"] = str(val).strip()
    except ImportError:
        cfg = _load_nexus_config()
        llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
        val = llm_keys.get("dashscope")
        if val and not os.environ.get("DASHSCOPE_API_KEY"):
            os.environ["DASHSCOPE_API_KEY"] = str(val).strip()
    # 当 OPENAI_API_KEY 实际存的是阿里云 Qwen 密钥时：注入到 DASHSCOPE，供 qwen/* 模型使用
    if not os.environ.get("DASHSCOPE_API_KEY") and os.environ.get("OPENAI_API_KEY"):
        use_for_qwen = os.environ.get("QWEN_USE_OPENAI_KEY", "").lower() in ("1", "true", "yes")
        if not use_for_qwen:
            cfg = _load_nexus_config()
            llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
            use_for_qwen = llm.get("use_openai_key_for_qwen", False)
        if use_for_qwen:
            os.environ["DASHSCOPE_API_KEY"] = os.environ["OPENAI_API_KEY"]


def _model_needs_key(model: str) -> tuple[bool, str | None]:
    """判断模型是否需要 API Key，返回 (需要, 环境变量名)"""
    m = (model or "").lower()
    if m.startswith("ollama/") or m.startswith("ollama:"):
        return False, None
    if m.startswith("openai/") or m.startswith("gpt-"):
        return True, "OPENAI_API_KEY"
    if m.startswith("qwen/") or m.startswith("dashscope/") or m.startswith("qwen-"):
        return True, "DASHSCOPE_API_KEY"
    # 默认按 OpenAI 处理（gpt-4o-mini 等）
    return True, "OPENAI_API_KEY"


def _normalize_model_for_litellm(model: str) -> str:
    """将裸模型名转为 LiteLLM 所需格式，如 qwen-max -> dashscope/qwen-max"""
    m = (model or "").strip()
    if not m:
        return m
    ml = m.lower()
    if ml.startswith("ollama/") or ml.startswith("ollama:"):
        return m
    if ml.startswith("qwen") and not ml.startswith("qwen/") and not ml.startswith("dashscope/"):
        return f"dashscope/{m}"
    return m


def _get_retry_config(config: dict[str, Any] | None = None) -> tuple[int, list[str], float]:
    """
    读取重试与降级配置。
    返回 (max_attempts, fallback_models, timeout_seconds)
    有 DASHSCOPE_API_KEY 时默认用 dashscope 兜底，避免回退到未启动的 Ollama。
    """
    cfg = config or _load_nexus_config()
    llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
    max_attempts = int(llm.get("max_attempts", 2))
    fallback = llm.get("fallback_models")
    if isinstance(fallback, list):
        fallback_models = [str(m).strip() for m in fallback if m]
    elif isinstance(fallback, str) and fallback.strip():
        fallback_models = [fallback.strip()]
    else:
        env_fallback = os.environ.get("LITELLM_FALLBACK_MODELS", "").strip()
        if env_fallback:
            fallback_models = [m.strip() for m in env_fallback.split(",") if m.strip()]
        elif _has_dashscope_key():
            fallback_models = ["dashscope/qwen3.5-flash"]
        else:
            fallback_models = [_OLLAMA_FALLBACK]
    timeout = float(llm.get("timeout_seconds", 60.0))
    return max_attempts, fallback_models, timeout


def _resolve_model_with_fallback(model: str) -> str:
    """若云模型无 Key，回退到 ollama/qwen2.5。使用 _get_openai_key_from_sources 多源检测"""
    needs, env_key = _model_needs_key(model)
    if not needs:
        return model
    if env_key == "OPENAI_API_KEY":
        has_key = bool(_get_openai_key_from_sources())
    else:
        has_key = bool(os.environ.get(env_key))
        if not has_key:
            try:
                from core.brain.llm.credential_loader import get_dashscope_key
                has_key = bool(get_dashscope_key())
            except ImportError:
                cfg = _load_nexus_config()
                has_key = bool((cfg.get("llm_keys") or {}).get("dashscope"))
    if has_key:
        return model
    logger.info(
        "[LiteLLM] %s 未配置，回退到本地模型 %s。"
        "可设置环境变量或 ~/.jachin/nexus_config.json → llm_keys",
        env_key or "API Key",
        _OLLAMA_FALLBACK,
    )
    return _OLLAMA_FALLBACK


class LiteLLMEngine:
    """
    虫群大脑引擎 — 通过 litellm.acompletion 统一接入任意模型。
    不再维护 Qwen/OpenAI/Ollama 分支，model_name 直接透传。
    API Key 从环境变量或 nexus_config.json 注入；无 Key 时回退 ollama/qwen2.5。
    """

    def __init__(self, model_name: str | None = None) -> None:
        _inject_api_keys()
        raw = model_name or _get_model_name()
        resolved = _resolve_model_with_fallback(raw)
        self.model_name = _normalize_model_for_litellm(resolved)

    async def generate_response(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str | dict[str, Any]:
        """
        调用 litellm.acompletion，返回纯文本或 tool_calls 字典。
        v8.0 神盾：for attempt 重试 + fallback_models 降级灾备。
        """
        _inject_api_keys()
        max_attempts, fallback_models, timeout = _get_retry_config()
        models_to_try = [self.model_name] + [m for m in fallback_models if m != self.model_name]
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            model = _normalize_model_for_litellm(models_to_try[min(attempt, len(models_to_try) - 1)])
            try:
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }
                if tools:
                    kwargs_chat["tools"] = tools

                response = await litellm.acompletion(**kwargs_chat)
                choice = response.choices[0] if response.choices else None
                if not choice:
                    return ""

                msg = choice.message
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    return {"content": msg.content or "", "tool_calls": msg.tool_calls}
                return (msg.content or "").strip()
            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1 and len(models_to_try) > 1:
                    next_model = models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                    console.print(
                        f"[yellow][⚠ 降级策略][/yellow] 主模型异常，尝试第 {attempt + 2} 次呼叫备用算力: [cyan]{next_model}[/cyan]"
                    )
                    logger.warning("[LiteLLM] attempt=%s model=%s 失败: %s，降级至 %s", attempt + 1, model, e, next_model)
                else:
                    logger.exception("[LiteLLM] 调用异常 model=%s: %s", model, e)
                    raise last_error
        raise last_error or RuntimeError("LLM 调用失败")

    async def generate_response_stream(
        self,
        messages: list[dict[str, str]],
        chunk_callback: Callable[[str], Awaitable[None]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """
        v8.0 流式神经：stream=True 调用 litellm.acompletion，逐 token 回调并返回完整响应。
        v8.0 神盾：for attempt 重试 + fallback_models 降级灾备。
        """
        _inject_api_keys()
        max_attempts, fallback_models, timeout = _get_retry_config()
        models_to_try = [self.model_name] + [m for m in fallback_models if m != self.model_name]
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            model = _normalize_model_for_litellm(models_to_try[min(attempt, len(models_to_try) - 1)])
            full_content: list[str] = []
            try:
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }

                response = await litellm.acompletion(**kwargs_chat)
                async for chunk in response:
                    choice = chunk.choices[0] if chunk.choices else None
                    if not choice or not hasattr(choice, "delta"):
                        continue
                    delta = getattr(choice.delta, "content", None) or ""
                    if delta:
                        full_content.append(delta)
                        if chunk_callback:
                            await chunk_callback(delta)
                return "".join(full_content).strip()
            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1 and len(models_to_try) > 1:
                    next_model = models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                    console.print(
                        f"[yellow][⚠ 降级策略][/yellow] 主模型异常，尝试第 {attempt + 2} 次呼叫备用算力: [cyan]{next_model}[/cyan]"
                    )
                    logger.warning("[LiteLLM] 流式 attempt=%s model=%s 失败: %s，降级至 %s", attempt + 1, model, e, next_model)
                else:
                    logger.exception("[LiteLLM] 流式调用异常 model=%s: %s", model, e)
                    raise last_error
        raise last_error or RuntimeError("LLM 流式调用失败")


class CognitiveEngineFactory:
    """
    认知引擎工厂 — 仅读取 model_name，透传 LiteLLM。
    """

    _engine: LiteLLMEngine | None = None

    @classmethod
    def get_engine(cls, config: dict[str, Any] | None = None) -> LiteLLMEngine:
        """
        获取 LiteLLM 引擎实例。
        config.llm.model_name: 如 gpt-4o, qwen/qwen-max, ollama/qwen2.5
        """
        global _IGNITION_EMITTED
        _inject_api_keys()  # 先注入 key，确保 _get_model_name 能检测到 nexus_config/credential_loader 中的 DASHSCOPE_API_KEY
        model_name = _get_model_name(config)
        engine = LiteLLMEngine(model_name=model_name)
        cls._engine = engine

        if not _IGNITION_EMITTED:
            console.print(
                f"[bold green][Ignition][/bold green] Cognitive Swarm Online. Model: [cyan]{engine.model_name}[/cyan]"
            )
            _IGNITION_EMITTED = True
        return engine
