"""
Jachin Nexus V2 - L3 节点 Redis 共享状态（无状态 L2 集群化）

- l3_node_status:{node_id}: Hash，TTL 60s（l3_http_url、mcp_tools_json、sub_account_id）
- l3_task_queue:{node_id}: coordinate 子任务 RPOP
- l3_mcp_delegate_queue:{node_id}: MCP 跨节点委托（Pull）；载荷含 task_token（见 core/mcp_task_token.py）

l3_http_url 仅用于 **NAT 降级** HTTP 入站（须 task_token）；主路径为 Pull。Redis 不可用时 v2_mcp 仅 HTTP 降级并提示不可靠。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from core.redis_manager import get_redis_client

logger = logging.getLogger(__name__)

_L3_STATUS_TTL = 60
_L3_STATUS_PREFIX = "l3_node_status:"
_L3_QUEUE_PREFIX = "l3_task_queue:"
# MCP 跨节点「拉取」下行队列（与 coordinate 的 l3_task_queue 隔离）
_MCP_DELEGATE_QUEUE_PREFIX = "l3_mcp_delegate_queue:"
_MCP_DELEGATE_RESULT_PREFIX = "l3_mcp_delegate_result:"
_MCP_DELEGATE_LEASE_PREFIX = "l3_mcp_delegate_lease:"


def write_l3_node_status(
    node_id: str,
    sub_account_id: str,
    capabilities_json: str = "{}",
    trust_zone: str = "",
    l3_http_url: str = "",
    mcp_tools_json: str = "[]",
) -> bool:
    """
    L3 调用 poll 或心跳时，将在线状态写入 Redis。
    TTL 60 秒，超时未心跳则视为离线。
    l3_http_url: L3 HTTP API（NAT 降级：POST /api/v3/mcp/execute，须带 L2 签发的 task_token）。
    mcp_tools_json: 该 L3 可执行的 MCP 工具列表 JSON，如 ["atom_web_scraper","read_file"]。
    """
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"{_L3_STATUS_PREFIX}{node_id}"
        mapping = {
            "last_seen_at": str(time.time()),
            "capabilities_json": capabilities_json or "{}",
            "sub_account_id": sub_account_id or "",
            "trust_zone": trust_zone or "",
        }
        if l3_http_url:
            mapping["l3_http_url"] = l3_http_url.strip()
        if mcp_tools_json:
            mapping["mcp_tools_json"] = mcp_tools_json
        client.hset(key, mapping=mapping)
        client.expire(key, _L3_STATUS_TTL)
        return True
    except Exception as e:
        logger.debug("[L3Redis] write_l3_node_status 失败: %s", e)
        return False


def get_l3_nodes_with_mcp_tool(
    sub_account_id: str,
    tool_name: str,
    *,
    require_l3_http_url: bool = True,
) -> list[dict[str, Any]]:
    """
    从 Redis 获取具备指定 MCP 工具的在线 L3 节点（供 v2_mcp 委托）。
    返回 [{node_id, l3_http_url, last_seen_at}, ...]，按 last_seen_at 降序。
    tool_name 支持带或不带 mcp: 前缀。
    require_l3_http_url=False 时包含无入站 URL 的节点（仅靠 **Pull 队列** 消费，见 push_mcp_delegate_task）。
    """
    client = get_redis_client()
    if not client:
        return []
    tool_raw = (tool_name or "").strip().replace("mcp:", "", 1).strip()
    if not tool_raw:
        return []
    now = time.time()
    cutoff = now - _L3_STATUS_TTL * 2
    try:
        keys = list(client.scan_iter(match=f"{_L3_STATUS_PREFIX}*", count=100))
        out: list[dict[str, Any]] = []
        for k in keys:
            node_id = k[len(_L3_STATUS_PREFIX):]
            data = client.hgetall(k)
            if not data:
                continue
            if str(data.get("sub_account_id", "")) != sub_account_id:
                continue
            try:
                last_seen = float(data.get("last_seen_at", 0))
                if last_seen < cutoff:
                    continue
                url = (data.get("l3_http_url") or "").strip()
                if require_l3_http_url and not url:
                    continue
                mcp_raw = data.get("mcp_tools_json") or "[]"
                tools_list = json.loads(mcp_raw) if isinstance(mcp_raw, (str, bytes)) else mcp_raw
                if not isinstance(tools_list, list):
                    continue
                has_tool = any(
                    (str(t).strip().replace("mcp:", "", 1).strip() == tool_raw)
                    for t in tools_list
                )
                if has_tool:
                    out.append({"node_id": node_id, "l3_http_url": url, "last_seen_at": last_seen})
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        out.sort(key=lambda x: -x.get("last_seen_at", 0))
        return out
    except Exception as e:
        logger.debug("[L3Redis] get_l3_nodes_with_mcp_tool 失败: %s", e)
        return []


def aggregate_mcp_tools_catalog_from_redis() -> list[dict[str, Any]]:
    """
    合并所有 Redis 在线 L3 节点上报的 ``mcp_tools_json`` 为扁平工具目录。
    供 L2 ``GET /api/v2/mcp/tools`` 在「无本机 stdio MCP」时展示边绦能力（仅名称级聚合）。
    """
    client = get_redis_client()
    if not client:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    try:
        keys = list(client.scan_iter(match=f"{_L3_STATUS_PREFIX}*", count=256))
        for k in keys:
            data = client.hgetall(k)
            if not data:
                continue
            raw = data.get("mcp_tools_json") or "[]"
            try:
                tools_list = json.loads(raw) if isinstance(raw, (str, bytes)) else []
            except json.JSONDecodeError:
                continue
            if not isinstance(tools_list, list):
                continue
            for t in tools_list:
                name = str(t).strip().replace("mcp:", "", 1).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append({
                    "name": name,
                    "description": f"[L3 edge] {name}",
                    "inputSchema": {"type": "object", "properties": {}},
                })
    except Exception as e:
        logger.debug("[L3Redis] aggregate_mcp_tools_catalog_from_redis 失败: %s", e)
    return out


def get_online_l3_nodes_for_sub_account(sub_account_id: str) -> list[dict[str, Any]]:
    """
    从 Redis 获取指定子账号下所有在线 L3 节点。
    返回 [{node_id, last_seen_at, capabilities_json, trust_zone, l3_http_url, mcp_tools_json}, ...]
    """
    client = get_redis_client()
    if not client:
        return []
    try:
        keys = list(client.scan_iter(match=f"{_L3_STATUS_PREFIX}*", count=100))
        out: list[dict[str, Any]] = []
        for k in keys:
            node_id = k[len(_L3_STATUS_PREFIX):]
            data = client.hgetall(k)
            if not data:
                continue
            if str(data.get("sub_account_id", "")) != sub_account_id:
                continue
            try:
                last_seen = float(data.get("last_seen_at", 0))
                caps = data.get("capabilities_json") or "{}"
                tz = data.get("trust_zone") or ""
                entry = {
                    "node_id": node_id,
                    "last_seen_at": last_seen,
                    "capabilities_json": caps,
                    "trust_zone": tz,
                }
                if data.get("l3_http_url"):
                    entry["l3_http_url"] = str(data.get("l3_http_url", "")).strip()
                if data.get("mcp_tools_json"):
                    entry["mcp_tools_json"] = str(data.get("mcp_tools_json", "[]"))
                out.append(entry)
            except (ValueError, TypeError):
                continue
        return out
    except Exception as e:
        logger.debug("[L3Redis] get_online_l3_nodes 失败: %s", e)
        return []


def push_subtask_to_queue(node_id: str, payload: dict[str, Any]) -> bool:
    """将子任务 Payload 推入目标 L3 的 Redis 队列（LPUSH）。"""
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"{_L3_QUEUE_PREFIX}{node_id}"
        client.lpush(key, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        logger.debug("[L3Redis] push_subtask_to_queue 失败: %s", e)
        return False


def pop_subtasks_from_queue(node_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    从 Redis 队列 RPOP 出最多 limit 条子任务。
    返回解析后的 payload 列表。
    """
    client = get_redis_client()
    if not client:
        return []
    try:
        key = f"{_L3_QUEUE_PREFIX}{node_id}"
        out: list[dict[str, Any]] = []
        for _ in range(limit):
            raw = client.rpop(key)
            if not raw:
                break
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out
    except Exception as e:
        logger.debug("[L3Redis] pop_subtasks_from_queue 失败: %s", e)
        return []


def push_mcp_delegate_task(node_id: str, payload: dict[str, Any]) -> bool:
    """将 MCP 代跑任务 LPUSH 到目标节点的下行队列（L3 经 L2 HTTP poll RPOP）。"""
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"{_MCP_DELEGATE_QUEUE_PREFIX}{node_id}"
        client.lpush(key, json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        logger.debug("[L3Redis] push_mcp_delegate_task 失败: %s", e)
        return False


def pop_mcp_delegate_tasks(node_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """RPOP 最多 limit 条 MCP 代跑任务。"""
    client = get_redis_client()
    if not client:
        return []
    try:
        key = f"{_MCP_DELEGATE_QUEUE_PREFIX}{node_id}"
        out: list[dict[str, Any]] = []
        for _ in range(limit):
            raw = client.rpop(key)
            if not raw:
                break
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out
    except Exception as e:
        logger.debug("[L3Redis] pop_mcp_delegate_tasks 失败: %s", e)
        return []


def set_mcp_delegate_lease(task_id: str, node_id: str, sub_account_id: str, ttl_sec: int = 90) -> bool:
    """领取任务后写入租约，防止任意子账号伪造 result。"""
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"{_MCP_DELEGATE_LEASE_PREFIX}{task_id}"
        body = json.dumps({"node_id": node_id, "sub_account_id": sub_account_id}, ensure_ascii=False)
        client.setex(key, ttl_sec, body)
        return True
    except Exception as e:
        logger.debug("[L3Redis] set_mcp_delegate_lease 失败: %s", e)
        return False


def get_mcp_delegate_lease(task_id: str) -> Optional[dict[str, Any]]:
    client = get_redis_client()
    if not client:
        return None
    try:
        key = f"{_MCP_DELEGATE_LEASE_PREFIX}{task_id}"
        raw = client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception as e:
        logger.debug("[L3Redis] get_mcp_delegate_lease 失败: %s", e)
        return None


def delete_mcp_delegate_lease(task_id: str) -> None:
    client = get_redis_client()
    if not client:
        return
    try:
        client.delete(f"{_MCP_DELEGATE_LEASE_PREFIX}{task_id}")
    except Exception as e:
        logger.debug("[L3Redis] delete_mcp_delegate_lease 失败: %s", e)


def set_mcp_delegate_result(task_id: str, body: dict[str, Any], ttl_sec: int = 120) -> bool:
    """执行端回写结果，供 L2 invoke 侧轮询读取。"""
    client = get_redis_client()
    if not client:
        return False
    try:
        key = f"{_MCP_DELEGATE_RESULT_PREFIX}{task_id}"
        client.setex(key, ttl_sec, json.dumps(body, ensure_ascii=False))
        return True
    except Exception as e:
        logger.debug("[L3Redis] set_mcp_delegate_result 失败: %s", e)
        return False


def get_mcp_delegate_result(task_id: str) -> Optional[dict[str, Any]]:
    client = get_redis_client()
    if not client:
        return None
    try:
        key = f"{_MCP_DELEGATE_RESULT_PREFIX}{task_id}"
        raw = client.get(key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception as e:
        logger.debug("[L3Redis] get_mcp_delegate_result 失败: %s", e)
        return None
