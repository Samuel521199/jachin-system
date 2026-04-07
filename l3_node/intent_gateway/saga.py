"""
§10.1 / §11.3 Saga 协调器骨架：记录正向成功步，失败时按逆拓扑调用白名单补偿。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from l3_node.intent_gateway.compensation_registry import get_compensation_registry

logger = logging.getLogger(__name__)


@dataclass
class SagaStepRecord:
    node_id: str
    compensation_action_id: str
    payload: dict[str, Any] = field(default_factory=dict)


class SagaCoordinator:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._committed: list[SagaStepRecord] = []

    def record_forward_committed(self, rec: SagaStepRecord) -> None:
        if rec.compensation_action_id:
            self._committed.append(rec)

    async def compensate_after_failure(self, error: str) -> dict[str, Any]:
        reg = get_compensation_registry()
        results: list[dict[str, Any]] = []
        for rec in reversed(self._committed):
            aid = rec.compensation_action_id
            if not aid:
                continue
            fn = reg.get(aid)
            if fn is None:
                results.append(
                    {
                        "node_id": rec.node_id,
                        "ok": False,
                        "error": f"unknown_compensation_action_id:{aid}",
                    }
                )
                logger.warning("[Saga] run_id=%s 未知补偿 ID %s", self.run_id, aid)
                continue
            try:
                out = await fn({"run_id": self.run_id, "node_id": rec.node_id, **rec.payload})
                results.append({"node_id": rec.node_id, "ok": True, "result": out})
            except Exception as e:
                results.append({"node_id": rec.node_id, "ok": False, "error": str(e)})
                logger.exception("[Saga] 补偿失败 run_id=%s node=%s", self.run_id, rec.node_id)
        return {"run_id": self.run_id, "trigger_error": error, "compensations": results}
