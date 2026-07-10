"""
阶段 C：WorkflowSpec — 线性 steps、**depends_on（DAG）** 拓扑执行，以及 **持久化运行时**
（on_failure、retry、retry_delay、resume）。与 HR `DAGWorkflow` 独立并存，互不替换。

- **一次性执行**（默认）：无状态文件，行为与历史版本一致。
- **持久化执行**：`persistent:true` 或 `resume:true` 时，状态写入
  `~/.jachin/workspace/.workflow_state/<workflow_id>__<run_id>.json`；
  成功后默认删除状态文件（可用 `keep_completed_state` 保留）。

YAML 步骤扩展字段：
  on_failure: abort | continue | retry   # 默认 abort；archived: continue_on_error: true → continue
  max_retries: 3                          # retry 时生效，默认 1
  retry_delay_sec: 60                    # >0 时未到时间则返回 paused_retry，需再次调用 resume

**L3 跨域 glue — domain_ref**（与 tool_id 二选一）：
  domain_ref: hr_recruitment              # 或 domain_workflow / domain
  input:
    workflow_id: hr_recruitment_main
    include_analyze: false
    context: { "skip_hr_plan_init_node": true }

详见 `docs/07_memory_first_main_agent_and_voice_app_agents.md`。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.intelligence_workspace import emit_intelligence_event, get_jachin_home

logger = logging.getLogger(__name__)


def _workspace() -> Path:
    return (get_jachin_home() / "workspace").resolve()


def _state_dir() -> Path:
    d = (_workspace() / ".workflow_state").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_key(s: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (s or "").strip())[:80]
    return t or "anon"


def _state_path(wid: str, run_id: str) -> Path:
    return _state_dir() / f"{_safe_key(wid)}__{_safe_key(run_id)}.json"


def _step_stable_id(step: dict[str, Any], index: int) -> str:
    sid = step.get("id")
    if sid is not None and str(sid).strip():
        return str(sid).strip()
    return f"__idx_{index}"


def _step_on_failure_mode(step: dict[str, Any]) -> str:
    if step.get("continue_on_error"):
        return "continue"
    raw = str(step.get("on_failure") or "abort").lower().strip()
    if raw in ("continue", "abort", "retry"):
        return raw
    return "abort"


def _step_max_retries(step: dict[str, Any]) -> int:
    try:
        n = int(step.get("max_retries", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, 50))


def _step_retry_delay_sec(step: dict[str, Any]) -> int:
    try:
        n = int(step.get("retry_delay_sec", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, min(n, 86400 * 7))


def _topological_order_indices(steps: list[dict[str, Any]]) -> list[int] | dict[str, Any]:
    """返回按依赖排序的 step 下标；环检测返回 {error: ...}。"""
    id_to_i: dict[str, int] = {}
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if sid is not None and str(sid).strip():
            id_to_i[str(sid).strip()] = i

    graph: dict[int, list[int]] = defaultdict(list)
    indeg: dict[int, int] = defaultdict(int)
    n = len(steps)
    for i in range(n):
        indeg[i] = 0
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        raw = s.get("depends_on") or s.get("needs") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for dep in raw:
            ds = str(dep).strip()
            if ds not in id_to_i:
                return {"error": f"步骤 {i} 依赖未知 id: {dep!r}"}
            j = id_to_i[ds]
            graph[j].append(i)
            indeg[i] += 1

    q = deque([i for i in range(n) if indeg[i] == 0])
    out: list[int] = []
    while q:
        u = q.popleft()
        out.append(u)
        for v in graph[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if len(out) != n:
        return {"error": "steps 存在循环依赖（depends_on）"}
    return out


def _load_yaml(rel: str) -> dict[str, Any]:
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return {"ok": False, "error": "非法 workflow 路径"}
    p = (_workspace() / rel).resolve()
    if not str(p).startswith(str(_workspace())):
        return {"ok": False, "error": "路径越界"}
    if not p.exists():
        return {"ok": False, "error": f"文件不存在: {rel}"}
    try:
        import yaml
    except ImportError:
        return {"ok": False, "error": "需要 PyYAML"}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"YAML 解析失败: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "根节点须为 mapping"}
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "缺少 steps 列表"}
    wid = str(data.get("id", p.stem))
    order = _topological_order_indices(steps)
    if isinstance(order, dict):
        return {"ok": False, "error": order.get("error", "拓扑失败")}
    return {
        "ok": True,
        "rel": rel,
        "path": p,
        "data": data,
        "steps": steps,
        "wid": wid,
        "order": order,
    }


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[WorkflowSpec] 读取状态失败 %s: %s", path, e)
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _step_domain_ref(step: dict[str, Any]) -> str:
    """L2 领域子图 id（小写）；与 tool_id 互斥优先 domain。"""
    for k in ("domain_ref", "domain_workflow", "domain"):
        v = step.get(k)
        if v is not None and str(v).strip():
            return str(v).strip().lower()
    return ""


def _step_domain_params(step: dict[str, Any]) -> dict[str, Any]:
    raw = step.get("input", step.get("params", {}))
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            o = json.loads(raw)
            return dict(o) if isinstance(o, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _execute_one_step(
    step: dict[str, Any],
    step_index: int,
    ord_i: int,
    *,
    allowed_skills: Optional[list[str]],
) -> dict[str, Any]:
    sid = step.get("id")
    domain = _step_domain_ref(step)
    if domain:
        params = _step_domain_params(step)
        try:
            from l3_node.orchestration.domain_registry import run_domain

            dom_out = run_domain(domain, params)
            ok = bool(dom_out.get("ok"))
            preview = json.dumps(dom_out, ensure_ascii=False)[:2000]
            return {
                "step_index": step_index,
                "id": sid,
                "order": ord_i,
                "domain_ref": domain,
                "ok": ok,
                "preview": preview,
                "error": None if ok else dom_out.get("error"),
            }
        except Exception as e:
            return {
                "step_index": step_index,
                "id": sid,
                "order": ord_i,
                "domain_ref": domain,
                "ok": False,
                "error": str(e),
            }

    tool_id = str(step.get("tool_id") or step.get("tool") or "").strip()
    inp = step.get("input", "")
    if isinstance(inp, dict):
        inp = json.dumps(inp, ensure_ascii=False)
    else:
        inp = str(inp or "")
    if not tool_id:
        return {
            "step_index": step_index,
            "id": sid,
            "order": ord_i,
            "error": "缺少 tool_id 或 domain_ref",
            "ok": False,
        }
    try:
        from l3_node.primitives.tools.loader import run_tool

        out = run_tool(tool_id, inp, allowed_skills=allowed_skills)
        return {
            "step_index": step_index,
            "id": sid,
            "order": ord_i,
            "tool_id": tool_id,
            "ok": True,
            "preview": str(out)[:2000],
        }
    except Exception as e:
        return {
            "step_index": step_index,
            "id": sid,
            "order": ord_i,
            "tool_id": tool_id,
            "ok": False,
            "error": str(e),
        }


def _run_ephemeral(
    loaded: dict[str, Any],
    *,
    allowed_skills: Optional[list[str]] = None,
) -> dict[str, Any]:
    """无状态：与历史 run_workflow_yaml 行为一致。"""
    steps: list = loaded["steps"]
    order: list[int] = loaded["order"]
    wid: str = loaded["wid"]
    results: list[dict[str, Any]] = []

    for ord_i, i in enumerate(order):
        step = steps[i]
        if not isinstance(step, dict):
            results.append({"step_index": i, "order": ord_i, "error": "step 非对象", "ok": False})
            continue
        rec = _execute_one_step(step, i, ord_i, allowed_skills=allowed_skills)
        results.append(rec)
        if not rec.get("ok"):
            emit_intelligence_event(
                "workflow_step_failed",
                {"workflow": wid, "step": i, "tool_id": rec.get("tool_id")},
            )
            if _step_on_failure_mode(step) == "continue" or step.get("continue_on_error"):
                continue
            return {"ok": False, "workflow_id": wid, "failed_at": i, "results": results, "persistent": False}

    emit_intelligence_event("workflow_done", {"workflow": wid, "steps": len(steps)})
    return {
        "ok": True,
        "workflow_id": wid,
        "execution_order": order,
        "results": results,
        "persistent": False,
    }


def _run_persistent(
    loaded: dict[str, Any],
    *,
    allowed_skills: Optional[list[str]] = None,
    run_id: str,
    resume: bool,
    reset: bool,
    keep_completed_state: bool,
) -> dict[str, Any]:
    steps: list = loaded["steps"]
    order: list[int] = loaded["order"]
    wid: str = loaded["wid"]
    rel: str = loaded["rel"]
    rid = (run_id or "default").strip() or "default"
    spath = _state_path(wid, rid)

    if reset and spath.exists():
        try:
            spath.unlink()
        except OSError as e:
            logger.debug("[WorkflowSpec] reset unlink: %s", e)

    state = _read_state(spath) if not reset else None
    if state is None:
        state = {
            "v": 1,
            "workflow_id": wid,
            "yaml_rel": rel,
            "run_id": rid,
            "completed_ids": [],
            "status": "running",
            "next_resume_at": None,
            "paused_step_id": None,
            "retry_counts": {},
            "updated_at": _utc_now_iso(),
        }
    else:
        # YAML 路径变更时以本次调用为准
        state["yaml_rel"] = rel
        if state.get("run_id") != rid:
            state["run_id"] = rid

    if not resume and not reset and state.get("status") == "completed":
        return {
            "ok": True,
            "workflow_id": wid,
            "run_id": rid,
            "message": "工作流此前已成功完成，未重新执行。若要重跑请传 reset:true",
            "results": state.get("last_results") or [],
            "persistent": True,
            "state_path": str(spath),
        }

    # 定时 resume
    nra = state.get("next_resume_at")
    dt = _parse_iso(nra if isinstance(nra, str) else None)
    if dt is not None:
        now = datetime.now(timezone.utc)
        if now < dt:
            return {
                "ok": False,
                "workflow_id": wid,
                "run_id": rid,
                "status": "paused_retry",
                "error": "尚未到重试时间",
                "next_resume_at": nra,
                "persistent": True,
                "state_path": str(spath),
            }
    state["next_resume_at"] = None
    state["paused_step_id"] = None

    results: list[dict[str, Any]] = list(state.get("last_results") or [])
    completed: set[str] = set(state.get("completed_ids") or [])

    for ord_i, i in enumerate(order):
        step = steps[i]
        if not isinstance(step, dict):
            continue
        sid = _step_stable_id(step, i)
        if sid in completed:
            continue

        mode = _step_on_failure_mode(step)
        max_r = _step_max_retries(step)
        delay_s = _step_retry_delay_sec(step)

        while True:
            rec = _execute_one_step(step, i, ord_i, allowed_skills=allowed_skills)
            results.append(rec)
            state["last_results"] = results[-50:]
            state["updated_at"] = _utc_now_iso()

            if rec.get("ok"):
                completed.add(sid)
                state["completed_ids"] = list(completed)
                rc = state.setdefault("retry_counts", {})
                if isinstance(rc, dict) and sid in rc:
                    rc.pop(sid, None)
                _atomic_write_json(spath, state)
                break

            emit_intelligence_event(
                "workflow_step_failed",
                {"workflow": wid, "step": i, "tool_id": rec.get("tool_id"), "persistent": True},
            )

            if mode == "continue":
                completed.add(sid)
                state["completed_ids"] = list(completed)
                _atomic_write_json(spath, state)
                break

            if mode == "retry":
                rc = state.setdefault("retry_counts", {})
                if not isinstance(rc, dict):
                    rc = {}
                    state["retry_counts"] = rc
                cnt = int(rc.get(sid, 0) or 0) + 1
                rc[sid] = cnt
                if cnt < max_r:
                    if delay_s > 0:
                        from datetime import timedelta

                        resume_at = datetime.now(timezone.utc) + timedelta(seconds=delay_s)
                        state["next_resume_at"] = resume_at.replace(microsecond=0).isoformat()
                        state["paused_step_id"] = sid
                        state["status"] = "paused_retry"
                        _atomic_write_json(spath, state)
                        return {
                            "ok": False,
                            "workflow_id": wid,
                            "run_id": rid,
                            "status": "paused_retry",
                            "error": rec.get("error", "step failed"),
                            "next_resume_at": state["next_resume_at"],
                            "retry_count": cnt,
                            "max_retries": max_r,
                            "results": results,
                            "persistent": True,
                            "state_path": str(spath),
                        }
                    _atomic_write_json(spath, state)
                    continue
                state["status"] = "failed"
                _atomic_write_json(spath, state)
                return {
                    "ok": False,
                    "workflow_id": wid,
                    "run_id": rid,
                    "failed_at": i,
                    "status": "failed",
                    "error": rec.get("error", "step failed"),
                    "results": results,
                    "persistent": True,
                    "state_path": str(spath),
                }

            # abort
            state["status"] = "failed"
            _atomic_write_json(spath, state)
            return {
                "ok": False,
                "workflow_id": wid,
                "run_id": rid,
                "failed_at": i,
                "status": "failed",
                "results": results,
                "persistent": True,
                "state_path": str(spath),
            }

    state["status"] = "completed"
    state["next_resume_at"] = None
    state["paused_step_id"] = None
    _atomic_write_json(spath, state)
    emit_intelligence_event("workflow_done", {"workflow": wid, "steps": len(steps), "persistent": True, "run_id": rid})

    out = {
        "ok": True,
        "workflow_id": wid,
        "run_id": rid,
        "execution_order": order,
        "results": results,
        "persistent": True,
        "state_path": str(spath),
        "status": "completed",
    }
    if not keep_completed_state:
        try:
            spath.unlink()
            out["state_cleared"] = True
        except OSError:
            pass
    return out


def run_workflow_yaml(
    yaml_rel_path: str,
    *,
    allowed_skills: Optional[list[str]] = None,
    persistent: bool = False,
    run_id: str = "default",
    resume: bool = False,
    reset: bool = False,
    keep_completed_state: bool = False,
) -> dict[str, Any]:
    """
    执行 workspace 下 YAML 工作流。

    - 默认 **ephemeral**：不写状态文件（兼容旧调用）。
    - **persistent** / **resume**：启用 `~/.jachin/workspace/.workflow_state/` 持久化与断点续跑。
    """
    loaded = _load_yaml(yaml_rel_path)
    if not loaded.get("ok"):
        return {"ok": False, "error": loaded.get("error", "load failed")}

    use_persistent = bool(persistent or resume)
    if use_persistent:
        rid = (run_id or "default").strip() or "default"
        return _run_persistent(
            loaded,
            allowed_skills=allowed_skills,
            run_id=rid,
            resume=resume,
            reset=reset,
            keep_completed_state=keep_completed_state,
        )
    return _run_ephemeral(loaded, allowed_skills=allowed_skills)


def new_workflow_run_id() -> str:
    """生成唯一 run_id（供 Agent / 调度器使用）。"""
    return uuid.uuid4().hex[:12]
