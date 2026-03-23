"""
L2 — HR 招聘领域子图适配器（委托给 build_hr_recruitment_dag / DAGWorkflow）。

注意：完整无人值守 tick 仍主要由 APScheduler + recruitment_scheduler 驱动；
此处供 YAML glue、显式工具调用、跨域编排「单步嵌入 HR 子图」使用。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_hr_recruitment_domain(params: dict[str, Any] | None) -> dict[str, Any]:
    p = params if isinstance(params, dict) else {}
    try:
        from l3_node.skills.hr_recruitment_dag import (
            HR_RECRUITMENT_DEFAULT_WORKFLOW_ID,
            build_hr_recruitment_dag,
        )
    except ImportError as e:
        return {"ok": False, "domain": "hr_recruitment", "error": f"HR DAG 不可用: {e}"}

    wid = str(p.get("workflow_id") or HR_RECRUITMENT_DEFAULT_WORKFLOW_ID).strip()
    include_analyze = p.get("include_analyze", True)
    if isinstance(include_analyze, str):
        include_analyze = include_analyze.lower() in ("1", "true", "yes")
    else:
        include_analyze = bool(include_analyze)

    ctx_in = p.get("context") or {}
    if not isinstance(ctx_in, dict):
        ctx_in = {}

    wf = build_hr_recruitment_dag(wid, include_analyze=include_analyze)
    try:
        result_ctx = wf.run(wid, dict(ctx_in))
    except Exception as e:
        logger.exception("[Orchestration L2] HR DAG 执行异常")
        return {
            "ok": False,
            "domain": "hr_recruitment",
            "workflow_id": wid,
            "error": str(e),
        }

    keys = list(result_ctx.keys()) if isinstance(result_ctx, dict) else []
    preview: dict[str, Any] = {}
    if isinstance(result_ctx, dict):
        for k in ("greeted_count", "resume_count", "job_name", "job_folder", "jd_config_path"):
            if k in result_ctx:
                preview[k] = result_ctx.get(k)

    return {
        "ok": True,
        "domain": "hr_recruitment",
        "workflow_id": wid,
        "include_analyze": include_analyze,
        "context_keys_sample": keys[:40],
        "context_preview": preview,
    }
