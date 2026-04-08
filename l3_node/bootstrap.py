"""
Jachin Nexus V2 - L3 节点引导（L2↔L3 配对，不经 L1）

1. 生成 RSA 密钥对（或加载已持久化的）
2. 向 **L2** 注册：POST /api/v2/auth/sync（含 organization_id 或 organization_slug，须落在 L2 sync_tenant_ids 内）
3. 轮询 GET /api/v2/auth/poll 等待 **L2 网关** 管理员审批
4. 拉取密文 Key，本地解密，填充 SecurityContext
5. 创建 LiteLLMEngine，可选启动 MemorySyncDaemon

工作区列表可从 L1 GET /api/v1/me/workspaces 拉取填表，属元数据，非 L1↔L3 配对。
"""
from __future__ import annotations

import logging

# 在首次 import httpx 前抑制 DEBUG 刷屏（connect_tcp/receive_response 等）
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.INFO)

import asyncio
import hashlib
import json
import os
import platform
import time
from pathlib import Path
from typing import Any, Callable, Optional

from l3_node.agent_core import MemorySyncDaemon, run_agent
from l3_node.llm_client import (
    LiteLLMEngine,
    SecurityContext,
    fetch_and_decrypt_keys,
    _inject_env_keys_into_ctx,
)

logger = logging.getLogger(__name__)

_JACHIN_DIR = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
_IDENTITY_PATH = _JACHIN_DIR / "l3_identity.json"
_GATEWAY_CONFIG_PATH = _JACHIN_DIR / "l2_gateway_config.json"


def _decrypt_fn(encrypted_b64: str, private_key_pem: str) -> str:
    from l3_node.crypto import decrypt_with_private_key
    return decrypt_with_private_key(encrypted_b64, private_key_pem)


def _device_fingerprint() -> str:
    """生成设备指纹（用于 L2 登记）。"""
    raw = f"{platform.node()}-{platform.machine()}-{os.getpid()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def ensure_identity() -> tuple[str, str]:
    """
    确保本地有 RSA 密钥对。有则加载，无则生成并持久化。
    Returns:
        (private_key_pem, public_key_pem)
    """
    try:
        from l3_node.early_log import trace
        trace("bootstrap ensure_identity: JACHIN_DIR=%s IDENTITY_PATH=%s exists=%s", _JACHIN_DIR, _IDENTITY_PATH, _IDENTITY_PATH.exists())
    except ImportError:
        pass
    from l3_node.crypto import generate_rsa_keypair, public_key_from_private

    _JACHIN_DIR.mkdir(parents=True, exist_ok=True)
    if _IDENTITY_PATH.exists():
        try:
            data = json.loads(_IDENTITY_PATH.read_text(encoding="utf-8"))
            priv = data.get("private_key_pem")
            if priv:
                pub = public_key_from_private(priv)
                return priv, pub
        except Exception as e:
            logger.warning("[L3 Identity] 加载失败，重新生成: %s", e)

    private_pem, public_pem = generate_rsa_keypair()
    _IDENTITY_PATH.write_text(
        json.dumps({"private_key_pem": private_pem, "public_key_pem": public_pem}, indent=2),
        encoding="utf-8",
    )
    if hasattr(os, "chmod"):
        try:
            _IDENTITY_PATH.chmod(0o600)
        except OSError:
            pass
    return private_pem, public_pem


async def bootstrap_l3_gateway_pending(
    l2_base_url: str,
    *,
    model_name: str = "gpt-4o-mini",
    poll_interval: float = 3.0,
    poll_timeout: float = 600.0,
    on_status: Optional[Callable[[str, str], None]] = None,
) -> tuple[LiteLLMEngine, str]:
    """
    L3 零信任网关配对：注册 → 轮询审批 → 解密 Key → 点火。

    Args:
        l2_base_url: L2 网关地址
        on_status: 可选回调 (status, message)，用于 UI 展示（如 "pending"/"等待审批"）

    Returns:
        (engine, node_id)
    """
    import httpx

    private_pem, public_pem = ensure_identity()
    device_fp = _device_fingerprint()

    base = l2_base_url.rstrip("/")
    sync_url = f"{base}/api/v2/auth/sync"
    poll_url = f"{base}/api/v2/auth/poll"

    # 设备名与 node_id：从配置文件读取，避免同一设备重复注册产生多个待审批节点
    try:
        from l3_node.early_log import trace
        trace("bootstrap: GATEWAY_CONFIG_PATH=%s exists=%s", _GATEWAY_CONFIG_PATH, _GATEWAY_CONFIG_PATH.exists())
    except ImportError:
        pass
    cfg_early: dict[str, Any] = {}
    if _GATEWAY_CONFIG_PATH.exists():
        try:
            cfg_early = json.loads(_GATEWAY_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg_early = {}

    display_name = (os.environ.get("JACHIN_DEVICE_NAME") or "").strip()[:64]
    if not display_name:
        display_name = (cfg_early.get("display_name") or "").strip()[:64]
    existing_node_id: Optional[str] = None
    nid = cfg_early.get("node_id")
    if isinstance(nid, str) and nid.strip().startswith("l3-"):
        existing_node_id = nid.strip()

    organization_id = (
        os.environ.get("JACHIN_ORGANIZATION_ID") or cfg_early.get("organization_id") or ""
    ).strip()
    organization_slug = (
        os.environ.get("JACHIN_ORGANIZATION_SLUG") or cfg_early.get("organization_slug") or ""
    ).strip().lower()
    workspace_name_cfg = (
        os.environ.get("JACHIN_WORKSPACE_NAME") or cfg_early.get("workspace_name") or ""
    ).strip()[:128]

    sync_body: dict[str, Any] = {
        "device_fingerprint": device_fp,
        "public_key_pem": public_pem,
        "capabilities": [],
    }
    if display_name:
        sync_body["display_name"] = display_name
    if existing_node_id:
        sync_body["node_id"] = existing_node_id  # 复用已有节点，避免 L2 出现多个待审批
    if organization_id:
        sync_body["organization_id"] = organization_id
    if organization_slug:
        sync_body["organization_slug"] = organization_slug
    if workspace_name_cfg:
        sync_body["workspace_name"] = workspace_name_cfg

    # 1. POST /auth/sync（无 sub_account_id，待审批）
    # trust_env=False：绕过系统代理，避免 localhost 请求被错误转发导致 ReadTimeout
    # 503 时重试（L2 启动中或依赖未就绪）；ReadTimeout 时重试（代理/网络慢）
    data: Optional[dict] = None
    for attempt in range(1, 5):
        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                resp = await client.post(sync_url, json=sync_body)
                if resp.status_code == 503:
                    if attempt < 4:
                        wait = 2.0 * attempt
                        logger.info("[L3 Gateway] L2 返回 503，%ds 后重试 (%d/4)", int(wait), attempt)
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503 and attempt < 4:
                wait = 2.0 * attempt
                logger.info("[L3 Gateway] L2 返回 503，%ds 后重试 (%d/4)", int(wait), attempt)
                await asyncio.sleep(wait)
                continue
            if e.response.status_code == 403:
                hint = "403 Forbidden"
                try:
                    j = e.response.json()
                    if isinstance(j, dict):
                        d = j.get("detail")
                        if isinstance(d, dict) and d.get("message"):
                            hint = str(d["message"])
                        elif isinstance(d, str):
                            hint = d
                        elif j.get("message"):
                            hint = str(j["message"])
                except Exception:
                    hint = e.response.text or hint
                raise RuntimeError(
                    f"L2 拒绝 auth/sync（403）：{hint}\n"
                    "已配对 L2 时：organization_id 须落在 nexus_config 的 sync_tenant_ids，"
                    "或使用 organization_slug（L1 已设 slug）；亦可配置 JACHIN_ORGANIZATION_ID / "
                    "JACHIN_ORGANIZATION_SLUG 或 ~/.jachin/l2_gateway_config.json。"
                ) from e
            if e.response.status_code == 400:
                hint = "400 Bad Request"
                try:
                    j = e.response.json()
                    if isinstance(j, dict):
                        d = j.get("detail")
                        if isinstance(d, dict) and d.get("message"):
                            hint = str(d["message"])
                        elif isinstance(d, str):
                            hint = d
                        elif j.get("message"):
                            hint = str(j["message"])
                except Exception:
                    hint = e.response.text or hint
                raise RuntimeError(
                    f"L2 拒绝 auth/sync（400）：{hint}\n"
                    "常见原因：organization_slug 无法在 L1 解析（显示名/slug 不一致可多试 UUID），"
                    "或与 organization_id 同时填写。详见 ~/.jachin/l2_gateway_config.json。"
                ) from e
            raise
        except httpx.TimeoutException as e:
            if attempt < 4:
                wait = 3.0 * attempt
                logger.warning("[L3 Gateway] 连接 L2 超时 (%s)，%ds 后重试 (%d/4)", type(e).__name__, int(wait), attempt)
                await asyncio.sleep(wait)
                continue
            raise RuntimeError(
                f"连接 L2 超时（{type(e).__name__}），请确认 L2 已启动且网络可达。"
                " 若使用代理，可尝试关闭代理或增加超时。"
            ) from e
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"无法连接 L2（{base}）。新机器或 L2 未启动时，请使用独立模式：\n"
                f"  .\\scripts\\run_l3.ps1 --ws-only\n"
                f"  或  python -m l3_node --ws-only\n"
                f"（需 .env 配置 DASHSCOPE_API_KEY，无 MCP/Skill 订阅能力）"
            ) from e
    if not data:
        raise RuntimeError("L2 持续返回 503，请确保 L2 已启动（运行 启动后端.bat 或 python -m core.main）")
    node_id = data.get("node_id") or "l3-unknown"
    logger.info("[L3 Gateway] 已向 L2 宣誓效忠 node_id=%s，等待管理员审批", node_id)

    # 持久化 node_id 供下次使用
    _JACHIN_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(cfg_early)
    cfg["l2_base_url"] = base
    cfg["node_id"] = node_id
    cfg["paired"] = False  # 审批通过后设为 True
    if organization_id:
        cfg["organization_id"] = organization_id
    if organization_slug:
        cfg["organization_slug"] = organization_slug
    if workspace_name_cfg:
        cfg["workspace_name"] = workspace_name_cfg
    _GATEWAY_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    if on_status:
        on_status("pending", "请求已发送，等待 L2 节点管理员审批...")

    # 2. 轮询 /auth/poll（trust_env=False 绕过代理）
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                r = await client.get(poll_url, params={"node_id": node_id})
                r.raise_for_status()
                poll_data = r.json()
        except Exception as e:
            logger.debug("[L3 Gateway] 轮询异常: %s", e)
            continue

        st = poll_data.get("status")
        if st == "pending":
            if on_status:
                on_status("pending", poll_data.get("message", "等待审批..."))
            continue

        if st == "approved":
            encrypted_keys = poll_data.get("encrypted_api_keys", [])
            sub_account_id = poll_data.get("sub_account_id", "")
            logger.info("[L3 Gateway] auth/poll 返回 approved encrypted_keys=%d sub_account_id=%s", len(encrypted_keys), (sub_account_id or "")[:20] + ("..." if len(sub_account_id or "") > 20 else ""))
            break
        logger.warning("[L3 Gateway] 未知状态: %s", st)
    else:
        raise TimeoutError("配对超时：L2 管理员未在限定时间内审批")

    # 3. 解密 Key，创建引擎
    provider_alias = {"qwen": "dashscope", "openai": "openai", "dashscope": "dashscope"}
    ctx = SecurityContext()
    for item in encrypted_keys:
        provider = (item.get("provider") or "").lower()
        enc = item.get("encrypted_key", "")
        if not provider or not enc:
            continue
        try:
            plain = _decrypt_fn(enc, private_pem)
            norm = provider_alias.get(provider, provider)
            ctx.set_key(norm, plain)
            if norm != provider:
                ctx.set_key(provider, plain)
            logger.debug("[L3 Gateway] 解密 Key 成功 provider=%s", provider)
        except Exception as e:
            logger.warning("[L3 Gateway] 解密 %s 失败: %s", provider, e)

    # 若 L2 未分配 Key 或解密失败，从 .env 兜底注入
    if not ctx.has_any_key():
        logger.info("[L3 Gateway] L2 未分配 Key 或解密失败，尝试从 .env 兜底注入")
        _inject_env_keys_into_ctx(ctx)
    if ctx.has_any_key():
        logger.info("[L3 Gateway] 已分配到 API Key providers=%s", list(ctx._keys.keys()))
    else:
        logger.warning("[L3 Gateway] 未获取到 API Key，聊天将不可用。请检查 L2 控制台或 .env 中的 DASHSCOPE_API_KEY")

    model_endpoints = poll_data.get("model_endpoints") or {}
    if isinstance(model_endpoints, dict) and model_endpoints:
        first_model = next(iter(model_endpoints.values()), None)
        if first_model and isinstance(first_model, str):
            model_name = first_model

    # 有 dashscope（L2 或 .env）时用 qwen，避免回退到未启动的 Ollama
    has_dashscope = ctx.get_key("dashscope") or os.environ.get("DASHSCOPE_API_KEY")
    if has_dashscope:
        try:
            from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

            fallback = [DASHSCOPE_ECON_FALLBACK_MODEL]
        except ImportError:
            fallback = ["dashscope/qwen3.5-flash-2026-02-23"]
        if "gpt" in (model_name or "").lower() or "openai" in (model_name or "").lower():
            model_name = os.environ.get("LLM_MODEL", "qwen3.5-plus")
    else:
        fallback = None
    _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
    logger.debug("[L3 Gateway] 创建引擎 model=%s fallback=%s timeout=%s ctx_has_key=%s", model_name, fallback, _timeout, ctx.has_any_key())
    try:
        from l3_node.early_log import trace
        trace("bootstrap: creating LiteLLMEngine model=%s", model_name)
    except ImportError:
        pass
    engine = LiteLLMEngine(
        security_context=ctx,
        model_name=model_name,
        fallback_models=fallback,
        timeout=_timeout,
        max_attempts=2,
    )
    from core.wasm_runner import register_host_services
    register_host_services(llm_engine=engine, l2_base_url=base)

    cfg["paired"] = True
    cfg["sub_account_id"] = sub_account_id
    cfg["permissions_snapshot"] = poll_data.get("permissions_snapshot") or {}
    cfg["model_endpoints"] = model_endpoints
    if display_name:
        cfg["display_name"] = display_name
    if not cfg.get("l3_http_url"):
        cfg["l3_http_url"] = "http://127.0.0.1:18991"
    _GATEWAY_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    # L3 获批后立即从 L2 同步技能到 l3_skill_cache（不依赖 Desktop perform_startup_sync）
    try:
        from l3_node.skill_sync import sync_skills_from_l2
        logger.info("[L3 Gateway] 开始技能同步...")
        synced, skipped, failed_list = sync_skills_from_l2()
        logger.info("[L3 Gateway] 技能同步结果 synced=%d skipped=%d failed=%d", synced, skipped, len(failed_list))
        if failed_list:
            for f in failed_list[:5]:
                logger.warning("[L3 Gateway] 同步失败: %s", f)
    except Exception as e:
        logger.warning("[L3 Gateway] 技能同步异常: %s", e, exc_info=True)

    # L3 获批后从 L2 同步 L3_LOCAL MCP 到 l3_mcp_cache
    try:
        from l3_node.mcp_sync import sync_mcps_from_l2
        mcp_synced, mcp_skipped, mcp_failed = sync_mcps_from_l2()
        logger.info("[L3 Gateway] MCP 同步结果 synced=%d skipped=%d failed=%d", mcp_synced, mcp_skipped, len(mcp_failed))
    except Exception as e:
        logger.warning("[L3 Gateway] MCP 同步异常: %s", e, exc_info=True)

    try:
        from l3_node.mcp_stdio_bootstrap import start_l3_stdio_mcp_host

        await start_l3_stdio_mcp_host()
    except Exception as e:
        logger.warning("[L3 Gateway] L3 stdio MCP 宿主启动异常: %s", e, exc_info=True)

    if on_status:
        on_status("approved", "神经接驳成功，引擎已点火")

    return engine, node_id


async def bootstrap_l3_node(
    l2_base_url: str,
    sub_account_id: str,
    *,
    node_id: Optional[str] = None,
    device_fingerprint: str = "",
    model_name: str = "gpt-4o-mini",
    start_memory_sync: bool = True,
    memory_sync_interval: float = 300.0,
) -> tuple[LiteLLMEngine, Optional[MemorySyncDaemon], str]:
    """
    引导 L3 节点：注册、拉取 Key、解密、创建引擎。

    Returns:
        (engine, memory_daemon, node_id)
    """
    from l3_node.crypto import generate_rsa_keypair

    private_pem, public_pem = generate_rsa_keypair()

    import httpx
    auth_url = f"{l2_base_url.rstrip('/')}/api/v2/auth/sync"
    body = {
        "device_fingerprint": device_fingerprint,
        "public_key_pem": public_pem,
        "sub_account_id": sub_account_id,
        "node_id": node_id,
        "capabilities": [],
    }
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        resp = await client.post(auth_url, json=body)
        resp.raise_for_status()
        data = resp.json()
    resolved_node_id = data.get("node_id") or node_id or "l3-unknown"
    logger.info("[L3 Bootstrap] 已注册 node_id=%s", resolved_node_id)

    keys, model_endpoints = await fetch_and_decrypt_keys(
        l2_base_url, resolved_node_id, sub_account_id,
        private_pem, _decrypt_fn,
    )
    logger.debug("[L3 Bootstrap] 拉取 Key 结果 providers=%s model_endpoints=%s", list(keys.keys()), model_endpoints)
    if not keys:
        logger.warning("[L3 Bootstrap] 未获取到任何 API Key，将使用环境变量兜底")

    if isinstance(model_endpoints, dict) and model_endpoints:
        first_model = next(iter(model_endpoints.values()), None)
        if first_model and isinstance(first_model, str):
            model_name = first_model

    ctx = SecurityContext()
    for provider, plain in keys.items():
        ctx.set_key(provider, plain)

    if ctx.get_key("dashscope"):
        try:
            from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

            fallback = [DASHSCOPE_ECON_FALLBACK_MODEL]
        except ImportError:
            fallback = ["dashscope/qwen3.5-flash-2026-02-23"]
    else:
        fallback = None
    _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
    logger.debug("[L3 Bootstrap] 创建引擎 model=%s fallback=%s timeout=%s ctx_has_key=%s", model_name, fallback, _timeout, ctx.has_any_key())
    engine = LiteLLMEngine(
        security_context=ctx,
        model_name=model_name,
        fallback_models=fallback,
        timeout=_timeout,
        max_attempts=2,
    )
    from core.wasm_runner import register_host_services
    register_host_services(llm_engine=engine, l2_base_url=l2_base_url)

    memory_daemon: Optional[MemorySyncDaemon] = None
    if start_memory_sync:
        memory_daemon = MemorySyncDaemon(
            l2_base_url=l2_base_url,
            sub_account_id=sub_account_id,
            node_id=resolved_node_id,
            interval_seconds=memory_sync_interval,
        )
        memory_daemon.start()

    try:
        from l3_node.mcp_stdio_bootstrap import start_l3_stdio_mcp_host

        await start_l3_stdio_mcp_host()
    except Exception as e:
        logger.warning("[L3 Bootstrap] stdio MCP 宿主启动异常: %s", e, exc_info=True)

    return engine, memory_daemon, resolved_node_id


async def run_l3_agent(
    user_input: str,
    engine: LiteLLMEngine,
    **kwargs,
) -> str:
    """运行 L3 单主轴 ReAct（run_agent）；架构见 docs/architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md。"""
    return await run_agent(user_input, engine, **kwargs)


async def heartbeat_loop(
    l2_base_url: str,
    node_id: str,
    *,
    interval_sec: float = 60.0,
) -> None:
    """
    L3 心跳后台任务：定期调用 L2 /api/v2/auth/heartbeat 更新 last_seen_at，
    使 JachinLink 等能正确展示在线状态。
    """
    import httpx

    base = l2_base_url.rstrip("/")
    url = f"{base}/api/v2/auth/heartbeat"
    first = True
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                r = await client.get(url, params={"node_id": node_id})
                if r.is_success:
                    if first:
                        logger.info("[L3 Heartbeat] 心跳已发送，JachinLink 将显示在线")
                        first = False
                else:
                    logger.warning(
                        "[L3 Heartbeat] L2 心跳失败 HTTP %s %s (与全息监控无关，为 L3->L2 认证)",
                        r.status_code,
                        r.text[:80] if r.text else "",
                    )
        except Exception as e:
            logger.warning("[L3 Heartbeat] L2 不可达 %s (与全息监控无关，为 L3->L2 认证)", e)
        await asyncio.sleep(interval_sec)
