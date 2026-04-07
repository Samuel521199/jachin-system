"""
L3：轮询 ``GET /api/v2/mcp/delegate/poll``，消费 Redis ``l3_mcp_delegate_queue:{node_id}``，
校验 ``task_token`` 后本地 ``mcp_registry.invoke``，再 ``POST .../delegate/result``。

需 Redis、配对子账号、与 L2 一致的 ``JACHIN_MCP_TASK_TOKEN_SECRET``。
并发上限 ``JACHIN_L3_MCP_DELEGATE_MAX_CONCURRENT``（默认 4）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GATEWAY_CFG = Path.home() / ".jachin" / "l2_gateway_config.json"


def _read_gateway_cfg() -> dict[str, Any]:
    if not _GATEWAY_CFG.exists():
        return {}
    try:
        data = json.loads(_GATEWAY_CFG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _max_concurrent() -> int:
    try:
        n = int(os.environ.get("JACHIN_L3_MCP_DELEGATE_MAX_CONCURRENT", "4"))
        return max(1, min(n, 32))
    except ValueError:
        return 4


async def _post_delegate_result(
    client: Any,
    base: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> None:
    url = f"{base}/api/v2/mcp/delegate/result"
    r = await client.post(url, json=body, headers=headers)
    r.raise_for_status()


async def _handle_one_mcp_delegate_task(
    sem: asyncio.Semaphore,
    task: dict[str, Any],
    *,
    l2_base_url: str,
    node_id: str,
    sub_account_id: str,
) -> None:
    async with sem:
        task_id = str(task.get("task_id") or "")
        tool_name = (task.get("tool_name") or "").strip()
        if not task_id or not tool_name:
            logger.warning("[MCP Pull] 无效任务 payload keys=%s", list(task.keys()))
            return
        if task.get("kind") != "mcp_delegate":
            logger.warning("[MCP Pull] 未知 kind=%s task_id=%s", task.get("kind"), task_id)
            return

        arguments = task.get("arguments") if isinstance(task.get("arguments"), dict) else {}
        action_input = json.dumps(arguments, ensure_ascii=False)
        tool_id = f"mcp:{tool_name}" if not tool_name.startswith("mcp:") else tool_name

        headers = {"X-Sub-Account-Id": sub_account_id, "Content-Type": "application/json"}
        base = l2_base_url.rstrip("/")

        try:
            from core.mcp_task_token import verify_mcp_delegate_task_token

            tok = str(task.get("task_token") or "").strip()
            allow_legacy = os.environ.get("JACHIN_MCP_DELEGATE_ALLOW_LEGACY_NO_TOKEN", "0").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            if not tok and not allow_legacy:
                logger.warning("[MCP Pull] 拒绝无 task_token 的代跑任务 task_id=%s", task_id)
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                        await _post_delegate_result(
                            client,
                            base,
                            headers,
                            {
                                "task_id": task_id,
                                "node_id": node_id,
                                "ok": False,
                                "error": "缺少 task_token 或令牌无效（请升级 L2 并配置 JACHIN_MCP_TASK_TOKEN_SECRET）",
                                "error_class": "config",
                            },
                        )
                except Exception:
                    pass
                return
            if tok:
                vok, vwhy = verify_mcp_delegate_task_token(
                    tok,
                    task_id=task_id,
                    tool_name=tool_name,
                    executor_node_id=node_id,
                    sub_account_id=sub_account_id,
                )
                if not vok:
                    logger.warning("[MCP Pull] task_token 校验失败 task_id=%s reason=%s", task_id, vwhy)
                    try:
                        import httpx

                        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                            await _post_delegate_result(
                                client,
                                base,
                                headers,
                                {
                                    "task_id": task_id,
                                    "node_id": node_id,
                                    "ok": False,
                                    "error": f"task_token 无效: {vwhy}",
                                    "error_class": "config",
                                },
                            )
                    except Exception:
                        pass
                    return
        except ImportError:
            logger.warning("[MCP Pull] 无法导入 core.mcp_task_token，拒绝执行（fail-closed）")
            try:
                import httpx

                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                    await _post_delegate_result(
                        client,
                        base,
                        headers,
                        {
                            "task_id": task_id,
                            "node_id": node_id,
                            "ok": False,
                            "error": "L3 无法加载 core.mcp_task_token，拒绝代跑",
                            "error_class": "config",
                        },
                    )
            except Exception:
                pass
            return

        try:
            import httpx
        except ImportError:
            logger.warning("[MCP Pull] httpx 未安装")
            return

        try:
            from l3_node.primitives.mcp.registry import get_mcp_registry

            registry = get_mcp_registry()
            result_text = await registry.invoke(
                tool_id,
                action_input,
                timeout=float(os.environ.get("JACHIN_MCP_DELEGATE_TOOL_TIMEOUT", "120")),
                allow_l2_delegate=False,
            )
            _fail = (
                "权限拒绝",
                "禁止再转发 L2",
                "【系统异常】",
                "本机未安装该工具，且当前处于跨节点代跑",
            )
            ok = not any(m in (result_text or "") for m in _fail)
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                await _post_delegate_result(
                    client,
                    base,
                    headers,
                    {
                        "task_id": task_id,
                        "node_id": node_id,
                        "ok": bool(ok),
                        "result": result_text if ok else None,
                        "error": None if ok else result_text,
                        "error_class": None if ok else "ExecutionFailed",
                    },
                )
            logger.info("[MCP Pull] 已回写 task_id=%s tool=%s ok=%s", task_id, tool_name, ok)
        except Exception as e:
            logger.warning("[MCP Pull] 执行或回写失败 task_id=%s err=%s", task_id, e)
            try:
                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                    await _post_delegate_result(
                        client,
                        base,
                        headers,
                        {
                            "task_id": task_id,
                            "node_id": node_id,
                            "ok": False,
                            "error": str(e),
                            "error_class": "ExecutionFailed",
                        },
                    )
            except Exception as e2:
                logger.warning("[MCP Pull] 失败回写仍失败 task_id=%s err=%s", task_id, e2)


async def run_mcp_delegate_pull_forever(
    *,
    poll_interval_sec: float = 0.8,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """后台协程：配对且具备子账号时持续轮询 L2 代跑队列。"""
    sem = asyncio.Semaphore(_max_concurrent())
    while stop_event is None or not stop_event.is_set():
        try:
            cfg = _read_gateway_cfg()
            if not cfg.get("paired"):
                await asyncio.sleep(max(poll_interval_sec, 2.0))
                continue
            node_id = (cfg.get("node_id") or "").strip()
            sub_account_id = (cfg.get("sub_account_id") or "").strip()
            l2_base_url = (cfg.get("l2_base_url") or os.environ.get("L2_BASE_URL") or "").strip()
            if not node_id or not sub_account_id or not l2_base_url:
                await asyncio.sleep(max(poll_interval_sec, 2.0))
                continue

            try:
                import httpx
            except ImportError:
                await asyncio.sleep(5.0)
                continue

            headers = {"X-Sub-Account-Id": sub_account_id}
            url = f"{l2_base_url.rstrip('/')}/api/v2/mcp/delegate/poll"
            async with httpx.AsyncClient(timeout=12.0, trust_env=False) as client:
                r = await client.get(url, params={"node_id": node_id, "limit": 2}, headers=headers)
                if not r.is_success:
                    await asyncio.sleep(poll_interval_sec)
                    continue
                data = r.json()
            tasks = data.get("tasks") or []
            if not isinstance(tasks, list):
                tasks = []
            for t in tasks:
                if isinstance(t, dict):
                    asyncio.create_task(
                        _handle_one_mcp_delegate_task(
                            sem,
                            t,
                            l2_base_url=l2_base_url,
                            node_id=node_id,
                            sub_account_id=sub_account_id,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[MCP Pull] 轮询异常: %s", e)
        await asyncio.sleep(poll_interval_sec)
