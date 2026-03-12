"""
Jachin L2 - 遥测数据打包器 (Batcher)

提取尚未上报的 usage_telemetry 日志，转换为压缩 JSON，供 L1 上传。
上报前对 sub_account_id 进行 SHA-256 哈希脱敏，保护企业员工隐私。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _hash_sub_account_id(raw: str | None) -> str | None:
    """对 sub_account_id 进行 SHA-256 哈希脱敏，保护员工隐私。"""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    return hashlib.sha256(s.encode("utf-8")).hexdigest().lower()


def get_unreported_logs(limit: int = 10_000) -> tuple[bytes, list[str]]:
    """
    提取尚未上报的遥测日志，转换为 gzip 压缩的 JSON 字节流。

    Args:
        limit: 单次最多提取条数，默认 10000

    Returns:
        (compressed_bytes, ids): 压缩后的 JSON 字节、本批次记录的 id 列表。
        调用方上传成功后应调用 mark_reported(ids) 标记已上报。
    """
    from core.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, sub_account_id, item_id, action_name, status, latency_ms, timestamp
            FROM usage_telemetry
            WHERE reported = 0
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not rows:
            return gzip.compress(b"[]", compresslevel=6), []

        ids = [r[0] for r in rows]
        payload = [
            {
                "id": r[0],
                "sub_account_id": _hash_sub_account_id(r[1]),
                "item_id": r[2],
                "action_name": r[3],
                "status": r[4],
                "latency_ms": r[5],
                "timestamp": r[6],
            }
            for r in rows
        ]

        json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        compressed = gzip.compress(json_bytes, compresslevel=6)
        logger.info("[Telemetry Batcher] 提取 %d 条未上报日志，压缩后 %d bytes", len(ids), len(compressed))
        return compressed, ids
    finally:
        conn.close()


def mark_reported(ids: list[str]) -> int:
    """
    将指定 id 的日志标记为已上报。

    Args:
        ids: 已成功上报的 usage_telemetry.id 列表

    Returns:
        实际更新的行数
    """
    if not ids:
        return 0

    from core.db import get_connection

    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"UPDATE usage_telemetry SET reported = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        n = cur.rowcount
        logger.debug("[Telemetry Batcher] 已标记 %d 条为已上报", n)
        return n
    finally:
        conn.close()
