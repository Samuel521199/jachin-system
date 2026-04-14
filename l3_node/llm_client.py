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
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

from core.brain.llm.dashscope_regional import (
    get_dashscope_regional_api_base,
    get_dashscope_regional_credentials,
    get_jachin_active_region,
    litellm_apply_dashscope_credentials,
    user_configured_regional_dashscope_key_env,
)

try:
    from l3_node.llm_budget import BudgetExhaustedError as _BudgetExhaustedError
except ImportError:
    _BudgetExhaustedError = None  # type: ignore[misc, assignment]


def _dashscope_econ_fallback() -> str:
    """与 core 一致：主模型失败时的低成本 DashScope 降级。"""
    try:
        from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

        return DASHSCOPE_ECON_FALLBACK_MODEL
    except ImportError:
        return "dashscope/qwen3.5-flash-2026-02-23"


_L3_DEFAULT_REASONING_MODEL = "qwen3.5-plus"


def _effective_max_tokens_for_model(model: str, requested: int) -> int:
    """
    DashScope 对部分模型限制 max_tokens 上界；qwen-max / vl-max 等分支的上限由环境变量
    JACHIN_QWEN_MAX_MAX_TOKENS 给出（默认 8192），仅保证 >=1，不再在代码里锁死 API 未来可能放大的输出上限。
    agent_core ReAct 常传较大 max_tokens，须在调用 litellm 前按该上限钳制，避免 400。
    """
    try:
        n = int(requested)
    except (TypeError, ValueError):
        n = 1024
    n = max(1, n)
    ml = (model or "").lower()
    tail = ml.split("/")[-1] if "/" in ml else ml
    if "qwen-max" in tail or "vl-max" in tail or "qwen-vl-max" in ml:
        import os as _env_cap

        cap = 8192
        try:
            cap = int(_env_cap.environ.get("JACHIN_QWEN_MAX_MAX_TOKENS", "8192"))
            cap = max(1, cap)
        except ValueError:
            cap = 8192
        if n > cap:
            logger.info(
                "[L3 LLM] max_tokens=%s 超过模型 %s 允许上限 %s，已钳制为 %s",
                n,
                model,
                cap,
                cap,
            )
        return min(n, cap)
    return n


def _is_probably_network_llm_error(err: BaseException) -> bool:
    """避免把 InvalidParameter/max_tokens 等 400 误报成「网络不可达」。"""
    msg = str(err).lower()
    if "invalidparameter" in msg.replace(" ", ""):
        return False
    if "max_tokens" in msg and ("8192" in msg or "range of max_tokens" in msg):
        return False
    if "internalerror.algo" in msg.replace(" ", ""):
        return False
    name = type(err).__name__
    return "ConnectError" in name or "connecterror" in msg or "connection reset" in msg


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
            if isinstance(c, list):
                texts = [
                    str(p.get("text", ""))
                    for p in c
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                c = texts[0] if texts else f"[multimodal n={len(c)}]"
            elif not isinstance(c, str):
                c = str(c)
            last_user = c[:120].replace("\n", " ")
            break
    tc = len(tools) if tools else 0
    return f"msgs={n} roles={roles} tools={tc} last_user_preview={last_user!r}"


def _extract_tool_call_name_args(tc: Any) -> tuple[str, str]:
    """从 litellm/OpenAI tool_call 对象或 dict 取出 function.name / arguments。"""
    if isinstance(tc, dict):
        fn = tc.get("function")
        if isinstance(fn, dict):
            return str(fn.get("name") or ""), str(fn.get("arguments") or "")
        return "", ""
    fn = getattr(tc, "function", None)
    if fn is None:
        return "", ""
    n = getattr(fn, "name", None)
    a = getattr(fn, "arguments", None)
    if hasattr(fn, "model_dump"):
        try:
            dumped = fn.model_dump()
            if isinstance(dumped, dict):
                return str(dumped.get("name") or n or ""), str(dumped.get("arguments") or a or "")
        except Exception:
            pass
    return str(n or ""), str(a or "")


def tool_calls_to_react_text(
    tool_calls: list[Any] | None,
    *,
    openapi_fname_to_tool_id: Optional[dict[str, str]] = None,
    use_first_only: bool = True,
) -> str:
    """
    将 API 返回的 function/tool 调用转写为 Thought/Action/Action Input，供 agent_core._parse_action。
    *openapi_fname_to_tool_id*：清洗后的 function.name → 真实 tool id（如 mcp_query → mcp:query）。
    """
    openapi_fname_to_tool_id = openapi_fname_to_tool_id or {}
    if not tool_calls:
        return ""
    calls = list(tool_calls)
    if use_first_only and len(calls) > 1:
        logger.info("[L3 LLM] API 返回 %d 个 tool_calls，ReAct 单步仅取第一个", len(calls))
        calls = calls[:1]
    blocks: list[str] = []
    for tc in calls:
        name_api, args_raw = _extract_tool_call_name_args(tc)
        tid = openapi_fname_to_tool_id.get(str(name_api or "")) or str(name_api or "")
        if not tid:
            continue
        if isinstance(args_raw, (dict, list)):
            args_str = json.dumps(args_raw, ensure_ascii=False)
        else:
            args_str = str(args_raw or "").strip()
        if not args_str:
            args_str = "{}"
        blocks.append(f"Thought: （API function calling）\nAction: {tid}\nAction Input: {args_str}")
    return "\n\n".join(blocks)


def _accumulate_stream_tool_call_delta(tc_list: Any, acc: dict[int, dict[str, str]]) -> None:
    """合并流式 chunk 中的 tool_calls 片段（按 index）。"""
    if not tc_list:
        return
    for part in tc_list:
        if isinstance(part, dict):
            idx = int(part.get("index", 0) or 0)
            pid = part.get("id")
            fn = part.get("function")
            if isinstance(fn, dict):
                pname = fn.get("name")
                pargs = fn.get("arguments")
            else:
                pname, pargs = None, None
        else:
            idx = int(getattr(part, "index", 0) or 0)
            pid = getattr(part, "id", None)
            fn = getattr(part, "function", None)
            pname = getattr(fn, "name", None) if fn is not None else None
            pargs = getattr(fn, "arguments", None) if fn is not None else None
        slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if pid:
            slot["id"] = str(pid)
        if pname:
            slot["name"] = str(pname)
        if pargs:
            slot["arguments"] += str(pargs)


def _merged_tool_calls_from_stream_acc(acc: dict[int, dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in sorted(acc.keys()):
        m = acc[i]
        if not m.get("name"):
            continue
        out.append({"function": {"name": m["name"], "arguments": m.get("arguments") or "{}"}})
    return out


def _pop_l3_call_purpose(kwargs: dict[str, Any], default: str = "unspecified") -> str:
    p = kwargs.pop("l3_call_purpose", None)
    if p is None:
        p = kwargs.pop("call_purpose", None)
    s = str(p or "").strip()
    return s if s else default


class RunCancelledError(RuntimeError):
    """协作式取消：l3_cancel_event 已 set。"""


def _pop_l3_runtime_controls(kwargs: dict[str, Any]) -> tuple[Any, Any, Any]:
    acc = kwargs.pop("l3_token_accumulator", None)
    budget = kwargs.pop("l3_token_budget_max", None)
    cancel = kwargs.pop("l3_cancel_event", None)
    return acc, budget, cancel


def _apply_usage_budget(response: object, acc: Any, budget: Any) -> None:
    if acc is None or not isinstance(acc, dict):
        return
    try:
        from l3_node.llm_budget import accumulate_and_check, extract_usage_tokens

        pt, ct = extract_usage_tokens(response)
        accumulate_and_check(acc, pt, ct, budget)
    except ImportError:
        pass

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
    _rk, _rb = get_dashscope_regional_credentials()
    if _rb:
        _os.environ["DASHSCOPE_API_BASE"] = str(_rb).strip()
    else:
        _os.environ.setdefault("DASHSCOPE_API_BASE", get_dashscope_regional_api_base())
    if _rk:
        ctx.set_key("dashscope", str(_rk).strip())
        logger.info("[L3] 已从区域/环境变量注入 DashScope API Key")
        logger.debug("[L3 Keys] 环境变量兜底: provider=dashscope (regional)")
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


def _merge_litellm_optional_penalties(kwargs_remaining: dict[str, Any], kwargs_chat: dict[str, Any]) -> None:
    """将 presence_penalty / frequency_penalty 等传入 LiteLLM（此前 kwargs 被丢弃）。默认适度抑制复读，可用环境变量覆盖或关闭。"""
    for k in ("presence_penalty", "frequency_penalty", "top_p", "stop", "seed"):
        if k in kwargs_remaining and kwargs_remaining[k] is not None:
            kwargs_chat[k] = kwargs_remaining.pop(k)
    if _os.environ.get("JACHIN_LLM_DISABLE_DEFAULT_PENALTIES", "").strip().lower() in ("1", "true", "yes", "on"):
        for k, envk in (
            ("presence_penalty", "JACHIN_LLM_PRESENCE_PENALTY"),
            ("frequency_penalty", "JACHIN_LLM_FREQUENCY_PENALTY"),
        ):
            raw = _os.environ.get(envk, "").strip()
            if raw:
                try:
                    kwargs_chat[k] = float(raw)
                except ValueError:
                    pass
        return
    try:
        kwargs_chat.setdefault(
            "presence_penalty",
            float(_os.environ.get("JACHIN_LLM_PRESENCE_PENALTY", "0.35")),
        )
        kwargs_chat.setdefault(
            "frequency_penalty",
            float(_os.environ.get("JACHIN_LLM_FREQUENCY_PENALTY", "0.2")),
        )
    except ValueError:
        pass


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
            self.fallback_models = list(fallback_models)
        else:
            env_fb = os.environ.get("LITELLM_FALLBACK_MODELS", "").strip()
            if env_fb:
                self.fallback_models = [m.strip() for m in env_fb.split(",") if m.strip()]
            elif self.ctx.get_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY"):
                self.fallback_models = [_dashscope_econ_fallback()]
            else:
                try:
                    from core.llm_provider import DASHSCOPE_REASONING_MODEL as _plus

                    self.fallback_models = [_plus]
                except ImportError:
                    self.fallback_models = ["dashscope/qwen3.5-plus"]
        try:
            from core.llm_provider import DASHSCOPE_REASONING_MODEL, sanitize_llm_fallback_models

            self.fallback_models = sanitize_llm_fallback_models(self.fallback_models or [])
        except ImportError:
            DASHSCOPE_REASONING_MODEL = "dashscope/qwen3.5-plus"  # type: ignore[misc]
        if not self.fallback_models:
            self.fallback_models = (
                [_dashscope_econ_fallback()]
                if (self.ctx.get_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY"))
                else [DASHSCOPE_REASONING_MODEL]
            )
        mn = (self.model_name or "").lower()
        if mn.startswith("ollama/") or mn.startswith("ollama:"):
            self.model_name = os.environ.get("LLM_MODEL", _L3_DEFAULT_REASONING_MODEL)
            if not (self.model_name or "").startswith(("dashscope/", "qwen")):
                self.model_name = (
                    f"dashscope/{self.model_name}" if self.model_name else DASHSCOPE_REASONING_MODEL
                )
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
                # 与 litellm_apply_dashscope_credentials 一致：已配 *_SEA / *_CN 时勿用 ctx（常为 L2 国内 Key）污染 env
                if user_configured_regional_dashscope_key_env():
                    rk, _ = get_dashscope_regional_credentials()
                    if rk:
                        os.environ["DASHSCOPE_API_KEY"] = rk
                    else:
                        os.environ["DASHSCOPE_API_KEY"] = key
                else:
                    os.environ["DASHSCOPE_API_KEY"] = key
                os.environ["DASHSCOPE_API_BASE"] = get_dashscope_regional_api_base()
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
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        openapi_fname_to_tool_id: Optional[dict[str, str]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
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
        _acc, _budget, _cancel = _pop_l3_runtime_controls(kwargs)
        _override_model = kwargs.pop("l3_override_model", None)
        openapi_fname_to_tool_id = kwargs.pop("openapi_fname_to_tool_id", openapi_fname_to_tool_id)

        # 优先从 env 注入，确保有 DASHSCOPE 时绝不走 Ollama
        _inject_env_keys_into_ctx(self.ctx)
        has_keys = self.ctx.has_any_key()
        logger.info(
            "[L3 LLM][调度] purpose=%s action=chat_completion has_key=%s %s",
            purpose,
            has_keys,
            _brief_llm_context(messages, tools),
        )
        logger.info(
            "[L3 LLM][调度] dashscope: region=%s api_base=%s（SEA 应为 dashscope-intl 域名；CN 为 dashscope.aliyuncs.com）",
            get_jachin_active_region(),
            get_dashscope_regional_api_base(),
        )
        if not has_keys:
            logger.info("[L3] 从 L2 兜底拉取 API Key...")
            await try_fetch_keys_from_l2(self.ctx)
        if not self.ctx.has_any_key():
            raise RuntimeError(
                "未配置 API Key。请在 L2 管理为子账号添加 API Key，或在项目根 .env 中设置 DASHSCOPE_API_KEY"
            )
        try:
            from core.llm_provider import DASHSCOPE_REASONING_MODEL, sanitize_llm_fallback_models

            self.fallback_models = sanitize_llm_fallback_models(self.fallback_models or [])
            if (self.model_name or "").lower().startswith(("ollama/", "ollama:")):
                self.model_name = _os.environ.get("LLM_MODEL", _L3_DEFAULT_REASONING_MODEL)
                if not (self.model_name or "").startswith(("dashscope/", "qwen")):
                    self.model_name = (
                        f"dashscope/{self.model_name}" if self.model_name else DASHSCOPE_REASONING_MODEL
                    )
        except ImportError:
            pass

        models_to_try = [self.model_name] + [m for m in (self.fallback_models or []) if m != self.model_name]
        if _override_model:
            om = self._normalize_model(str(_override_model).strip())
            models_to_try = [om] + [m for m in models_to_try if self._normalize_model(m) != om]
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
                effective_tools = tools
                t0 = time.perf_counter()
                if _cancel is not None and getattr(_cancel, "is_set", lambda: False)():
                    raise RunCancelledError("l3_llm_cancelled_before_completion")
                _mt = _effective_max_tokens_for_model(model, max_tokens)
                try:
                    from l3_node.dashscope_multimodal_normalize import (
                        maybe_normalize_messages_for_dashscope_litellm,
                    )

                    _msgs_api = maybe_normalize_messages_for_dashscope_litellm(
                        list(messages or []), model=model
                    )
                except Exception:
                    _msgs_api = list(messages or [])
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": _msgs_api,
                    "temperature": temperature,
                    "max_tokens": _mt,
                    "timeout": self.timeout,
                }
                if tools:
                    try:
                        from core.llm_provider import dashscope_vl_should_omit_openai_tools_for_multimodal

                        if dashscope_vl_should_omit_openai_tools_for_multimodal(
                            model=model, messages=list(messages or [])
                        ):
                            logger.warning(
                                "[L3 LLM] DashScope VL + 多模态：本轮不传 OpenAI tools[]（避免 image_url 被忽略），"
                                "仍以 system 内 ReAct 工具说明为准"
                            )
                            effective_tools = None
                    except Exception as _e:
                        logger.debug("[L3 LLM] dashscope VL tools 规避检查跳过: %s", _e)
                if effective_tools:
                    kwargs_chat["tools"] = effective_tools
                    kwargs_chat["tool_choice"] = "auto"

                _rfmt = kwargs.pop("response_format", None)
                if _rfmt is not None:
                    kwargs_chat["response_format"] = _rfmt
                _merge_litellm_optional_penalties(kwargs, kwargs_chat)
                if kwargs:
                    logger.debug("[L3 LLM] ignoring unsupported kwargs: %s", sorted(kwargs.keys()))

                litellm_apply_dashscope_credentials(
                    model, kwargs_chat, explicit_api_key=self.ctx.get_key("dashscope")
                )

                try:
                    from l3_node.multimodal_log import (
                        log_litellm_outbound_messages,
                        summarize_messages_for_litellm_dispatch,
                    )

                    log_litellm_outbound_messages(
                        logger,
                        list(kwargs_chat.get("messages") or []),
                        purpose=str(purpose),
                        model=str(model),
                        stream=False,
                    )
                    logger.debug(
                        "[L3 LLM][dispatch_payload_summary]\n%s",
                        summarize_messages_for_litellm_dispatch(
                            list(kwargs_chat.get("messages") or []), purpose=str(purpose)
                        ),
                    )
                except Exception:
                    pass

                _cap = float(self.timeout) + 45.0
                try:
                    response = await asyncio.wait_for(
                        litellm.acompletion(**kwargs_chat),
                        timeout=_cap,
                    )
                except asyncio.TimeoutError as te:
                    raise TimeoutError(
                        f"L3 LLM 非流式逾时 (>{_cap:.0f}s, purpose={purpose}, model={model})"
                    ) from te
                _apply_usage_budget(response, _acc, _budget)
                choice = response.choices[0] if response.choices else None
                if not choice:
                    logger.info(
                        "[L3 LLM][调度] purpose=%s result=empty model_used=%s",
                        purpose,
                        model,
                    )
                    try:
                        from core.deep_execution_log import log_llm_completion

                        log_llm_completion(
                            source="l3_node.llm_client",
                            purpose=str(purpose),
                            phase=phase,
                            model=model,
                            stream=False,
                            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                            messages=messages,
                            tools=effective_tools,
                            response_text="",
                        )
                    except Exception:
                        pass
                    return ""
                msg = choice.message
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    tclist = list(msg.tool_calls)
                    synth = tool_calls_to_react_text(
                        tclist,
                        openapi_fname_to_tool_id=openapi_fname_to_tool_id,
                    )
                    rest = (getattr(msg, "content", None) or "").strip()
                    out = (synth + "\n\n" + rest).strip() if rest else synth
                    logger.info(
                        "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=tool_calls->react n=%d chars=%d",
                        purpose,
                        model,
                        len(tclist),
                        len(out),
                    )
                    try:
                        from core.deep_execution_log import log_llm_completion

                        _names = []
                        try:
                            for _tc in tclist:
                                _fn = getattr(getattr(_tc, "function", None), "name", None)
                                _names.append(str(_fn or _tc))
                        except Exception:
                            _names = ["(unreadable)"]
                        log_llm_completion(
                            source="l3_node.llm_client",
                            purpose=str(purpose),
                            phase=phase,
                            model=model,
                            stream=False,
                            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                            messages=messages,
                            tools=effective_tools,
                            response_text=out,
                            response_dict_summary=(
                                f"stream_tool_calls_synthesized_to_react n={len(tclist)} names={_names}"
                            ),
                        )
                    except Exception:
                        pass
                    return out
                text = (msg.content or "").strip()
                logger.info(
                    "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=text chars=%d",
                    purpose,
                    model,
                    len(text),
                )
                try:
                    from core.deep_execution_log import log_llm_completion

                    log_llm_completion(
                        source="l3_node.llm_client",
                        purpose=str(purpose),
                        phase=phase,
                        model=model,
                        stream=False,
                        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                        messages=messages,
                        tools=effective_tools,
                        response_text=text,
                    )
                except Exception:
                    pass
                return text
            except Exception as e:
                if _BudgetExhaustedError and isinstance(e, _BudgetExhaustedError):
                    raise
                if isinstance(e, RunCancelledError):
                    raise
                last_error = e
                if _is_probably_network_llm_error(e):
                    logger.warning(
                        "[L3 LLM] 网络不可达 model=%s: %s。请检查本机能否访问 dashscope.aliyuncs.com，或配置 HTTP_PROXY/HTTPS_PROXY",
                        model,
                        e,
                    )
                elif "max_tokens" in str(e).lower():
                    logger.warning(
                        "[L3 LLM] 模型/API 参数错误（非纯网络故障）model=%s: %s",
                        model,
                        str(e)[:400],
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
                    try:
                        from core.deep_execution_log import log_llm_completion

                        log_llm_completion(
                            source="l3_node.llm_client",
                            purpose=str(purpose),
                            phase=phase,
                            model=model,
                            stream=False,
                            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                            messages=messages,
                            tools=tools,
                            error=f"{type(e).__name__}: {e}"[:8000],
                        )
                    except Exception:
                        pass
                    raise last_error
        raise last_error or RuntimeError("LLM 调用失败")

    async def generate_response_stream(
        self,
        messages: list[dict[str, Any]],
        chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        openapi_fname_to_tool_id: Optional[dict[str, str]] = None,
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
        _acc, _budget, _cancel = _pop_l3_runtime_controls(kwargs)
        _stream_run_id = kwargs.pop("l3_run_id", None)
        _override_model_s = kwargs.pop("l3_override_model", None)
        tools = kwargs.pop("tools", tools)
        openapi_fname_to_tool_id = kwargs.pop("openapi_fname_to_tool_id", openapi_fname_to_tool_id)

        _inject_env_keys_into_ctx(self.ctx)
        has_keys = self.ctx.has_any_key()
        _rn = (
            "react_note=已传 tools[]（流式 function calling 将转写为 ReAct Action）"
            if tools
            else "react_note=未传 tools[]（仅靠 system 内工具说明；须输出 Action:/Action Input:）"
        )
        logger.info(
            "[L3 LLM][调度] purpose=%s action=chat_completion_stream has_key=%s %s %s",
            purpose,
            has_keys,
            _brief_llm_context(messages, tools),
            _rn,
        )
        logger.info(
            "[L3 LLM][调度] dashscope: region=%s api_base=%s（SEA 应为 dashscope-intl 域名；CN 为 dashscope.aliyuncs.com）",
            get_jachin_active_region(),
            get_dashscope_regional_api_base(),
        )
        if not has_keys:
            await try_fetch_keys_from_l2(self.ctx)
        if not self.ctx.has_any_key():
            raise RuntimeError(
                "未配置 API Key。请在 L2 管理为子账号添加 API Key，或在项目根 .env 中设置 DASHSCOPE_API_KEY"
            )
        try:
            from core.llm_provider import DASHSCOPE_REASONING_MODEL, sanitize_llm_fallback_models

            self.fallback_models = sanitize_llm_fallback_models(self.fallback_models or [])
            if (self.model_name or "").lower().startswith(("ollama/", "ollama:")):
                self.model_name = _os.environ.get("LLM_MODEL", _L3_DEFAULT_REASONING_MODEL)
                if not (self.model_name or "").startswith(("dashscope/", "qwen")):
                    self.model_name = (
                        f"dashscope/{self.model_name}" if self.model_name else DASHSCOPE_REASONING_MODEL
                    )
        except ImportError:
            pass

        models_to_try = [self.model_name] + [
            m for m in self.fallback_models if m != self.model_name
        ]
        if _override_model_s:
            om = self._normalize_model(str(_override_model_s).strip())
            models_to_try = [om] + [m for m in models_to_try if self._normalize_model(m) != om]
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
            try:
                effective_tools = tools
                t0 = time.perf_counter()
                if _cancel is not None and getattr(_cancel, "is_set", lambda: False)():
                    raise RunCancelledError("l3_llm_cancelled_before_stream")
                _mt = _effective_max_tokens_for_model(model, max_tokens)
                try:
                    from l3_node.dashscope_multimodal_normalize import (
                        maybe_normalize_messages_for_dashscope_litellm,
                    )

                    _msgs_api_s = maybe_normalize_messages_for_dashscope_litellm(
                        list(messages or []), model=model
                    )
                except Exception:
                    _msgs_api_s = list(messages or [])
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": _msgs_api_s,
                    "stream": True,
                    "temperature": temperature,
                    "max_tokens": _mt,
                    "timeout": self.timeout,
                }
                if tools:
                    try:
                        from core.llm_provider import dashscope_vl_should_omit_openai_tools_for_multimodal

                        if dashscope_vl_should_omit_openai_tools_for_multimodal(
                            model=model, messages=list(messages or [])
                        ):
                            logger.warning(
                                "[L3 LLM][stream] DashScope VL + 多模态：本轮不传 OpenAI tools[]（避免 image_url 被忽略），"
                                "仍以 system 内 ReAct 工具说明为准"
                            )
                            effective_tools = None
                    except Exception as _e:
                        logger.debug("[L3 LLM][stream] dashscope VL tools 规避检查跳过: %s", _e)
                if effective_tools:
                    kwargs_chat["tools"] = effective_tools
                    kwargs_chat["tool_choice"] = "auto"
                _rfmt_s = kwargs.pop("response_format", None)
                if _rfmt_s is not None:
                    kwargs_chat["response_format"] = _rfmt_s
                _merge_litellm_optional_penalties(kwargs, kwargs_chat)
                if kwargs:
                    logger.debug("[L3 LLM][stream] ignoring unsupported kwargs: %s", sorted(kwargs.keys()))

                from core.litellm_stream_hints import merge_dashscope_stream_incremental_hint

                merge_dashscope_stream_incremental_hint(model, kwargs_chat)

                litellm_apply_dashscope_credentials(
                    model, kwargs_chat, explicit_api_key=self.ctx.get_key("dashscope")
                )

                try:
                    from l3_node.multimodal_log import (
                        log_litellm_outbound_messages,
                        summarize_messages_for_litellm_dispatch,
                    )

                    log_litellm_outbound_messages(
                        logger,
                        list(kwargs_chat.get("messages") or []),
                        purpose=str(purpose),
                        model=str(model),
                        stream=True,
                    )
                    logger.debug(
                        "[L3 LLM][dispatch_payload_summary][stream]\n%s",
                        summarize_messages_for_litellm_dispatch(
                            list(kwargs_chat.get("messages") or []), purpose=str(purpose)
                        ),
                    )
                except Exception:
                    pass

                response = await litellm.acompletion(**kwargs_chat)

                _tcall_acc: dict[int, dict[str, str]] = {}

                async def _consume_stream() -> tuple[list[str], object | None]:
                    from core.stream_text_delta import StreamDeltaNormalizer

                    # 默认始终启用 StreamDeltaNormalizer：用前两帧自动判别「真增量」vs「每帧累积全文」。
                    # DashScope/LiteLLM 部分路径每帧 content 为全文，若当增量拼接会产生严重复读。
                    # 调试可设 JACHIN_STREAM_DELTA_RAW=1 在 normalizer 内强制逐帧透传。
                    pieces: list[str] = []
                    luc: object | None = None
                    _norm = StreamDeltaNormalizer()
                    async for chunk in response:
                        if _cancel is not None and getattr(_cancel, "is_set", lambda: False)():
                            raise RunCancelledError("l3_llm_cancelled_mid_stream")
                        choice = chunk.choices[0] if chunk.choices else None
                        if not choice or not hasattr(choice, "delta"):
                            continue
                        d = choice.delta
                        _tcl = getattr(d, "tool_calls", None)
                        if _tcl:
                            _accumulate_stream_tool_call_delta(_tcl, _tcall_acc)
                        delta = getattr(d, "content", None) or ""
                        if not delta and not _tcl:
                            u = getattr(chunk, "usage", None)
                            if u is not None:
                                luc = chunk
                            continue
                        if not delta:
                            u = getattr(chunk, "usage", None)
                            if u is not None:
                                luc = chunk
                            continue
                        piece = _norm.feed(delta)
                        if piece:
                            pieces.append(piece)
                            if chunk_callback:
                                await chunk_callback(piece)
                        u = getattr(chunk, "usage", None)
                        if u is not None:
                            luc = chunk
                    return pieces, luc

                import l3_node.agent_cancel as _agent_cancel_mod

                _stream_task = asyncio.create_task(_consume_stream())
                if _stream_run_id:
                    _agent_cancel_mod.register_stream_task(str(_stream_run_id), _stream_task)
                try:
                    pieces, _last_usage_chunk = await _stream_task
                except asyncio.CancelledError:
                    raise RunCancelledError("l3_llm_stream_task_cancelled") from None
                finally:
                    if _stream_run_id:
                        _agent_cancel_mod.unregister_stream_task(str(_stream_run_id))
                if _last_usage_chunk is not None:
                    _apply_usage_budget(_last_usage_chunk, _acc, _budget)
                text_out = "".join(pieces).strip()
                merged_calls = _merged_tool_calls_from_stream_acc(_tcall_acc)
                if merged_calls:
                    synth = tool_calls_to_react_text(
                        merged_calls,
                        openapi_fname_to_tool_id=openapi_fname_to_tool_id,
                    )
                    out = (synth + "\n\n" + text_out).strip() if text_out else synth
                    logger.info(
                        "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=stream+tool_calls n=%d chars=%d",
                        purpose,
                        model,
                        len(merged_calls),
                        len(out),
                    )
                else:
                    out = text_out
                    logger.info(
                        "[L3 LLM][调度] purpose=%s result=ok model_used=%s outcome=stream chars=%d",
                        purpose,
                        model,
                        len(out),
                    )
                try:
                    from core.deep_execution_log import log_llm_completion

                    _sum = (
                        f"stream+tool_calls n={len(merged_calls)}"
                        if merged_calls
                        else "stream_text_only"
                    )
                    log_llm_completion(
                        source="l3_node.llm_client",
                        purpose=str(purpose),
                        phase=phase,
                        model=model,
                        stream=True,
                        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                        messages=messages,
                        tools=effective_tools,
                        response_text=out,
                        response_dict_summary=_sum,
                    )
                except Exception:
                    pass
                return out
            except Exception as e:
                if _BudgetExhaustedError and isinstance(e, _BudgetExhaustedError):
                    raise
                if isinstance(e, RunCancelledError):
                    raise
                last_error = e
                if _is_probably_network_llm_error(e):
                    logger.warning(
                        "[L3 LLM] 网络不可达 model=%s: %s。请检查本机能否访问 dashscope.aliyuncs.com，或配置 HTTP_PROXY/HTTPS_PROXY",
                        model,
                        e,
                    )
                elif "max_tokens" in str(e).lower():
                    logger.warning(
                        "[L3 LLM] 模型/API 参数错误（非纯网络故障）model=%s: %s",
                        model,
                        str(e)[:400],
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
                    try:
                        from core.deep_execution_log import log_llm_completion

                        log_llm_completion(
                            source="l3_node.llm_client",
                            purpose=str(purpose),
                            phase=phase,
                            model=model,
                            stream=True,
                            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                            messages=messages,
                            tools=effective_tools,
                            error=f"{type(e).__name__}: {e}"[:8000],
                        )
                    except Exception:
                        pass
                    raise last_error
        raise last_error or RuntimeError("LLM 流式调用失败")
