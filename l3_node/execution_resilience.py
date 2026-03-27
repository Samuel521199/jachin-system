"""
执行韧性共享工具：错误分类、RunReport 结构体与落盘。
规范见 docs/JACHIN_EXECUTION_RESILIENCE_CONTRACT.md。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("l3_node")

ERROR_TRANSIENT = "transient"
ERROR_RESOURCE = "resource"
ERROR_PER_ITEM = "per_item"
ERROR_CONFIG = "config"
ERROR_PERMANENT = "permanent"


def classify_wasm_error_message(msg: str) -> str:
    """将 Wasm/宿主错误串映射到契约 §3 类别（启发式）。"""
    if not msg:
        return ERROR_TRANSIENT
    lower = msg.lower()
    if (
        "linear_memory_oob" in lower
        or "unreachable" in lower
        or ("memory" in lower and "out" in lower)
        or "out of memory" in lower
        or "oom" in lower
    ):
        return ERROR_RESOURCE
    if "timeout" in lower or "timed out" in lower or "429" in lower:
        return ERROR_TRANSIENT
    if "connection" in lower or "econnreset" in lower or "broken pipe" in lower:
        return ERROR_TRANSIENT
    if (
        "401" in msg
        or "403" in msg
        or "credential" in lower
        or "api key" in lower
        or "未配置" in msg
        or "permission" in lower
    ):
        return ERROR_CONFIG
    if "trap" in lower and "wasm" in lower:
        return ERROR_RESOURCE
    return ERROR_TRANSIENT


def build_run_report(
    *,
    status: str,
    ok_count: int,
    failed_items: list[dict[str, Any]],
    degraded: bool = False,
    fallback_used: str | None = None,
    artifacts: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rep: dict[str, Any] = {
        "status": status,
        "ok_count": ok_count,
        "failed_items": failed_items,
        "degraded": degraded,
        "fallback_used": fallback_used,
        "artifacts": list(artifacts or []),
    }
    if extra:
        rep.update(extra)
    return rep


def write_run_report_json(output_dir: Path, report: dict[str, Any]) -> None:
    """写入 result/_run_report.json（先写临时文件再 replace，尽量避免半写）。"""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        p = output_dir / "_run_report.json"
        tmp = output_dir / "_run_report.json.tmp"
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        logger.warning("[RunReport] 写入失败 %s: %s", output_dir, e)


def log_execution_brief(*, domain: str, goal: str, outcome: str, message: str) -> None:
    """原则 C：有界退出时统一日志前缀，便于检索。"""
    logger.warning(
        "[ExecutionBrief] domain=%s goal=%s outcome=%s %s",
        domain,
        goal,
        outcome,
        message[:500],
    )
