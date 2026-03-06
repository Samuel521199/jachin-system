"""
Jachin Nexus V2 - L3 本地解密与直连 LLM

内存级解密：从 L2 拉取密文 Key，用 L3 私钥解密，仅存于 SecurityContext。
严禁明文落盘或打入日志。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

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

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

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
        self.ctx = security_context
        self.model_name = model_name or "gpt-4o-mini"
        self.fallback_models = fallback_models or ["ollama/qwen2.5"]
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
        if ml.startswith("qwen-") and not ml.startswith("qwen/"):
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
        """同步风格调用，带重试与 Fallback。"""
        import litellm

        models_to_try = [self.model_name] + [
            m for m in self.fallback_models if m != self.model_name
        ]
        last_error: Optional[Exception] = None

        for attempt in range(self.max_attempts):
            model = self._normalize_model(
                models_to_try[min(attempt, len(models_to_try) - 1)]
            )
            self._inject_key(model)
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
                    return ""
                msg = choice.message
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    return {"content": msg.content or "", "tool_calls": msg.tool_calls}
                return (msg.content or "").strip()
            except Exception as e:
                last_error = e
                if attempt < self.max_attempts - 1 and len(models_to_try) > 1:
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
        """流式调用，带重试与 Fallback。"""
        import litellm

        models_to_try = [self.model_name] + [
            m for m in self.fallback_models if m != self.model_name
        ]
        last_error: Optional[Exception] = None

        for attempt in range(self.max_attempts):
            model = self._normalize_model(
                models_to_try[min(attempt, len(models_to_try) - 1)]
            )
            self._inject_key(model)
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
                return "".join(full_content).strip()
            except Exception as e:
                last_error = e
                if attempt < self.max_attempts - 1 and len(models_to_try) > 1:
                    logger.warning(
                        "[L3 LLM] 流式 attempt=%s model=%s 失败，降级: %s",
                        attempt + 1, model, e,
                    )
                else:
                    logger.exception("[L3 LLM] 流式异常 model=%s: %s", model, e)
                    raise last_error
        raise last_error or RuntimeError("LLM 流式调用失败")
