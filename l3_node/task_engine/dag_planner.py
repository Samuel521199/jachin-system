"""
TaskDAG Planner — LLM 自动维护（AV）

在 ReAct 循环开始前（或用户显式要求时），对复杂意图调用 LLM 生成结构化任务图
并自动写回 ~/.jachin/workspace/task_dags/active.json，驱动 DAG 进度展示与续跑。

工作流：
  1. 触发：
     a. 自动触发（JACHIN_DAG_AUTO_PLAN=1）：agent_core 在收到用户意图后，
        如意图被启发式判断为"复杂任务"，自动调 plan_task_dag
     b. 显式调用：工具 core:plan_dag / HTTP POST /api/v1/registry/dag-plan

  2. LLM 生成结构化 JSON（节点列表 + 依赖关系）
  3. 写回 active.json（已存在时更新 title + nodes，保留已完成节点状态）
  4. 返回 PlannerResult 供调用方展示或注入 Observation

启发式判断"复杂任务"（auto-trigger）：
  - 意图字符数 > JACHIN_DAG_AUTO_PLAN_MIN_CHARS（默认 60）
  - OR 包含多步关键词："步骤|先|然后|接着|最后|分步|拆解|计划|安排|任务列表"
  - AND 当前 active.json 不存在（避免反复覆盖）

环境变量
--------
JACHIN_DAG_AUTO_PLAN=1                 开启自动 Planner（默认关）
JACHIN_DAG_AUTO_PLAN_MIN_CHARS=60      自动触发字符数阈值（默认 60）
JACHIN_DAG_AUTO_PLAN_OVERWRITE=0       是否覆盖已存在的 active.json（默认 0）
JACHIN_DAG_PLAN_MODEL=                 规划用模型（默认使用 LLM_MODEL）
JACHIN_DAG_PLAN_MAX_NODES=16           最大节点数（默认 16）
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def auto_plan_enabled() -> bool:
    return (os.environ.get("JACHIN_DAG_AUTO_PLAN") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _min_chars() -> int:
    raw = (os.environ.get("JACHIN_DAG_AUTO_PLAN_MIN_CHARS") or "60").strip()
    try:
        return max(20, int(raw))
    except ValueError:
        return 60


def _auto_overwrite() -> bool:
    return (os.environ.get("JACHIN_DAG_AUTO_PLAN_OVERWRITE") or "0").strip().lower() in (
        "1", "true", "yes"
    )


def _plan_model() -> str:
    return (
        os.environ.get("JACHIN_DAG_PLAN_MODEL")
        or os.environ.get("LLM_MODEL")
        or "qwen-plus"
    ).strip()


def _max_nodes() -> int:
    raw = (os.environ.get("JACHIN_DAG_PLAN_MAX_NODES") or "16").strip()
    try:
        return max(2, min(32, int(raw)))
    except ValueError:
        return 16


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class PlanNode:
    node_id: str
    title: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    estimated_minutes: int = 0


@dataclass
class PlannerResult:
    ok: bool
    dag_id: str
    title: str
    nodes: list[PlanNode]
    written_to: str      # 写入路径（或 "skipped"）
    error: str = ""
    raw_json: str = ""


# ---------------------------------------------------------------------------
# 启发式判断
# ---------------------------------------------------------------------------

_MULTI_STEP_PATTERN = re.compile(
    r"步骤|先.*然后|接着|最后|分步|拆解|计划|任务列表|清单|依次|第[一二三四五六七八九十\d]+步",
)


def should_auto_plan(intent: str) -> bool:
    """启发式判断是否需要自动生成 DAG。"""
    if not auto_plan_enabled():
        return False
    s = (intent or "").strip()
    if not s:
        return False
    if len(s) < _min_chars() and not _MULTI_STEP_PATTERN.search(s):
        return False
    # 已有 active.json 时默认不覆盖
    from l3_node.task_engine.task_dag import active_task_dag_path
    if active_task_dag_path().is_file() and not _auto_overwrite():
        return False
    return True


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """你是一个任务规划助手。根据用户的意图，生成一个结构化任务图（TaskDAG）。

输出格式（JSON，不要添加额外说明）：
{
  "title": "任务标题（≤30字）",
  "nodes": [
    {
      "node_id": "1",
      "title": "节点标题（≤30字）",
      "description": "简要描述（可选）",
      "depends_on": [],
      "estimated_minutes": 5
    }
  ]
}

约束：
- 节点数量 2~{max_nodes} 个
- node_id 为字符串数字（"1"、"2"……）
- depends_on 列出前置节点的 node_id
- estimated_minutes 为粗略预估（分钟）
- 节点标题用行动词开头（如：搜索/分析/生成/写入/审核/发送）
- 不要输出 JSON 以外的任何内容"""


async def _call_llm_plan(intent: str) -> str:
    """调用 LLM 生成 TaskDAG JSON 字符串。"""
    try:
        from l3_node.llm_client import LiteLLMEngine
    except ImportError:
        raise RuntimeError("LiteLLMEngine unavailable")

    engine = LiteLLMEngine(model=_plan_model())
    system = _PLAN_SYSTEM.replace("{max_nodes}", str(_max_nodes()))
    user_msg = f"请为以下任务意图生成 TaskDAG：\n\n{intent}"
    response = await engine.generate_response(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        temperature=0.2,
        max_tokens=1200,
    )
    return str(response or "").strip()


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def _parse_plan_json(raw: str, intent: str) -> tuple[str, list[PlanNode]]:
    """从 LLM 输出中提取 JSON，返回 (title, nodes)。"""
    # 尝试提取 JSON 代码块
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if m:
        raw = m.group(1)
    else:
        # 直接找最外层 {}
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]

    data = json.loads(raw)
    title = str(data.get("title") or intent[:30]).strip()
    raw_nodes = data.get("nodes") or []
    if not isinstance(raw_nodes, list):
        raise ValueError("nodes field is not a list")

    nodes: list[PlanNode] = []
    for n in raw_nodes[:_max_nodes()]:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("node_id") or str(len(nodes) + 1)).strip()
        t = str(n.get("title") or f"步骤 {nid}").strip()[:60]
        desc = str(n.get("description") or "").strip()[:200]
        deps = [str(d) for d in (n.get("depends_on") or []) if str(d).strip()]
        try:
            est = int(n.get("estimated_minutes") or 0)
        except (TypeError, ValueError):
            est = 0
        nodes.append(PlanNode(
            node_id=nid, title=t, description=desc,
            depends_on=deps, status="pending", estimated_minutes=est,
        ))
    if not nodes:
        raise ValueError("no valid nodes parsed")
    return title, nodes


# ---------------------------------------------------------------------------
# active.json 读写
# ---------------------------------------------------------------------------

def _write_active_json(dag_id: str, title: str, nodes: list[PlanNode]) -> str:
    from l3_node.task_engine.task_dag import active_task_dag_path
    path = active_task_dag_path()

    # 保留已存在节点的完成状态
    existing_status: dict[str, str] = {}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            for n in (old.get("nodes") or []):
                if isinstance(n, dict):
                    nid = str(n.get("node_id") or n.get("id") or "")
                    st = str(n.get("status") or "pending")
                    if nid:
                        existing_status[nid] = st
        except Exception:
            pass

    node_dicts = []
    for n in nodes:
        st = existing_status.get(n.node_id, "pending")
        d: dict[str, Any] = {
            "node_id": n.node_id,
            "title": n.title,
            "status": st,
        }
        if n.description:
            d["description"] = n.description
        if n.depends_on:
            d["depends_on"] = n.depends_on
        if n.estimated_minutes:
            d["estimated_minutes"] = n.estimated_minutes
        node_dicts.append(d)

    payload: dict[str, Any] = {
        "dag_id": dag_id,
        "title": title,
        "nodes": node_dicts,
        "planned_at": time.time(),
        "planner_version": "AV",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from l3_node.task_engine.task_plan_dag_bridge import mirror_active_json_to_task_plan_md

        mirror_active_json_to_task_plan_md()
    except Exception:
        pass
    return str(path)


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

async def plan_task_dag(
    intent: str,
    *,
    force: bool = False,
    dag_id: str | None = None,
) -> PlannerResult:
    """
    主入口：为给定意图生成 TaskDAG 并写入 active.json。

    Parameters
    ----------
    intent : str
        用户意图 / 任务描述
    force : bool
        True 时忽略 auto_plan 启发式检查（直接执行）
    dag_id : str | None
        自定义 DAG ID（默认自动生成）
    """
    did = dag_id or f"dag-{uuid.uuid4().hex[:8]}"

    if not force and not should_auto_plan(intent):
        return PlannerResult(
            ok=False, dag_id=did, title="", nodes=[],
            written_to="skipped",
            error="auto_plan not triggered (intent too short or active.json exists)",
        )

    try:
        raw = await _call_llm_plan(intent)
    except Exception as e:
        logger.warning("[DagPlanner] LLM call failed: %s", e)
        return PlannerResult(
            ok=False, dag_id=did, title="", nodes=[],
            written_to="skipped", error=str(e),
        )

    try:
        title, nodes = _parse_plan_json(raw, intent)
    except Exception as e:
        logger.warning("[DagPlanner] JSON parse failed: %s | raw=%s", e, raw[:200])
        return PlannerResult(
            ok=False, dag_id=did, title="", nodes=[],
            written_to="skipped", error=f"parse error: {e}",
            raw_json=raw[:500],
        )

    written = _write_active_json(did, title, nodes)
    logger.info(
        "[DagPlanner] wrote dag_id=%s title=%s nodes=%d to %s",
        did, title, len(nodes), written,
    )
    return PlannerResult(
        ok=True, dag_id=did, title=title, nodes=nodes,
        written_to=written, raw_json=raw[:1000],
    )


def plan_task_dag_sync(intent: str, *, force: bool = False) -> PlannerResult:
    """同步版本（供非 async 场景调用）。"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, plan_task_dag(intent, force=force))
                return fut.result(timeout=60)
        else:
            return loop.run_until_complete(plan_task_dag(intent, force=force))
    except Exception as e:
        return PlannerResult(
            ok=False, dag_id="", title="", nodes=[],
            written_to="skipped", error=str(e),
        )
