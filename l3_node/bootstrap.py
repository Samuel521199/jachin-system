"""
Jachin Nexus V2 - L3 节点引导

1. 生成 RSA 密钥对（或加载已持久化的）
2. 向 L2 注册 (POST /api/v2/auth/sync)
3. 轮询 GET /api/v2/auth/poll 等待管理员审批
4. 拉取密文 Key，本地解密，填充 SecurityContext
5. 创建 LiteLLMEngine，可选启动 MemorySyncDaemon
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Callable, Optional

from l3_node.agent_core import MemorySyncDaemon, run_agent
from l3_node.llm_client import (
    LiteLLMEngine,
    SecurityContext,
    fetch_and_decrypt_keys,
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

    # 1. POST /auth/sync（无 sub_account_id，待审批）
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            sync_url,
            json={
                "device_fingerprint": device_fp,
                "public_key_pem": public_pem,
                "capabilities": [],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    node_id = data.get("node_id") or "l3-unknown"
    logger.info("[L3 Gateway] 已向 L2 宣誓效忠 node_id=%s，等待管理员审批", node_id)

    # 持久化 node_id 供下次使用
    _JACHIN_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if _GATEWAY_CONFIG_PATH.exists():
        try:
            cfg = json.loads(_GATEWAY_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    cfg["l2_base_url"] = base
    cfg["node_id"] = node_id
    cfg["paired"] = False  # 审批通过后设为 True
    _GATEWAY_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    if on_status:
        on_status("pending", "请求已发送，等待 L2 节点管理员审批...")

    # 2. 轮询 /auth/poll
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
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
        except Exception as e:
            logger.warning("[L3 Gateway] 解密 %s 失败: %s", provider, e)

    engine = LiteLLMEngine(
        security_context=ctx,
        model_name=model_name,
        fallback_models=["ollama/qwen2.5"],
        timeout=60.0,
        max_attempts=2,
    )

    cfg["paired"] = True
    _GATEWAY_CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

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
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(auth_url, json=body)
        resp.raise_for_status()
        data = resp.json()
    resolved_node_id = data.get("node_id") or node_id or "l3-unknown"
    logger.info("[L3 Bootstrap] 已注册 node_id=%s", resolved_node_id)

    keys = await fetch_and_decrypt_keys(
        l2_base_url, resolved_node_id, sub_account_id,
        private_pem, _decrypt_fn,
    )
    if not keys:
        logger.warning("[L3 Bootstrap] 未获取到任何 API Key，将使用环境变量兜底")

    ctx = SecurityContext()
    for provider, plain in keys.items():
        ctx.set_key(provider, plain)

    engine = LiteLLMEngine(
        security_context=ctx,
        model_name=model_name,
        fallback_models=["ollama/qwen2.5"],
        timeout=60.0,
        max_attempts=2,
    )

    memory_daemon: Optional[MemorySyncDaemon] = None
    if start_memory_sync:
        memory_daemon = MemorySyncDaemon(
            l2_base_url=l2_base_url,
            sub_account_id=sub_account_id,
            node_id=resolved_node_id,
            interval_seconds=memory_sync_interval,
        )
        memory_daemon.start()

    return engine, memory_daemon, resolved_node_id


async def run_l3_agent(
    user_input: str,
    engine: LiteLLMEngine,
    **kwargs,
) -> str:
    """运行 L3 单体 Agent。"""
    return await run_agent(user_input, engine, **kwargs)
