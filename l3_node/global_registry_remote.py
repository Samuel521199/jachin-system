"""
GlobalTaskRegistry 跨进程/跨机抢占信号（路线图 §1 · resource_tags · 跨机 HTTP）

本机 `check_and_preempt` 标记 SQLite 后，若同进程 `request_cancel_run` 无效，
可向 `JACHIN_GLOBAL_REGISTRY_PEER_URLS` 中的 L3 节点发送取消请求。

环境变量
--------
JACHIN_GLOBAL_REGISTRY_PEER_URLS   逗号分隔 L3 HTTP 基址（与 Coordinator peers 同格式）
JACHIN_GLOBAL_REGISTRY_REMOTE_PREEMPT=1  开启远地取消（默认关）
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


def remote_preempt_enabled() -> bool:
    return (os.environ.get("JACHIN_GLOBAL_REGISTRY_REMOTE_PREEMPT") or "").strip().lower() in (
        "1", "true", "yes",
    )


def _peer_urls() -> list[str]:
    raw = (os.environ.get("JACHIN_GLOBAL_REGISTRY_PEER_URLS") or "").strip()
    if not raw:
        raw = (os.environ.get("JACHIN_COORDINATOR_PEER_URLS") or "").strip()
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def request_remote_cancel_run(run_id: str) -> dict[str, Any]:
    """向所有 peer POST preempt-cancel；返回汇总。"""
    rid = (run_id or "").strip()
    if not rid or not remote_preempt_enabled():
        return {"attempted": 0, "ok": 0}
    urls = _peer_urls()
    if not urls:
        return {"attempted": 0, "ok": 0, "error": "no_peer_urls"}
    try:
        import httpx
    except ImportError:
        return {"attempted": 0, "ok": 0, "error": "httpx_missing"}

    token = (
        os.environ.get("JACHIN_REGISTRY_DIAG_TOKEN")
        or os.environ.get("JACHIN_HOOK_EVENTS_READ_TOKEN")
        or ""
    ).strip()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["X-Jachin-Registry-Diag-Token"] = token
        headers["X-Jachin-Hook-Events-Token"] = token

    ok_n = 0
    for base in urls:
        url = urljoin(base + "/", "api/v1/registry/preempt-cancel")
        try:
            r = httpx.post(url, json={"run_id": rid}, headers=headers, timeout=3.0)
            if r.status_code < 300:
                ok_n += 1
        except Exception as e:
            logger.debug("[GlobalRegistryRemote] preempt POST %s failed: %s", base, e)
    return {"attempted": len(urls), "ok": ok_n, "run_id": rid}


def try_remote_preempt_after_local(run_ids: list[str]) -> None:
    """本地 cancel 后仍可对其它节点上的 run_id 发远地取消。"""
    if not remote_preempt_enabled() or not run_ids:
        return
    for rid in run_ids:
        summary = request_remote_cancel_run(rid)
        if summary.get("ok"):
            logger.info("[GlobalRegistryRemote] remote preempt ok run_id=%s %s", rid[:12], summary)
