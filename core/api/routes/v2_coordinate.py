"""
Jachin Nexus V2 - L2 虫群精准协同调度 API

POST /api/v2/coordinate/task: L3 请求协同，L2 按 skill_required 精准匹配节点并派发
GET /api/v2/coordinate/poll: L3 长轮询拉取分配给自己的子任务（拉取时更新 last_seen_at 保持在线）
POST /api/v2/coordinate/result: L3 提交子任务执行结果
GET /api/v2/coordinate/status: L3-1 轮询主任务状态与聚合结果
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from core.db import get_connection
from core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_003,
    ERR_BAD_REQUEST_001,
    ERR_BAD_REQUEST_002,
    ERR_INTERNAL_001,
    ERR_NOT_FOUND_001,
    ERR_QUOTA_001,
    ERR_SCHEDULER_001,
    ERR_SCHEDULER_002,
    ERR_SCHEDULER_003,
    api_error,
)
from core.l3_redis_state import (
    get_online_l3_nodes_for_sub_account,
    pop_subtasks_from_queue,
    push_subtask_to_queue,
    write_l3_node_status,
)
from core.permissions import ACTION_COORDINATE, get_permissions, normalize_permissions_for_l3, verify_permissions
from core.resource_quota import check_task_quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["v2-coordinate"])

# 节点在线阈值：last_seen_at 在此秒数内视为活跃
_ONLINE_THRESHOLD_SEC = 600


def _get_sub_account_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or request.headers.get("X-Sub-Account-Id")
    if auth and auth.startswith("Bearer "):
        return auth[7:].strip() or None
    if auth:
        return auth.strip() or None
    return request.headers.get("X-Sub-Account-Id")


def _normalize_skill(s: str) -> str:
    """规范化 skill 格式，支持 core:xxx 与 xxx"""
    s = (s or "").strip().lower()
    if not s:
        return ""
    return s if ":" in s else f"core:{s}"


def _skill_in_allowed(skill: str, allowed: list[str] | None) -> bool:
    """校验 skill 是否在 allowed_skills 白名单内（支持 core:xxx 与 xxx 互匹配）"""
    if not skill:
        return False
    if allowed is None:
        return True
    if not allowed:
        return False
    skill_norm = _normalize_skill(skill)
    for a in allowed:
        a_norm = _normalize_skill(a)
        if skill_norm == a_norm:
            return True
        if a_norm.startswith("core:") and skill_norm == a_norm[5:]:
            return True
        if not a_norm.startswith("core:") and skill_norm == f"core:{a_norm}":
            return True
    return False


def _parse_node_skills(capabilities_json: str) -> set[str]:
    """从 l3_nodes.capabilities_json 解析节点具备的技能集合（含 core:xxx 与 xxx 两种形式）"""
    out: set[str] = set()
    try:
        raw = json.loads(capabilities_json or "{}")
    except Exception:
        return out
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s.strip():
                n = _normalize_skill(s)
                out.add(n)
                out.add(n[5:] if n.startswith("core:") else f"core:{n}")
    elif isinstance(raw, dict):
        skills = raw.get("allowed_skills") or raw.get("skills") or []
        for s in skills:
            if isinstance(s, str) and s.strip():
                n = _normalize_skill(s)
                out.add(n)
                out.add(n[5:] if n.startswith("core:") else f"core:{n}")
    return out


def _node_has_skill(node_skills: set[str], skill_required: str) -> bool:
    """节点技能集合是否包含所需技能（支持 core:xxx 与 xxx 互匹配）"""
    if not node_skills or not skill_required:
        return False
    sn = _normalize_skill(skill_required)
    return sn in node_skills or (sn[5:] if sn.startswith("core:") else f"core:{sn}") in node_skills


def _parse_telemetry(capabilities_json: str) -> dict[str, Any]:
    """从 capabilities_json 解析硬件遥测：cpu_load, memory_free, memory_free_mb, has_gpu, trust_zone"""
    out: dict[str, Any] = {"cpu_load": 100.0, "memory_free": 0.0, "memory_free_mb": 0.0, "has_gpu": False, "trust_zone": ""}
    try:
        raw = json.loads(capabilities_json or "{}")
        if isinstance(raw, dict):
            if "cpu_load" in raw and raw["cpu_load"] is not None:
                out["cpu_load"] = float(raw["cpu_load"])
            if "memory_free" in raw and raw["memory_free"] is not None:
                out["memory_free"] = float(raw["memory_free"])
            if "memory_free_mb" in raw and raw["memory_free_mb"] is not None:
                out["memory_free_mb"] = float(raw["memory_free_mb"])
            elif out["memory_free"] > 0:
                out["memory_free_mb"] = out["memory_free"]  # 兼容旧字段
            if "has_gpu" in raw:
                out["has_gpu"] = bool(raw["has_gpu"])
            if "trust_zone" in raw and raw["trust_zone"]:
                out["trust_zone"] = str(raw["trust_zone"]).strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return out


def _score_node(
    cpu_load: float,
    memory_free: float,
    last_seen_at: float,
    now: float,
    trust_zone_affinity: bool = False,
) -> float:
    """
    节点打分：越低越优。
    cpu_load 越低越好，memory_free 越高越好，last_seen_at 越新越好。
    trust_zone_affinity=True 时同局域网大幅加分（减 30 分）。
    """
    load_score = min(100, max(0, cpu_load))
    freshness = max(0, now - last_seen_at)
    freshness_penalty = min(50, freshness / 60)
    score = load_score + freshness_penalty - (memory_free / 1000)
    if trust_zone_affinity:
        score -= 30
    return score


def _get_eligible_nodes_for_skill(
    conn,
    sub_account_id: str,
    skill_required: str,
    exclude_node_id: Optional[str] = None,
    requires_gpu: bool = False,
    requires_memory_mb: float = 0.0,
    parent_trust_zone: Optional[str] = None,
) -> list[tuple[str, float]]:
    """
    精准匹配 + 负载打分：返回 (node_id, score) 列表，按 score 升序（最优在前）。
    优先从 Redis 读取 L3 在线状态（无状态集群）；Redis 不可用时回退 SQLite last_seen_at。
    """
    if not skill_required or not skill_required.strip():
        return []
    now = time.time()
    cutoff = now - _ONLINE_THRESHOLD_SEC

    perms = get_permissions(conn, sub_account_id)
    perms_l3 = normalize_permissions_for_l3(perms)
    sub_allowed = perms_l3.get("allowed_skills")

    if sub_allowed is not None and not _skill_in_allowed(skill_required, sub_allowed):
        return []

    # 优先 Redis：无状态集群下 L3 在线状态在 Redis
    redis_nodes = get_online_l3_nodes_for_sub_account(sub_account_id)
    if redis_nodes:
        rows = [
            {
                "id": n["node_id"],
                "capabilities_json": n.get("capabilities_json") or "{}",
                "trust_zone": n.get("trust_zone") or "",
                "last_seen_at": n.get("last_seen_at", 0),
            }
            for n in redis_nodes
            if n.get("last_seen_at", 0) > cutoff
        ]
    else:
        rows = conn.execute(
            """
            SELECT id, capabilities_json, trust_zone, last_seen_at FROM l3_nodes
            WHERE sub_account_id = ? AND id IS NOT NULL AND last_seen_at > ?
            ORDER BY last_seen_at DESC
            """,
            (sub_account_id, cutoff),
        ).fetchall()

    scored: list[tuple[str, float]] = []
    for r in rows:
        node_id = r["id"]
        if exclude_node_id and node_id == exclude_node_id:
            continue
        caps = r.get("capabilities_json") or "{}"
        node_skills = _parse_node_skills(caps)
        if not node_skills and sub_allowed is not None:
            node_skills = {_normalize_skill(s) for s in sub_allowed}
        if not _node_has_skill(node_skills, skill_required):
            continue
        telemetry = _parse_telemetry(caps)
        if not telemetry.get("trust_zone") and r.get("trust_zone"):
            telemetry["trust_zone"] = str(r.get("trust_zone", "")).strip()
        if requires_gpu and not telemetry.get("has_gpu"):
            continue
        mem_mb = telemetry.get("memory_free_mb") or telemetry.get("memory_free", 0)
        if requires_memory_mb > 0 and mem_mb < requires_memory_mb:
            continue
        last_seen = float(r.get("last_seen_at") or 0)
        same_zone = bool(parent_trust_zone and telemetry.get("trust_zone") == parent_trust_zone)
        score = _score_node(
            telemetry["cpu_load"],
            telemetry["memory_free"],
            last_seen,
            now,
            trust_zone_affinity=same_zone,
        )
        scored.append((node_id, score))
    scored.sort(key=lambda x: x[1])
    return scored


def _fallback_eligible_nodes(
    conn,
    sub_account_id: str,
    skill_required: str,
    exclude_node_id: Optional[str] = None,
    requires_gpu: bool = False,
    requires_memory_mb: float = 0.0,
    parent_trust_zone: Optional[str] = None,
) -> list[tuple[str, float]]:
    """
    兜底：若无在线节点，放宽 last_seen_at 限制，按 capabilities 匹配并打分。
    用于兼容尚未实现 poll 心跳的 L3。
    """
    if not skill_required or not skill_required.strip():
        return []
    perms = get_permissions(conn, sub_account_id)
    perms_l3 = normalize_permissions_for_l3(perms)
    sub_allowed = perms_l3.get("allowed_skills")
    if sub_allowed is not None and not _skill_in_allowed(skill_required, sub_allowed):
        return []

    rows = conn.execute(
        """
        SELECT id, capabilities_json, trust_zone, last_seen_at FROM l3_nodes
        WHERE sub_account_id = ? AND id IS NOT NULL
        ORDER BY last_seen_at DESC
        """,
        (sub_account_id,),
    ).fetchall()

    now = time.time()
    scored: list[tuple[str, float]] = []
    for r in rows:
        node_id = r["id"]
        if exclude_node_id and node_id == exclude_node_id:
            continue
        caps = r["capabilities_json"] or "{}"
        node_skills = _parse_node_skills(caps)
        if not node_skills and sub_allowed is not None:
            node_skills = {_normalize_skill(s) for s in sub_allowed}
        if not _node_has_skill(node_skills, skill_required):
            continue
        telemetry = _parse_telemetry(caps)
        if not telemetry.get("trust_zone") and r.get("trust_zone"):
            telemetry["trust_zone"] = str(r.get("trust_zone", "")).strip()
        if requires_gpu and not telemetry.get("has_gpu"):
            continue
        mem_mb = telemetry.get("memory_free_mb") or telemetry.get("memory_free", 0)
        if requires_memory_mb > 0 and mem_mb < requires_memory_mb:
            continue
        last_seen = float(r.get("last_seen_at") or 0)
        same_zone = bool(parent_trust_zone and telemetry.get("trust_zone") == parent_trust_zone)
        score = _score_node(
            telemetry["cpu_load"],
            telemetry["memory_free"],
            last_seen,
            now,
            trust_zone_affinity=same_zone,
        )
        scored.append((node_id, score))
    scored.sort(key=lambda x: x[1])
    return scored


@router.post("/coordinate/task")
async def coordinate_task(request: Request) -> dict[str, Any]:
    """
    L3 请求协同。body: { parent_node_id, intent, sub_tasks: [{ intent, skill_required, input_data? }] }
    skill_required 必填（如 "core:shell_exec" 或 ["core:shell_exec"]）。
    L2 虫群精准调度：按 skill_required 筛选 allowed_skills 匹配的在线节点，原子派发。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    parent_node_id = body.get("parent_node_id") or body.get("parent_l3_node_id") or ""
    parent_task_id = body.get("parent_task_id")
    intent = body.get("intent") or ""
    sub_tasks = body.get("sub_tasks") or []
    task_skill_required = body.get("skill_required")
    task_timeout_seconds = body.get("timeout_seconds")

    if not parent_node_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "parent_node_id required")
    if not sub_tasks:
        raise api_error(400, ERR_BAD_REQUEST_002, "sub_tasks required")

    conn = get_connection()
    try:
        perm_row = conn.execute(
            "SELECT id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not perm_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        perms = get_permissions(conn, sub_account_id)
        allowed, msg = verify_permissions(perms, ACTION_COORDINATE)
        if not allowed:
            raise api_error(403, ERR_AUTH_003, msg or "无协同权限")
        task_allowed, task_msg = check_task_quota(conn, sub_account_id)
        if not task_allowed:
            raise api_error(402, ERR_QUOTA_001, task_msg or "月度任务配额已用尽")

        def _resolve_skill(st: dict) -> str:
            sk = st.get("skill_required") or (task_skill_required if task_skill_required else "")
            if isinstance(sk, list) and sk:
                return str(sk[0])
            return str(sk) if sk else ""

        def _requires_gpu(st: dict) -> bool:
            """子任务是否标记需要重型算力（GPU）"""
            if st.get("requires_gpu") or st.get("heavy_compute"):
                return True
            inp = st.get("input_data")
            if isinstance(inp, dict):
                if inp.get("requires_gpu") or inp.get("heavy_compute"):
                    return True
            if isinstance(inp, str):
                try:
                    d = json.loads(inp)
                    if isinstance(d, dict) and (d.get("requires_gpu") or d.get("heavy_compute")):
                        return True
                except json.JSONDecodeError:
                    pass
            return bool(body.get("requires_gpu") or body.get("heavy_compute"))

        def _requires_memory_mb(st: dict) -> float:
            """子任务所需内存（MB），能力标签硬约束"""
            v = st.get("requires_memory_mb")
            if v is not None:
                return float(v)
            inp = st.get("input_data")
            if isinstance(inp, dict) and inp.get("requires_memory_mb") is not None:
                return float(inp["requires_memory_mb"])
            if isinstance(inp, str):
                try:
                    d = json.loads(inp)
                    if isinstance(d, dict) and d.get("requires_memory_mb") is not None:
                        return float(d["requires_memory_mb"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return float(body.get("requires_memory_mb") or 0)

        parent_row = conn.execute(
            "SELECT capabilities_json, trust_zone FROM l3_nodes WHERE id = ?",
            (parent_node_id,),
        ).fetchone()
        parent_trust_zone = None
        if parent_row:
            pt = _parse_telemetry(parent_row.get("capabilities_json") or "{}")
            parent_trust_zone = pt.get("trust_zone") or parent_row.get("trust_zone") or None

        assignments: list[tuple[dict, str]] = []
        for st in sub_tasks:
            skill = _resolve_skill(st)
            if not skill or not skill.strip():
                raise api_error(
                    400,
                    ERR_BAD_REQUEST_002,
                    "每个 sub_task 必须包含 skill_required，或任务顶层包含 skill_required。",
                )
            try:
                from core.l1_policy import is_skill_banned
                if is_skill_banned(skill):
                    raise api_error(403, ERR_AUTH_003, f"技能 {skill} 已被 L1 平台全局封禁")
            except ImportError:
                pass
            needs_gpu = _requires_gpu(st)
            needs_mem = _requires_memory_mb(st)
            eligible = _get_eligible_nodes_for_skill(
                conn, sub_account_id, skill,
                exclude_node_id=parent_node_id,
                requires_gpu=needs_gpu,
                requires_memory_mb=needs_mem,
                parent_trust_zone=parent_trust_zone,
            )
            if not eligible:
                eligible = _fallback_eligible_nodes(
                    conn, sub_account_id, skill,
                    exclude_node_id=parent_node_id,
                    requires_gpu=needs_gpu,
                    requires_memory_mb=needs_mem,
                    parent_trust_zone=parent_trust_zone,
                )
            if not eligible:
                if needs_gpu:
                    raise api_error(400, ERR_SCHEDULER_002, "需要 GPU 算力，但当前子账号下没有具备该技能且 has_gpu 的节点")
                raise api_error(400, ERR_SCHEDULER_001, "调度失败：当前子账号下没有具备该技能的空闲节点")
            assignee = eligible[0][0]
            assignments.append((st, assignee))

        task_id = f"coord-{secrets.token_hex(8)}"
        now = time.time()
        conn.execute(
            """
            INSERT INTO coordinate_tasks (id, sub_account_id, parent_node_id, intent, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (task_id, sub_account_id, parent_node_id, intent, now, now),
        )

        for st, assignee in assignments:
            subtask_id = f"sub-{secrets.token_hex(6)}"
            st_intent = st.get("intent") or st.get("task", "")
            st_skill = _resolve_skill(st)
            st_input = json.dumps(st.get("input_data") or {}, ensure_ascii=False) if isinstance(st.get("input_data"), dict) else (st.get("input_data") or "")
            st_timeout = st.get("timeout_seconds")
            if st_timeout is None:
                st_timeout = task_timeout_seconds
            if st_timeout is not None:
                st_timeout = float(st_timeout) if st_timeout else None
            conn.execute(
                """
                INSERT INTO coordinate_subtasks (id, task_id, assignee_node_id, intent, skill_required, input_data, timeout_seconds, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (subtask_id, task_id, assignee, st_intent, st_skill, st_input, st_timeout, now, now),
            )
            # 无状态集群：子任务推入 Redis 队列，任意 L2 节点 poll 时均可 RPOP
            queue_payload = {
                "subtask_id": subtask_id,
                "task_id": task_id,
                "parent_task_id": task_id,
                "parent_l3_node_id": parent_node_id,
                "intent": st_intent,
                "skill_required": st_skill,
                "input_data": st_input,
            }
            if st_timeout is not None:
                queue_payload["timeout_seconds"] = st_timeout
            push_subtask_to_queue(assignee, queue_payload)
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[coordinate/task] %s", e)
        raise api_error(500, ERR_INTERNAL_001, "任务创建失败", detail=str(e))
    finally:
        conn.close()

    return {
        "ok": True,
        "task_id": task_id,
        "parent_task_id": parent_task_id or task_id,
        "parent_l3_node_id": parent_node_id,
        "status": "pending",
        "sub_tasks_count": len(sub_tasks),
        "message": "任务已创建，子任务已精准分配。L3 通过 GET /api/v2/coordinate/poll 拉取，轮询 GET /api/v2/coordinate/status 获取聚合结果。",
    }


@router.get("/coordinate/poll")
async def coordinate_poll(
    request: Request,
    node_id: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=20),
    cpu_load: Optional[float] = Query(None),
    memory_free: Optional[float] = Query(None),
    has_gpu: Optional[bool] = Query(None),
) -> dict[str, Any]:
    """
    L3 长轮询拉取分配给自己的待执行子任务。
    需携带 X-Sub-Account-Id，仅返回该子账号下且 assignee_node_id=node_id 的 pending 子任务。
    无状态集群：拉取时写入 Redis l3_node_status（TTL 60s），任务优先从 Redis 队列 RPOP。
    Redis 不可用时回退 SQLite last_seen_at 与本地子任务表。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    conn = get_connection()
    caps_json = "{}"
    trust_zone = ""
    try:
        row = conn.execute(
            "SELECT id, capabilities_json, trust_zone FROM l3_nodes WHERE id = ? AND sub_account_id = ?",
            (node_id, sub_account_id),
        ).fetchone()
        if row:
            caps_json = row.get("capabilities_json") or "{}"
            trust_zone = str(row.get("trust_zone") or "")
            if cpu_load is not None or memory_free is not None or has_gpu is not None:
                try:
                    caps = json.loads(caps_json) if isinstance(caps_json, str) else (caps_json or {})
                    if not isinstance(caps, dict):
                        caps = {}
                    if cpu_load is not None:
                        caps["cpu_load"] = float(cpu_load)
                    if memory_free is not None:
                        caps["memory_free"] = float(memory_free)
                    if has_gpu is not None:
                        caps["has_gpu"] = bool(has_gpu)
                    caps_json = json.dumps(caps, ensure_ascii=False)
                    conn.execute(
                        "UPDATE l3_nodes SET last_seen_at = ?, capabilities_json = ? WHERE id = ?",
                        (time.time(), caps_json, node_id),
                    )
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                conn.execute("UPDATE l3_nodes SET last_seen_at = ? WHERE id = ?", (time.time(), node_id))
            conn.commit()
    finally:
        conn.close()

    # 无状态集群：L3 在线状态写入 Redis，调度器从 Redis 读取
    write_l3_node_status(node_id, sub_account_id, caps_json, trust_zone)

    # 优先从 Redis 队列拉取（任意 L2 节点均可 RPOP）
    tasks = pop_subtasks_from_queue(node_id, limit=limit)
    if tasks:
        # 从 Redis 取到任务后，将 DB 中对应 subtask 标记为 in_progress，避免 DB 回退时重复下发
        conn = get_connection()
        try:
            for t in tasks:
                sid = t.get("subtask_id")
                if sid:
                    conn.execute(
                        "UPDATE coordinate_subtasks SET status = 'in_progress' WHERE id = ? AND status = 'pending'",
                        (sid,),
                    )
            conn.commit()
        except Exception as e:
            logger.debug("[coordinate/poll] 标记 in_progress 失败: %s", e)
        finally:
            conn.close()
    if not tasks:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.task_id, s.intent, s.skill_required, s.input_data, s.timeout_seconds,
                       t.parent_node_id
                FROM coordinate_subtasks s
                JOIN coordinate_tasks t ON s.task_id = t.id
                WHERE t.sub_account_id = ? AND s.assignee_node_id = ? AND s.status = 'pending'
                ORDER BY s.created_at ASC LIMIT ?
                """,
                (sub_account_id, node_id, limit),
            ).fetchall()
            for r in rows:
                t = {
                    "subtask_id": r["id"],
                    "task_id": r["task_id"],
                    "parent_task_id": r["task_id"],
                    "parent_l3_node_id": r.get("parent_node_id") or "",
                    "intent": r["intent"],
                    "skill_required": r["skill_required"] or "",
                    "input_data": r["input_data"] or "",
                }
                to_sec = r.get("timeout_seconds")
                if to_sec is not None:
                    t["timeout_seconds"] = float(to_sec)
                tasks.append(t)
                conn.execute(
                    "UPDATE coordinate_subtasks SET status = 'in_progress' WHERE id = ?",
                    (r["id"],),
                )
            conn.commit()
        finally:
            conn.close()

    return {"tasks": tasks, "count": len(tasks)}


@router.post("/coordinate/result")
async def coordinate_result(request: Request) -> dict[str, Any]:
    """
    L3 提交子任务执行结果。body: { subtask_id, result }
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    subtask_id = body.get("subtask_id") or ""
    result = body.get("result")

    if not subtask_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "subtask_id required")

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT s.id, s.task_id, s.assignee_node_id, t.sub_account_id FROM coordinate_subtasks s
            JOIN coordinate_tasks t ON s.task_id = t.id
            WHERE s.id = ?
            """,
            (subtask_id,),
        ).fetchone()
        if not row:
            raise api_error(404, ERR_SCHEDULER_003, "Subtask not found")
        if row["sub_account_id"] != sub_account_id:
            raise api_error(403, ERR_AUTH_003, "Sub-account mismatch")

        assignee = row.get("assignee_node_id")
        if assignee:
            conn.execute(
                "UPDATE l3_nodes SET last_seen_at = ? WHERE id = ? AND sub_account_id = ?",
                (time.time(), assignee, sub_account_id),
            )

        result_json = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
        now = time.time()
        conn.execute(
            "UPDATE coordinate_subtasks SET status = 'done', result_json = ?, updated_at = ? WHERE id = ?",
            (result_json, now, subtask_id),
        )

        task_id = row["task_id"]
        undone = conn.execute(
            "SELECT id FROM coordinate_subtasks WHERE task_id = ? AND status != 'done'",
            (task_id,),
        ).fetchall()
        if not undone:
            results = conn.execute(
                "SELECT id, result_json FROM coordinate_subtasks WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
            aggregated = [r["result_json"] for r in results]
            conn.execute(
                "UPDATE coordinate_tasks SET status = 'done', result_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(aggregated, ensure_ascii=False), now, task_id),
            )
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[coordinate/result] %s", e)
        raise api_error(500, ERR_INTERNAL_001, "结果提交失败", detail=str(e))
    finally:
        conn.close()

    return {"ok": True, "subtask_id": subtask_id, "message": "结果已提交"}


@router.get("/coordinate/status")
async def coordinate_status(
    request: Request,
    task_id: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """
    L3-1 轮询主任务状态。返回 status、子任务进度、聚合结果（当 done 时）。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id required")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, sub_account_id, parent_node_id, intent, status, result_json FROM coordinate_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            raise api_error(404, ERR_SCHEDULER_003, "Task not found")
        if row["sub_account_id"] != sub_account_id:
            raise api_error(403, ERR_AUTH_003, "Sub-account mismatch")

        subtasks = conn.execute(
            "SELECT id, assignee_node_id, intent, status FROM coordinate_subtasks WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()

        done = sum(1 for s in subtasks if s["status"] == "done")
        total = len(subtasks)
        result = None
        if row["status"] == "done" and row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except Exception:
                result = row["result_json"]
    except HTTPException:
        raise
    finally:
        conn.close()

    return {
        "task_id": task_id,
        "status": row["status"],
        "progress": f"{done}/{total}",
        "subtasks": [{"id": s["id"], "assignee": s["assignee_node_id"], "intent": s["intent"], "status": s["status"]} for s in subtasks],
        "result": result,
    }
