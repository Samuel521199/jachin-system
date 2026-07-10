"""
DAG 级 Guardrails（AP）—— 跨 Node 预算控制

在 active.json TaskDAG 运行期间，以 dag_id 为粒度跟踪整个 DAG 的资源消耗：
- 总迭代次数（所有节点 RoleExecutionAgent 轮次之和）
- 总工具调用次数
- 总 Token 消耗
- 单 DAG 实际运行节点数

状态持久化到 SQLite（`workspace/dag_guardrails.sqlite3`），跨进程可见；
每次节点完成后更新，续跑时自动继承已用配额。

在 dag_resume.py 的续跑流程中、以及直接调度节点前调用
`DagGuardrailsChecker.check_dag_budget()` —— 若超限则阻止调度并出 DagBrief。

环境变量
--------
JACHIN_DAG_GUARDRAILS_ENABLE=1         开启 DAG 级 Guardrails（默认关）
JACHIN_DAG_GR_MAX_TOTAL_ITERATIONS=200 单 DAG 最大总迭代次数（默认 200）
JACHIN_DAG_GR_MAX_TOTAL_TOOL_CALLS=400 单 DAG 最大总工具调用（默认 400）
JACHIN_DAG_GR_MAX_TOTAL_TOKENS=2000000 单 DAG 最大总 Token 消耗（默认 200 万）
JACHIN_DAG_GR_MAX_NODES=50             单 DAG 最大节点数（默认 50）
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def dag_guardrails_enabled() -> bool:
    return (os.environ.get("JACHIN_DAG_GUARDRAILS_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _cfg_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default


def _cfg_max_total_iterations() -> int:
    return _cfg_int("JACHIN_DAG_GR_MAX_TOTAL_ITERATIONS", 200)


def _cfg_max_total_tool_calls() -> int:
    return _cfg_int("JACHIN_DAG_GR_MAX_TOTAL_TOOL_CALLS", 400)


def _cfg_max_total_tokens() -> int:
    return _cfg_int("JACHIN_DAG_GR_MAX_TOTAL_TOKENS", 2_000_000)


def _cfg_max_nodes() -> int:
    return _cfg_int("JACHIN_DAG_GR_MAX_NODES", 50)


# ---------------------------------------------------------------------------
# SQLite 存储
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "dag_guardrails.sqlite3"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dag_budgets (
            dag_id TEXT PRIMARY KEY,
            total_iterations INTEGER NOT NULL DEFAULT 0,
            total_tool_calls INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            nodes_executed INTEGER NOT NULL DEFAULT 0,
            first_seen_ts REAL NOT NULL,
            last_updated_ts REAL NOT NULL,
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class DagBudgetState:
    dag_id: str
    total_iterations: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
    nodes_executed: int = 0
    first_seen_ts: float = field(default_factory=time.time)
    last_updated_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DagGuardrailsViolation:
    rule: str
    dag_id: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def dag_brief(self) -> str:
        return (
            f"[DagGuardrails·ExecutionBrief] DAG「{self.dag_id}」超出 {self.rule} 预算：\n"
            f"{self.message}\n"
            f"已消耗：iterations={self.context.get('total_iterations')} "
            f"tool_calls={self.context.get('total_tool_calls')} "
            f"tokens={self.context.get('total_tokens')} "
            f"nodes={self.context.get('nodes_executed')}。\n"
            "请检查 DAG 复杂度或拆分任务；本次续跑已被阻止。"
        )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load_dag_budget(dag_id: str) -> DagBudgetState:
    """读取 SQLite 中的 DAG 预算状态；未记录则返回空白状态。"""
    path = _db_path()
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=8.0)
        try:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT * FROM dag_budgets WHERE dag_id = ?", (dag_id,)
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return DagBudgetState(dag_id=dag_id)
    return DagBudgetState(
        dag_id=dag_id,
        total_iterations=int(row["total_iterations"]),
        total_tool_calls=int(row["total_tool_calls"]),
        total_tokens=int(row["total_tokens"]),
        nodes_executed=int(row["nodes_executed"]),
        first_seen_ts=float(row["first_seen_ts"]),
        last_updated_ts=float(row["last_updated_ts"]),
    )


def update_dag_budget(
    dag_id: str,
    *,
    delta_iterations: int = 0,
    delta_tool_calls: int = 0,
    delta_tokens: int = 0,
    node_completed: bool = False,
) -> DagBudgetState:
    """
    增量更新 DAG 预算（upsert）。
    在单节点 run_agent 完成后由调用方触发，传入本轮增量。
    """
    now = time.time()
    path = _db_path()
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=8.0)
        try:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT * FROM dag_budgets WHERE dag_id = ?", (dag_id,)
            ).fetchone()
            if row is None:
                new_state = DagBudgetState(
                    dag_id=dag_id,
                    total_iterations=max(0, delta_iterations),
                    total_tool_calls=max(0, delta_tool_calls),
                    total_tokens=max(0, delta_tokens),
                    nodes_executed=1 if node_completed else 0,
                    first_seen_ts=now,
                    last_updated_ts=now,
                )
                conn.execute(
                    """
                    INSERT INTO dag_budgets
                    (dag_id, total_iterations, total_tool_calls, total_tokens,
                     nodes_executed, first_seen_ts, last_updated_ts, extra_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
                    """,
                    (
                        dag_id,
                        new_state.total_iterations,
                        new_state.total_tool_calls,
                        new_state.total_tokens,
                        new_state.nodes_executed,
                        now,
                        now,
                    ),
                )
            else:
                new_state = DagBudgetState(
                    dag_id=dag_id,
                    total_iterations=int(row["total_iterations"]) + max(0, delta_iterations),
                    total_tool_calls=int(row["total_tool_calls"]) + max(0, delta_tool_calls),
                    total_tokens=int(row["total_tokens"]) + max(0, delta_tokens),
                    nodes_executed=int(row["nodes_executed"]) + (1 if node_completed else 0),
                    first_seen_ts=float(row["first_seen_ts"]),
                    last_updated_ts=now,
                )
                conn.execute(
                    """
                    UPDATE dag_budgets SET
                        total_iterations = ?,
                        total_tool_calls = ?,
                        total_tokens = ?,
                        nodes_executed = ?,
                        last_updated_ts = ?
                    WHERE dag_id = ?
                    """,
                    (
                        new_state.total_iterations,
                        new_state.total_tool_calls,
                        new_state.total_tokens,
                        new_state.nodes_executed,
                        now,
                        dag_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    logger.debug(
        "[DagGuardrails] dag_id=%s iter=%d tc=%d tok=%d nodes=%d",
        dag_id,
        new_state.total_iterations,
        new_state.total_tool_calls,
        new_state.total_tokens,
        new_state.nodes_executed,
    )
    return new_state


def reset_dag_budget(dag_id: str) -> None:
    """重置 DAG 预算（一般在 DAG 完成或人工重置时调用）。"""
    path = _db_path()
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=8.0)
        try:
            _ensure_schema(conn)
            conn.execute("DELETE FROM dag_budgets WHERE dag_id = ?", (dag_id,))
            conn.commit()
        finally:
            conn.close()


def list_active_dag_budgets(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近活跃的 DAG 预算记录（诊断端点用）。"""
    path = _db_path()
    if not path.is_file():
        return []
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=8.0)
        try:
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM dag_budgets ORDER BY last_updated_ts DESC LIMIT ?",
                (min(100, max(1, limit)),)
            ).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 检查器
# ---------------------------------------------------------------------------

class DagGuardrailsChecker:
    """
    在调度下一个 DAG 节点（或开始续跑）之前调用 check_dag_budget()。
    返回 None 表示通过，返回 DagGuardrailsViolation 表示需要阻止。
    """

    def __init__(self, dag_id: str) -> None:
        self.dag_id = dag_id
        self._max_iter = _cfg_max_total_iterations()
        self._max_tc = _cfg_max_total_tool_calls()
        self._max_tok = _cfg_max_total_tokens()
        self._max_nodes = _cfg_max_nodes()

    def check_dag_budget(self) -> DagGuardrailsViolation | None:
        """读取当前预算状态，检查是否超限。未开启时直接返回 None。"""
        if not dag_guardrails_enabled():
            return None
        try:
            state = load_dag_budget(self.dag_id)
        except Exception as e:
            logger.warning("[DagGuardrails] load failed: %s", e)
            return None

        ctx = state.to_dict()

        if state.total_iterations >= self._max_iter:
            return DagGuardrailsViolation(
                rule="max_total_iterations",
                dag_id=self.dag_id,
                message=f"总迭代次数 {state.total_iterations} 已达 DAG 上限 {self._max_iter}。",
                context=ctx,
            )
        if state.total_tool_calls >= self._max_tc:
            return DagGuardrailsViolation(
                rule="max_total_tool_calls",
                dag_id=self.dag_id,
                message=f"总工具调用次数 {state.total_tool_calls} 已达 DAG 上限 {self._max_tc}。",
                context=ctx,
            )
        if state.total_tokens >= self._max_tok:
            return DagGuardrailsViolation(
                rule="max_total_tokens",
                dag_id=self.dag_id,
                message=f"总 Token 消耗 {state.total_tokens} 已达 DAG 上限 {self._max_tok}。",
                context=ctx,
            )
        if state.nodes_executed >= self._max_nodes:
            return DagGuardrailsViolation(
                rule="max_nodes",
                dag_id=self.dag_id,
                message=f"已执行节点数 {state.nodes_executed} 已达 DAG 上限 {self._max_nodes}。",
                context=ctx,
            )
        return None

    def record_node_completion(
        self,
        *,
        iterations: int = 0,
        tool_calls: int = 0,
        tokens: int = 0,
    ) -> DagBudgetState:
        """节点完成后调用，更新 DAG 预算。"""
        return update_dag_budget(
            self.dag_id,
            delta_iterations=iterations,
            delta_tool_calls=tool_calls,
            delta_tokens=tokens,
            node_completed=True,
        )
