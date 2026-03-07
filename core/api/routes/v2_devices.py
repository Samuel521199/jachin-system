"""
Jachin Nexus V2 - 设备列表 API（供 JachinLink 控制面板）

GET /api/v2/devices: 返回 L3 节点列表，格式兼容原 DeviceRegistry 契约。
不依赖 Dapr/DeviceRegistry，直接查询 layer3_nodes 表。
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Query

from core.db import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["v2-devices"])

_OFFLINE_THRESHOLD_SEC = 300  # 5 分钟内无心跳视为离线


@router.get("/devices")
async def list_devices(
    online_only: bool = Query(
        default=True,
        description="仅返回在线设备；false 时返回全部已审批设备（含离线）",
    ),
) -> dict:
    """
    获取 L3 节点列表（设备列表），供 JachinLink 网络拓扑展示。
    默认仅返回在线设备（last_seen 5 分钟内），避免展示历史重复/离线脏数据。
    返回格式兼容前端 DeviceStatus 契约。
    """
    now = time.time()
    cutoff = now - _OFFLINE_THRESHOLD_SEC

    conn = get_connection()
    try:
        if online_only:
            rows = conn.execute(
                """
                SELECT id, device_fingerprint, capabilities_json, last_seen_at, display_name
                FROM l3_nodes
                WHERE sub_account_id IS NOT NULL AND last_seen_at > ?
                ORDER BY last_seen_at DESC
                """,
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, device_fingerprint, capabilities_json, last_seen_at, display_name
                FROM l3_nodes
                WHERE sub_account_id IS NOT NULL
                ORDER BY last_seen_at DESC
                """,
            ).fetchall()
    finally:
        conn.close()

    devices_list = []
    for r in rows:
        node_id = r[0]
        device_fingerprint = r[1] or ""
        caps_json = r[2] or "[]"
        last_seen = r[3]
        display_name = (r[4] or "").strip() if len(r) > 4 else ""

        try:
            caps = json.loads(caps_json) if caps_json else []
        except json.JSONDecodeError:
            caps = []

        last_seen_float = float(last_seen) if last_seen is not None else 0
        online = last_seen_float > cutoff

        capabilities = []
        if isinstance(caps, list):
            for c in caps:
                if isinstance(c, dict) and c.get("name"):
                    capabilities.append({"name": c["name"]})
                elif isinstance(c, str):
                    capabilities.append({"name": c})

        name = display_name or device_fingerprint or node_id or "未知设备"

        devices_list.append({
            "device_id": node_id,
            "device_type": "l3_node",
            "location": "",
            "capabilities": capabilities,
            "metadata": {"name": name},
            "timestamp": int(last_seen_float) if last_seen_float else None,
            "online": online,
        })

    return {
        "devices": devices_list,
        "total": len(devices_list),
        "online": sum(1 for d in devices_list if d["online"]),
    }
