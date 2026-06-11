"""PMO 飞书推送审计：写入标准日志 + PMO 人类可读 debug 文件。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_pmo_lark_push(
    *,
    tool: str,
    chat_id: str,
    status: str,
    message_id: str = "",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """
    推送时打印完整 chat_id（oc_…），并同步追加到 ``pmo_copilot_*.txt``（若已开启）。
    """
    cid = (chat_id or "").strip() or "(unknown)"
    st = (status or "").strip().lower() or "unknown"
    mid = (message_id or "").strip()
    err = (error or "").strip()
    tool_name = (tool or "pmo_push").strip()

    if st == "success":
        logger.info(
            "[PMO Lark Push] tool=%s chat_id=%s status=success message_id=%s",
            tool_name,
            cid,
            mid or "(none)",
        )
    else:
        logger.warning(
            "[PMO Lark Push] tool=%s chat_id=%s status=%s error=%s",
            tool_name,
            cid,
            st,
            err or st,
        )

    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_lark_push_line

        append_pmo_lark_push_line(
            tool=tool_name,
            chat_id=cid,
            status=st,
            message_id=mid,
            error=err,
            extra=extra,
        )
    except Exception:
        logger.debug("[PMO Lark Push] debug file append skipped", exc_info=True)


def log_pmo_lark_push_plan(*, tool: str, chat_ids: list[str], debug: dict[str, str] | None = None) -> None:
    """推送前打印计划目标群列表。"""
    ids = [str(x).strip() for x in chat_ids if str(x).strip()]
    target_txt = ", ".join(ids) if ids else "(none)"
    logger.info("[PMO Lark Push] tool=%s plan chat_ids=[%s]", (tool or "pmo_push").strip(), target_txt)
    if debug:
        logger.info("[PMO Lark Push] env/session debug: %s", debug)
    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_lark_push_plan_line

        append_pmo_lark_push_plan_line(tool=(tool or "pmo_push").strip(), chat_ids=ids, debug=debug)
    except Exception:
        pass
