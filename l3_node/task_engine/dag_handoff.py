"""
DAG 跨进程续跑转交（AR）—— HTTP Handoff

允许 L3 Node A 将当前进行中的 TaskDAG 状态打包导出（Handoff Package），
通过 HTTP 或共享文件系统传递给 L3 Node B，由 B 导入并接管续跑。

这是「跨进程集群 DAG 续跑」的 Phase 1 实现：
- 不需要中心化 Coordinator
- 不需要分布式锁（依赖幂等的 probe_dag_resume + run_id 唯一性）
- 传输格式：JSON（可直接 POST 或落文件）
- 安全：导入时校验 package schema 版本，防止损坏的包破坏本地 active.json

完整集群调度（Phase 2）仍为 ⏳，需要 L2 编排层支持。

端点（http_server.py 接入）：
    POST /api/v1/registry/dag-handoff/export  — 导出当前 DAG Handoff Package
    POST /api/v1/registry/dag-handoff/import  — 导入 Handoff Package 并续跑

环境变量：
    JACHIN_DAG_HANDOFF_DIR   共享目录路径，非空时 export 会同时落文件（可选）
    JACHIN_PERSIST_HOOKS=1   Node A 必须开启，export 才能获得 hook_events 已完成节点集合
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HANDOFF_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Handoff Package 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HandoffNode:
    node_id: str
    title: str
    description: str
    status: str      # pending / running / failed


@dataclass
class DagHandoffPackage:
    """可跨进程传输的 DAG 续跑包。"""
    schema_version: str
    package_id: str
    exported_at: float
    source_run_id: str
    dag_title: str
    dag_id: str              # 用于 DAG Guardrails 识别
    completed_node_ids: list[str]
    pending_nodes: list[HandoffNode]
    resume_intent: str
    context_hint: str        # 供 Node B system prompt 使用的上下文摘要
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pending_nodes"] = [asdict(n) for n in self.pending_nodes]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DagHandoffPackage":
        nodes = [
            HandoffNode(**n) if isinstance(n, dict) else n
            for n in (d.get("pending_nodes") or [])
        ]
        return cls(
            schema_version=str(d.get("schema_version") or _HANDOFF_SCHEMA_VERSION),
            package_id=str(d.get("package_id") or str(uuid.uuid4())),
            exported_at=float(d.get("exported_at") or time.time()),
            source_run_id=str(d.get("source_run_id") or ""),
            dag_title=str(d.get("dag_title") or ""),
            dag_id=str(d.get("dag_id") or d.get("dag_title") or ""),
            completed_node_ids=list(d.get("completed_node_ids") or []),
            pending_nodes=nodes,
            resume_intent=str(d.get("resume_intent") or ""),
            context_hint=str(d.get("context_hint") or ""),
            extra=dict(d.get("extra") or {}),
        )


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def export_dag_handoff(
    run_id: str,
    *,
    context_hint: str = "",
) -> DagHandoffPackage | None:
    """
    从当前进程的 hook_events + active.json 构建 Handoff Package。
    run_id 为原始 run_id；context_hint 可附加供 Node B 参考的上下文说明。

    返回 None 表示无法构建（如 active.json 不存在）。
    """
    from l3_node.task_engine.dag_resume import probe_dag_resume

    result = probe_dag_resume(run_id)
    if not result.ok:
        logger.warning("[DagHandoff] export failed: %s", result.message)
        return None
    if not result.pending_nodes and not result.completed_node_ids:
        logger.info("[DagHandoff] nothing to export (DAG complete)")
        return None

    package_id = str(uuid.uuid4())
    pkg = DagHandoffPackage(
        schema_version=_HANDOFF_SCHEMA_VERSION,
        package_id=package_id,
        exported_at=time.time(),
        source_run_id=run_id,
        dag_title=result.dag_title,
        dag_id=result.dag_title,   # 以 dag_title 作 dag_id（full ID 需 Planner 支持，Phase 2）
        completed_node_ids=result.completed_node_ids,
        pending_nodes=[
            HandoffNode(
                node_id=n.node_id,
                title=n.title,
                description=n.description,
                status=n.status,
            )
            for n in result.pending_nodes
        ],
        resume_intent=result.resume_intent,
        context_hint=(context_hint or "").strip()[:2000],
    )

    # 可选：落文件到共享目录
    handoff_dir = (os.environ.get("JACHIN_DAG_HANDOFF_DIR") or "").strip()
    if handoff_dir:
        _write_handoff_file(handoff_dir, pkg)

    logger.info(
        "[DagHandoff] exported package_id=%s dag=%s pending=%d",
        package_id,
        result.dag_title,
        len(result.pending_nodes),
    )
    return pkg


def _write_handoff_file(handoff_dir: str, pkg: DagHandoffPackage) -> None:
    """落文件到共享目录（共享 NFS / 本地磁盘均可）。"""
    try:
        d = Path(handoff_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        fp = d / f"dag_handoff_{pkg.package_id[:8]}.json"
        fp.write_text(json.dumps(pkg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[DagHandoff] package written to %s", fp)
    except Exception as e:
        logger.warning("[DagHandoff] write_handoff_file failed: %s", e)


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

@dataclass
class HandoffImportResult:
    ok: bool
    package_id: str
    dag_title: str
    pending_count: int
    resume_intent: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def import_dag_handoff(package_data: dict[str, Any]) -> HandoffImportResult:
    """
    导入 Handoff Package 到本地 active.json，并返回续跑意图文本。
    调用方可将 resume_intent 传给 run_agent，由 Agent 驱动后续执行。

    - 检查 schema_version 兼容性
    - 将 pending_nodes 写入 active.json（覆盖或合并现有 DAG）
    - 继承 completed_node_ids（标记已完成节点 status=done）
    """
    from l3_node.task_engine.task_dag import load_task_dag_dict, save_active_task_dag_dict

    # ── schema 校验 ──────────────────────────────────────────────────────────
    sv = str(package_data.get("schema_version") or "")
    if sv != _HANDOFF_SCHEMA_VERSION:
        return HandoffImportResult(
            ok=False,
            package_id=str(package_data.get("package_id") or ""),
            dag_title=str(package_data.get("dag_title") or ""),
            pending_count=0,
            resume_intent="",
            message=f"schema_version 不兼容：期望 {_HANDOFF_SCHEMA_VERSION!r}，收到 {sv!r}。",
        )

    try:
        pkg = DagHandoffPackage.from_dict(package_data)
    except Exception as e:
        return HandoffImportResult(
            ok=False,
            package_id=str(package_data.get("package_id") or ""),
            dag_title=str(package_data.get("dag_title") or ""),
            pending_count=0,
            resume_intent="",
            message=f"Handoff Package 解析失败: {e}",
        )

    if not pkg.pending_nodes:
        return HandoffImportResult(
            ok=True,
            package_id=pkg.package_id,
            dag_title=pkg.dag_title,
            pending_count=0,
            resume_intent="",
            message="Handoff Package 中无待续跑节点，无需导入。",
        )

    # ── 读取现有 active.json（或从包构建新 DAG）────────────────────────────
    existing_dag = load_task_dag_dict() or {}
    existing_nodes: list[dict] = []
    if isinstance(existing_dag.get("nodes"), list):
        existing_nodes = existing_dag["nodes"]

    # 构建 node_id → node 映射（现有 + Handoff）
    node_map: dict[str, dict] = {
        str(n.get("node_id") or n.get("id") or ""): dict(n)
        for n in existing_nodes
        if isinstance(n, dict) and (n.get("node_id") or n.get("id"))
    }

    # 将已完成节点标记为 done
    completed_set = set(pkg.completed_node_ids)
    for nid in completed_set:
        if nid in node_map:
            node_map[nid]["status"] = "done"

    # 将待续跑节点写入（若不存在则新建）
    for pn in pkg.pending_nodes:
        if pn.node_id not in node_map:
            node_map[pn.node_id] = {
                "node_id": pn.node_id,
                "title": pn.title,
                "description": pn.description,
                "status": "pending",
            }
        else:
            node_map[pn.node_id]["status"] = "pending"

    # ── 写回 active.json ─────────────────────────────────────────────────────
    new_dag: dict[str, Any] = dict(existing_dag)
    new_dag["title"] = pkg.dag_title
    new_dag["nodes"] = list(node_map.values())
    new_dag["run_id"] = f"{pkg.source_run_id}.handoff.{pkg.package_id[:8]}"
    new_dag["_handoff_package_id"] = pkg.package_id
    new_dag["_handoff_imported_at"] = time.time()
    if pkg.context_hint:
        new_dag["_handoff_context_hint"] = pkg.context_hint

    save_active_task_dag_dict(new_dag)
    logger.info(
        "[DagHandoff] import OK package_id=%s dag=%s pending=%d completed=%d",
        pkg.package_id,
        pkg.dag_title,
        len(pkg.pending_nodes),
        len(completed_set),
    )

    return HandoffImportResult(
        ok=True,
        package_id=pkg.package_id,
        dag_title=pkg.dag_title,
        pending_count=len(pkg.pending_nodes),
        resume_intent=pkg.resume_intent,
        message=(
            f"Handoff 导入成功：{len(pkg.pending_nodes)} 个节点待续跑，"
            f"{len(completed_set)} 个节点已标记完成。"
        ),
    )


# ---------------------------------------------------------------------------
# 从共享目录扫描可导入的包
# ---------------------------------------------------------------------------

async def auto_handoff_to_peer(
    run_id: str,
    *,
    context_hint: str = "",
    release_lock: bool = True,
) -> dict[str, Any]:
    """
    AS — 自动将当前 DAG 转交给空闲对等节点。

    流程：
    1. export_dag_handoff 打包当前 DAG 状态
    2. 从 Coordinator 找到负载最低的空闲节点（load_score < 0.5）
    3. HTTP POST 到对方的 /api/v1/registry/dag-handoff/import 端点
    4. 成功后可选释放本节点对该 DAG 的分布式锁

    返回 { ok, target_node_id, target_url, message }
    """
    # ── 1. 打包 ─────────────────────────────────────────────────────────────
    pkg = export_dag_handoff(run_id, context_hint=context_hint)
    if pkg is None:
        return {"ok": False, "message": "无法导出 Handoff Package（active.json 不存在或 DAG 已完成）"}

    # ── 2. 发现空闲节点 ──────────────────────────────────────────────────────
    try:
        from l3_node.task_engine.dag_coordinator import (
            coordinator_enabled,
            discover_http_peers,
            find_idle_peer,
            register_node,
        )
    except ImportError:
        return {"ok": False, "message": "Coordinator 模块不可用，请确认 JACHIN_COORDINATOR_ENABLE=1"}

    if not coordinator_enabled():
        return {"ok": False, "message": "Coordinator 未开启（JACHIN_COORDINATOR_ENABLE=1）"}

    # 先尝试本地 SQLite 发现，再补充 HTTP 发现
    peer = find_idle_peer(exclude_self=True)
    if peer is None:
        http_peers = await discover_http_peers()
        for hp in http_peers:
            if hp.load_score < 0.5:
                peer = hp
                break

    if peer is None:
        return {
            "ok": False,
            "message": "未发现空闲对等节点；请检查 JACHIN_COORDINATOR_PEER_URLS 配置或等待其他节点心跳。",
            "package_id": pkg.package_id,
        }

    if not peer.http_url:
        return {
            "ok": False,
            "message": f"节点 {peer.node_id!r} 未配置 HTTP URL，无法转交。",
            "package_id": pkg.package_id,
        }

    # ── 3. HTTP 转交 ─────────────────────────────────────────────────────────
    try:
        import aiohttp as _aio
        import json as _json

        target_url = f"{peer.http_url}/api/v1/registry/dag-handoff/import"
        payload = {"package": pkg.to_dict()}

        async with _aio.ClientSession() as sess:
            async with sess.post(
                target_url,
                data=_json.dumps(payload, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"},
                timeout=_aio.ClientTimeout(total=30.0),
            ) as resp:
                resp_data = await resp.json(content_type=None)
                if not resp_data.get("ok"):
                    return {
                        "ok": False,
                        "target_node_id": peer.node_id,
                        "target_url": peer.http_url,
                        "message": f"对方导入失败: {resp_data.get('message', resp_data)}",
                        "package_id": pkg.package_id,
                    }
    except ImportError:
        return {"ok": False, "message": "aiohttp 不可用，无法执行 HTTP 转交"}
    except Exception as e:
        return {
            "ok": False,
            "target_node_id": peer.node_id,
            "target_url": peer.http_url,
            "message": f"HTTP 转交失败: {e}",
            "package_id": pkg.package_id,
        }

    # ── 4. 可选：释放本节点对该 DAG 的锁 ────────────────────────────────────
    if release_lock:
        try:
            from l3_node.task_engine.dag_coordinator import _self_node_id, list_dag_locks, release_dag
            self_id = _self_node_id()
            for lk in list_dag_locks():
                if lk.dag_id == pkg.dag_id and lk.owner_id == self_id:
                    release_dag(pkg.dag_id, self_id, lk.lock_token)
                    break
        except Exception as _re:
            logger.debug("[DagHandoff] release lock after auto_handoff failed: %s", _re)

    logger.info(
        "[DagHandoff] auto_handoff_to_peer OK dag=%s → node=%s url=%s package=%s",
        pkg.dag_title, peer.node_id, peer.http_url, pkg.package_id,
    )
    return {
        "ok": True,
        "target_node_id": peer.node_id,
        "target_url": peer.http_url,
        "package_id": pkg.package_id,
        "resume_intent": pkg.resume_intent,
        "message": f"DAG「{pkg.dag_title}」已成功转交给节点 {peer.node_id!r}。",
    }


def list_available_handoff_packages(limit: int = 10) -> list[dict[str, Any]]:
    """
    扫描 JACHIN_DAG_HANDOFF_DIR 中可导入的包（按修改时间倒序）。
    目录未配置或不存在时返回 []。
    """
    handoff_dir = (os.environ.get("JACHIN_DAG_HANDOFF_DIR") or "").strip()
    if not handoff_dir:
        return []
    d = Path(handoff_dir).expanduser()
    if not d.is_dir():
        return []
    results: list[tuple[float, dict]] = []
    for fp in d.glob("dag_handoff_*.json"):
        try:
            mtime = fp.stat().st_mtime
            data = json.loads(fp.read_text(encoding="utf-8"))
            results.append((mtime, {
                "file": fp.name,
                "package_id": data.get("package_id"),
                "dag_title": data.get("dag_title"),
                "exported_at": data.get("exported_at"),
                "pending_count": len(data.get("pending_nodes") or []),
                "completed_count": len(data.get("completed_node_ids") or []),
            }))
        except Exception:
            pass
    results.sort(key=lambda x: -x[0])
    return [r[1] for r in results[:max(1, min(50, limit))]]
