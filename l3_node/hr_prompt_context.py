"""
聚合注入 Agent system prompt：HR 专用 task_plan/progress、多岗状态摘要、审计尾。
与通用 get_planning_context_for_prompt（workspace/task_plan.md）互补，非替代。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_hr_recruitment_runtime_context_for_prompt(
    *,
    audit_lines: int = 12,
    max_jobs_digest: int = 6,
    digest_chars: int = 220,
) -> str:
    """
    返回一段可拼入 system prompt 的 HR 运行时上下文；无内容时返回空串。
    """
    parts: list[str] = []
    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer, list_hr_recruitment_job_entries

        ptr = get_hr_recruitment_workflow_pointer()
        entries = list_hr_recruitment_job_entries()
        if not entries:
            jf = (ptr.get("primary_job_folder") or ptr.get("job_folder") or "").strip()
            jn = (ptr.get("job_name") or "").strip()
            if jf or jn:
                entries = [
                    {
                        "job_folder": jf or jn,
                        "job_name": jn or jf,
                        "jd_config_path": (ptr.get("jd_config_path") or "").strip(),
                    }
                ]
        if entries:
            dig_lines = ["【在册招聘岗位快照（指针 + 调度状态摘要）】"]
            for ent in entries[:max_jobs_digest]:
                jn = (ent.get("job_name") or ent.get("job_folder") or "").strip()
                jf = (ent.get("job_folder") or "").strip()
                if not jn and not jf:
                    continue
                line = _one_job_digest_line(jn or jf, digest_chars)
                dig_lines.append(f"- {line}")
            if len(dig_lines) > 1:
                parts.append("\n".join(dig_lines))
    except Exception as e:
        logger.debug("[HRPromptCtx] digest 跳过: %s", e)

    try:
        from l3_node.hr_audit_log import format_hr_recruitment_audit_for_prompt

        aud = format_hr_recruitment_audit_for_prompt(audit_lines).strip()
        if aud:
            parts.append(aud)
    except Exception as e:
        logger.debug("[HRPromptCtx] audit 跳过: %s", e)

    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def _one_job_digest_line(job_name: str, max_chars: int) -> str:
    try:
        import sys
        from pathlib import Path

        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        root = _get_hr_recruitment_plugin_root()
        if not root:
            return f"{job_name}（调度器未加载）"
        sroot = str(root.resolve())
        if sroot not in sys.path:
            sys.path.insert(0, sroot)
        from recruitment_scheduler import get_recruitment_status_digest

        d: dict[str, Any] = get_recruitment_status_digest(job_name)
        if not d.get("has_active_job"):
            return f"{job_name}：无活跃岗位记录"
        bits = [
            f"岗={d.get('job_name') or job_name}",
            f"pendingPDF≈{d.get('pending_pdf_count')}/{d.get('collect_cap')}",
            f"待透析≈{d.get('unprocessed_for_analysis')}",
            f"调度={'开' if d.get('scheduler_active') else '关'}",
            f"全局停={'是' if d.get('globally_stopped') else '否'}",
        ]
        s = "；".join(str(x) for x in bits)
        return s[:max_chars] + ("…" if len(s) > max_chars else "")
    except Exception as e:
        return f"{job_name}（摘要失败:{e})"
