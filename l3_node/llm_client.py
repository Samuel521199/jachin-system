"""
Jachin Nexus V2 - L3 本地解密与直连 LLM

内存级解密：从 L2 拉取密文 Key，用 L3 私钥解密，仅存于 SecurityContext。
严禁明文落盘或打入日志。
无 Key 时自动向 L2 请求（需已配对）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


def _dashscope_econ_fallback() -> str:
    """与 core 一致：主模型失败时的低成本 DashScope 降级。"""
    try:
        from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

        return DASHSCOPE_ECON_FALLBACK_MODEL
    except ImportError:
        return "dashscope/qwen3.5-flash-2026-02-23"


_L3_DEFAULT_REASONING_MODEL = "qwen3.5-plus"


def _brief_llm_context(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
) -> str:
    """供调度日志使用：消息规模、角色计数、最后一条 user 预览、工具数。"""
    n = len(messages)
    roles: dict[str, int] = {}
    for m in messages:
        r = (m.get("role") or "?").strip()
        roles[r] = roles.get(r, 0) + 1
    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "").strip() == "user":
            c = m.get("content") or ""
            if not isinstance(c, str):
                c = str(c)
            last_user = c[:120].replace("\n", " ")
            break
    tc = len(tools) if tools else 0
    return f"msgs={n} roles={roles} tools={tc} last_user_preview={last_user!r}"


def _pop_l3_call_purpose(kwargs: dict[str, Any], default: str = "unspecified") -> str:
    p = kwargs.pop("l3_call_purpose", None)
    if p is None:
        p = kwargs.pop("call_purpose", None)
    s = str(p or "").strip()
    return s if s else default

import os as _os
_JACHIN_DIR = Path(_os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
_GATEWAY_CONFIG = _JACHIN_DIR / "l2_gateway_config.json"
_IDENTITY_PATH = _JACHIN_DIR / "l3_identity.json"

# Provider -> env var mapping for LiteLLM
_PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY",
    "gpt-4": "OPENAI_API_KEY",
    "gpt-4o": "OPENAI_API_KEY",
    "gpt-4o-mini": "OPENAI_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}


class SecurityContext:
    """
    L3 安全上下文：内存中持有解密后的 API Key。
    严禁写入文件或日志。
    """

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}  # provider -> plaintext key (memory only)
        self._locked = False

    def set_key(self, provider: str, plain_key: str) -> None:
        """设置明文 Key（仅内存）。"""
        if provider and plain_key:
            self._keys[provider] = plain_key

    def get_key(self, provider: str) -> Optional[str]:
        """获取明文 Key。provider: openai | dashscope"""
        key = self._keys.get(provider)
        if key:
            return key
        if provider == "dashscope":
            return self._keys.get("qwen")
        return None

    def has_any_key(self) -> bool:
        """是否已有任意 API Key"""
        return bool(self._keys)

    def inject_for_litellm(self, provider: str) -> None:
        """将 Key 注入到 os.environ，供 LiteLLM 读取。调用后立即使用，用完可清除。"""
        import os
        key = self.get_key(provider)
        if key:
            env_key = _PROVIDER_ENV.get(provider, "OPENAI_API_KEY")
            if provider in ("dashscope", "qwen", "qwen-max"):
                env_key = "DASHSCOPE_API_KEY"
            os.environ[env_key] = key

    def clear(self) -> None:
        """清除内存中的 Key（安全关闭时调用）。"""
        self._keys.clear()


async def fetch_and_decrypt_keys(
    l2_base_url: str,
    node_id: str,
    sub_account_id: str,
    private_key_pem: str,
    decrypt_fn: Callable[[str, str], str],
) -> tuple[dict[str, str], dict[str, str]]:
    """
    从 L2 拉取密文 Key，用私钥解密。
    返回 (keys_dict, model_endpoints)。
    keys_dict: {provider: plain_key}，仅内存使用，禁止落盘或日志。
    model_endpoints: {"api-1": "gpt-4o", ...}，L2 下发的模型通道配置。
    """
    import httpx

    url = f"{l2_base_url.rstrip('/')}/api/v2/keys"
    params = {"node_id": node_id}
    headers = {"X-Sub-Account-Id": sub_account_id}

    logger.debug("[L3 Keys] 请求 L2 GET %s node_id=%s sub_account_id=%s", url, node_id, sub_account_id[:16] + "..." if len(sub_account_id) > 16 else sub_account_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    enc_list = data.get("encrypted_api_keys", [])
    logger.debug("[L3 Keys] L2 返回 %d 个加密 Key，providers=%s", len(enc_list), [x.get("provider") for x in enc_list])
    result: dict[str, str] = {}
    provider_alias = {"qwen": "dashscope", "openai": "openai", "dashscope": "dashscope"}
    for item in data.get("encrypted_api_keys", []):
        provider = (item.get("provider") or "").lower()
        enc = item.get("encrypted_key", "")
        if not provider or not enc:
            continue
        try:
            plain = decrypt_fn(enc, private_key_pem)
            norm = provider_alias.get(provider, provider)
            result[norm] = plain
            if norm != provider:
                result[provider] = plain  # 保留原始 key 便于查找
        except Exception as e:
            logger.warning("[SecurityContext] 解密 %s 失败: %s", provider, type(e).__name__)
    model_endpoints = data.get("model_endpoints") or {}
    if not isinstance(model_endpoints, dict):
        model_endpoints = {}
    return result, model_endpoints


async def try_fetch_keys_from_l2(ctx: SecurityContext) -> bool:
    """
    当 ctx 无 Key 时，从 L2 拉取并注入。
    需已配对：l2_gateway_config.json 含 paired=true、node_id、sub_account_id、l2_base_url。
    返回 True 表示成功拉取并注入。
    """
    if ctx.has_any_key():
        return True
    logger.info("[L3] 无 API Key（启动时未拉取），尝试从 L2 兜底拉取...")
    if not _GATEWAY_CONFIG.exists():
        logger.warning("[L3] 未找到 l2_gateway_config.json，跳过 L2 拉取")
        return False
    if not _IDENTITY_PATH.exists():
        logger.warning("[L3] 未找到 l3_identity.json，跳过 L2 拉取")
        return False
    try:
        cfg = json.loads(_GATEWAY_CONFIG.read_text(encoding="utf-8"))
        if not cfg.get("paired"):
            logger.warning("[L3] 未配对 (paired=false)，跳过 L2 拉取")
            return False
        l2_base = (cfg.get("l2_base_url") or _os.environ.get("L2_BASE_URL", "")).strip()
        node_id = (cfg.get("node_id") or "").strip()
        sub_id = (cfg.get("sub_account_id") or "").strip()
        if not l2_base or not node_id or not sub_id:
            logger.warning("[L3] 配置不完整 (l2_base_url/node_id/sub_account_id)，跳过 L2 拉取")
            return False
        ident = json.loads(_IDENTITY_PATH.read_text(encoding="utf-8"))
        priv_pem = ident.get("private_key_pem") or ""
        if not priv_pem:
            logger.warning("[L3] 无私钥，跳过 L2 拉取")
            return False
        from l3_node.crypto import decrypt_with_private_key

        def _decrypt(enc: str, _: str) -> str:
            return decrypt_with_private_key(enc, priv_pem)

        keys, model_eps = await fetch_and_decrypt_keys(l2_base, node_id, sub_id, priv_pem, _decrypt)
        logger.debug("[L3 Keys] 兜底拉取解密后 providers=%s model_endpoints=%s", list(keys.keys()), model_eps)
        if not keys:
            logger.warning("[L3] L2 返回了空 Key，请在 L2 管理为子账号添加 API Key")
            return False
        provider_alias = {"qwen": "dashscope", "openai": "openai", "dashscope": "dashscope"}
        for prov, plain in keys.items():
            norm = provider_alias.get(prov, prov)
            ctx.set_key(norm, plain)
            if norm != prov:
                ctx.set_key(prov, plain)
        logger.info("[L3] 已从 L2 拉取 API Key 并注入（兜底拉取成功）providers=%s", list(ctx._keys.keys()))
        return True
    except Exception as e:
        logger.warning("[L3] 从 L2 兜底拉取 Key 失败: %s", e)
        return False


def _inject_env_keys_into_ctx(ctx: SecurityContext) -> bool:
    """将环境变量中的 Key 注入 ctx（.env 已加载时可用）。"""
    if ctx.has_any_key():
        return True
    dash = _os.environ.get("DASHSCOPE_API_KEY") or _os.environ.get("QWEN_API_KEY") or _os.environ.get("QWEN_AI_API_KEY")
    openai_key = _os.environ.get("OPENAI_API_KEY")
    if dash:
        ctx.set_key("dashscope", dash.strip())
        logger.info("[L3] 已从环境变量注入 DASHSCOPE_API_KEY")
        logger.debug("[L3 Keys] 环境变量兜底: provider=dashscope")
        return True
    if openai_key:
        ctx.set_key("openai", openai_key.strip())
        logger.info("[L3] 已从环境变量注入 OPENAI_API_KEY")
        logger.debug("[L3 Keys] 环境变量兜底: provider=openai")
        return True
    return False


class LiteLLMEngine:
    """
    L3 直连 LLM 引擎。
    使用 SecurityContext 中的明文 Key，向 api.openai.com 等直接发起请求。
    本地处理超时、Fallback 重试。
    """

    def __init__(
        self,
        security_context: SecurityContext,
        model_name: str = "gpt-4o-mini",
        fallback_models: Optional[list[str]] = None,
        timeout: float = 60.0,
        max_attempts: int = 2,
    ) -> None:
        import os
        self.ctx = security_context
        self.model_name = model_name or "gpt-4o-mini"
        if fallback_models:
            self.fallback_models = fallback_models
        else:
            env_fb = os.environ.get("LITELLM_FALLBACK_MODELS", "").strip()
            if env_fb:
                self.fallback_models = [m.strip() for m in env_fb.split(",") if m.strip()]
            elif self.ctx.get_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY"):
                self.fallback_models = [_dashscope_econ_fallback()]
            else:
                self.fallback_models = ["ollama/qwen2.5"]
        # 有 dashscope 时绝不使用 ollama
        if (self.ctx.get_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY")) and "ollama" in str(self.fallback_models).lower():
            self.fallback_models = [m for m in self.fallback_models if "ollama" not in m.lower()]
            if not self.fallback_models:
                self.fallback_models = [_dashscope_econ_fallback()]
        if (self.ctx.get_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY")) and "ollama" in (self.model_name or "").lower():
            self.model_name = os.environ.get("LLM_MODEL", _L3_DEFAULT_REASONING_MODEL)
            if not (self.model_name or "").startswith(("dashscope/", "qwen")):
                fb = _dashscope_econ_fallback()
                self.model_name = f"dashscope/{self.model_name}" if self.model_name else fb
        self.timeout = timeout
        self.max_attempts = max_attempts

    def _resolve_provider(self, model: str) -> str:
        m = (model or "").lower()
        if "gpt" in m or "openai" in m:
            return "openai"
        if "qwen" in m or "dashscope" in m:
            return "dashscope"
        return "openai"

    def _inject_key(self, model: str) -> None:
        import os
        provider = self._resolve_provider(model)
        key = self.ctx.get_key(provider)
        logger.debug("[L3 LLM] _inject_key model=%s provider=%s has_key=%s", model, provider, bool(key))
        if key:
            if provider == "dashscope":
                os.environ["DASHSCOPE_API_KEY"] = key
            else:
                os.environ["OPENAI_API_KEY"] = key

    def _normalize_model(self, model: str) -> str:
        m = (model or "").strip()
        if not m:
            return self.model_name
        ml = m.lower()
        if ml.startswith("qwen") and not ml.startswith("qwen/") and not ml.startswith("dashscope/"):
            return f"dashscope/{m}"
        return m

    async def generate_response(
        self,
        messages: list[dict[str, str]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str | dict[str, Any]:
        """同步风格调用，带重试与 Fallback。无 Key 时自动向 L2 请求。"""
        try:
            from l3_node.early_log import trace
            trace("llm_client: importing litellm (may need model_prices json)...")
        except ImportError:
            pass
        try:
            import tiktoken_ext.openai_public  # noqa: F401 - PyInstaller 需预加载
        except ImportError:
            pass
        import litellm

        purpose = _pop_l3_call_purpose(kwargs)

        # 优先从 env 注入，确保有 DASHSCOPE 时绝不走 Ollama
        _inject_env_keys_into_ctx(self.ctx)
        has_keys = self.ctx.has_any_key()
        logger.info(
            "[L3 LLM][调度] purpose=%s action=chat_completion has_key=%s %s",
            purpose,
            has_keys,
            _brief_llm_context(messages, tools),
        )
        if not has_keys:
            logger.info("[L3] 从 L2 兜底拉取 API Key...")
            await try_fetch_keys_from_l2(self.ctx)
        if not self.ctx.has_any_key():
            raise RuntimeError(
                "未配置 API Key。请在 L2 管理为子账号添加 API Key，或在项目根 .env 中设置 DASHSCOPE_API_KEY"
            )
        # 有 dashscope 时强制使用 dashscope，绝不连接 Ollama
        has_dashscope = self.ctx.get_key("dashscope") or _os.environ.get("DASHSCOPE_API_KEY")
        if has_dashscope:
            self.fallback_models = [m for m in (self.fallback_models or []) if "ollama" not in (m or "").lower()]
            if not self.fallback_models:
                self.fallback_models = [_dashscope_econ_fallback()]
            if "ollama" in (self.model_name or "").lower():
                self.model_name = _os.environ.get("LLM_MODEL", _L3_DEFAULT_REASONING_MODEL)
                if not (self.model_name or "").startswith(("dashscope/", "qwen")):
                    fb = _dashscope_econ_fallback()
                    self.model_name = f"dashscope/{self.model_name}" if self.model_name else fb

        models_to_try = [self.model_name] + [m for m in (self.fallback_models or []) if m != self.model_name]
        last_error: Optional[Exception] = None

        for attempt in range(self.max_attempts):
            model = self._normalize_model(models_to_try[min(attempt, len(models_to_try) - 1)])
            self._inject_key(model)
            phase = "primary" if attempt == 0 else "fallback_resilience"
            next_if_fail = (
                models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                if attempt + 1 < len(models_to_try)
                else None
            )
            logger.info(
                "[L3 LLM][调度] purpose=%s phase=%s attempt=%d/%d model=%s next_if_fail=%s",
                purpose,
                phase,
                attempt + 1,
                self.max_attempts,
                model,
                next_if_fail or "-",
            )
            try:
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": self.timeout,
                }
                if tools:
                    kwargs_chat["tools"] = tools

                response = await litellm.acompletion(**kwargs_chat)
                choice = response.choices[0] if response.choices else None
                if not choice:
                    logger.info(
                        "[L3 LLM][调度] purpose=%s result=empty model_used=%s",
                        purpose,
                        model,
                    )
                    return ""
                msg = choice.message
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    logger.info(
                        "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=tool_calls n=%d",
                        purpose,
                        model,
                        len(msg.tool_calls),
                    )
                    return {"content": msg.content or "", "tool_calls": msg.tool_calls}
                text = (msg.content or "").strip()
                logger.info(
                    "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=text chars=%d",
                    purpose,
                    model,
                    len(text),
                )
                return text
            except Exception as e:
                last_error = e
                err_msg = str(e)
                is_connect_err = "ConnectError" in type(e).__name__ or "connect" in err_msg.lower()
                if is_connect_err:
                    logger.warning(
                        "[L3 LLM] 网络不可达 model=%s: %s。请检查本机能否访问 dashscope.aliyuncs.com，或配置 HTTP_PROXY/HTTPS_PROXY",
                        model, e,
                    )
                if attempt < self.max_attempts - 1 and len(models_to_try) > 1:
                    nxt = models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                    logger.info(
                        "[L3 LLM][调度] purpose=%s phase=fallback_chain from=%s to=%s err=%s: %s",
                        purpose,
                        model,
                        nxt,
                        type(e).__name__,
                        str(e)[:320],
                    )
                    logger.warning(
                        "[L3 LLM] attempt=%s model=%s 失败，降级: %s",
                        attempt + 1, model, e,
                    )
                else:
                    logger.exception("[L3 LLM] 调用异常 model=%s: %s", model, e)
                    raise last_error
        raise last_error or RuntimeError("LLM 调用失败")

    async def generate_response_stream(
        self,
        messages: list[dict[str, str]],
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """流式调用，带重试与 Fallback。无 Key 时自动向 L2 请求。"""
        # PyInstaller 打包需预加载 tiktoken_ext，否则 litellm 流式 token 统计会报 Unknown encoding cl100k_base
        try:
            import tiktoken_ext.openai_public  # noqa: F401
        except ImportError:
            pass
        import litellm

        purpose = _pop_l3_call_purpose(kwargs)

        _inject_env_keys_into_ctx(self.ctx)
        has_keys = self.ctx.has_any_key()
        logger.info(
            "[L3 LLM][调度] purpose=%s action=chat_completion_stream has_key=%s %s",
            purpose,
            has_keys,
            _brief_llm_context(messages, None),
        )
        if not has_keys:
            await try_fetch_keys_from_l2(self.ctx)
        if not self.ctx.has_any_key():
            raise RuntimeError(
                "未配置 API Key。请在 L2 管理为子账号添加 API Key，或在项目根 .env 中设置 DASHSCOPE_API_KEY"
            )
        has_dashscope = self.ctx.get_key("dashscope") or _os.environ.get("DASHSCOPE_API_KEY")
        if has_dashscope:
            self.fallback_models = [m for m in (self.fallback_models or []) if "ollama" not in (m or "").lower()]
            if not self.fallback_models:
                self.fallback_models = [_dashscope_econ_fallback()]
            if "ollama" in (self.model_name or "").lower():
                self.model_name = _os.environ.get("LLM_MODEL", _L3_DEFAULT_REASONING_MODEL)
                if not (self.model_name or "").startswith(("dashscope/", "qwen")):
                    fb = _dashscope_econ_fallback()
                    self.model_name = f"dashscope/{self.model_name}" if self.model_name else fb

        models_to_try = [self.model_name] + [
            m for m in self.fallback_models if m != self.model_name
        ]
        last_error: Optional[Exception] = None

        for attempt in range(self.max_attempts):
            model = self._normalize_model(
                models_to_try[min(attempt, len(models_to_try) - 1)]
            )
            self._inject_key(model)
            phase = "primary" if attempt == 0 else "fallback_resilience"
            next_if_fail = (
                models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                if attempt + 1 < len(models_to_try)
                else None
            )
            logger.info(
                "[L3 LLM][调度] purpose=%s phase=%s attempt=%d/%d model=%s stream=1 next_if_fail=%s",
                purpose,
                phase,
                attempt + 1,
                self.max_attempts,
                model,
                next_if_fail or "-",
            )
            full_content: list[str] = []
            try:
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": self.timeout,
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
                out = "".join(full_content).strip()
                logger.info(
                    "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=stream chars=%d",
                    purpose,
                    model,
                    len(out),
                )
                return out
            except Exception as e:
                last_error = e
                err_msg = str(e)
                is_connect_err = "ConnectError" in type(e).__name__ or "connect" in err_msg.lower()
                if is_connect_err:
                    logger.warning(
                        "[L3 LLM] 网络不可达 model=%s: %s。请检查本机能否访问 dashscope.aliyuncs.com，或配置 HTTP_PROXY/HTTPS_PROXY",
                        model, e,
                    )
                if attempt < self.max_attempts - 1 and len(models_to_try) > 1:
                    nxt = models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                    logger.info(
                        "[L3 LLM][调度] purpose=%s phase=fallback_chain stream=1 from=%s to=%s err=%s: %s",
                        purpose,
                        model,
                        nxt,
                        type(e).__name__,
                        str(e)[:320],
                    )
                    logger.warning(
                        "[L3 LLM] 流式 attempt=%s model=%s 失败，降级: %s",
                        attempt + 1, model, e,
                    )
                else:
                    logger.exception("[L3 LLM] 流式异常 model=%s: %s", model, e)
                    raise last_error
        raise last_error or RuntimeError("LLM 流式调用失败")
