"""注册默认 Intent Registry 项（招聘停止、BI 分析），逻辑与 agent_preflight 原块一致。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_BOOTSTRAPPED = False


def _bi_capability_available() -> bool:
    try:
        from l3_node.capability_runtime_gate import capability_available

        return capability_available(
            ids=("com.jachin.bi.daily_report", "com.jachin.bi.analysis"),
            prefixes=("com.jachin.bi",),
            name_includes=("bi ", "bi每日", "bi 每日", "战报"),
            dev_env="JACHIN_DEV_LOAD_BI_CAPABILITY",
        )
    except Exception:
        return False


def _match_stop_recruitment(bundle: Any, _ctx: dict[str, Any]) -> bool:
    ui = (bundle.user_input or "").strip()
    return bool(
        re.search(
            r"(关闭|停止|取消|不要|结束|暂停)(?:所有|全部)?(?:的)?(?:招聘|无人值守|自动化)(?:流程)?|"
            r"(停止|取消)(?:所有|全部)?(?:的)?招聘(?:流程)?|"
            r"招聘(?:流程)?(?:要)?(?:停止|关闭|取消)",
            ui,
        )
    )


async def _handle_stop_recruitment(bundle: Any, _ctx: dict[str, Any]) -> Optional[str]:
    try:
        from l3_node.primitives.mcp.registry import _invoke_stop_automated_recruitment_local

        result_str = await asyncio.to_thread(_invoke_stop_automated_recruitment_local, "")
        result = json.loads(result_str)
        if result.get("ok"):
            removed = result.get("removed", [])
            msg = f"已停止所有招聘流程，已移除 {len(removed)} 个定时任务。"
            if removed:
                msg += " 后续将不再执行打招呼、抓简历、Agent 讨论或 Lark 同步。"
            return msg
    except Exception as e:
        logger.warning("[IntentRegistry] stop_automated_recruitment 失败: %s", e)
    return None


def _match_bi(bundle: Any, _ctx: dict[str, Any]) -> bool:
    if not _bi_capability_available():
        return False
    ui = (bundle.user_input or "").strip()
    try:
        from l3_node.primitives.skills.bi.bi_daily_report import is_bi_analysis_intent

        return bool(is_bi_analysis_intent(ui))
    except ImportError:
        return bool(
            re.search(
                r"BI\s*分析|bi\s*分析|帮我开始.*BI|今天的BI分析|开始BI分析|执行BI分析",
                ui,
                re.IGNORECASE,
            )
        )


def _match_slot_demo_restart(bundle: Any, _ctx: dict[str, Any]) -> bool:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        if not bool(get_intent_gateway_config().get("slot_filling_demo_restart_enabled", False)):
            return False
    except Exception:
        return False
    ui = (bundle.user_input or "").strip()
    return bool(
        re.search(
            r"重启(?:一下)?(?:云)?(?:端)?(?:的)?(?:服务器|主机|实例)|我要重启(?:服务器|主机)?",
            ui,
        )
    )


async def _handle_slot_demo_defer(_bundle: Any, _ctx: dict[str, Any]) -> Optional[str]:
    """槽位齐后交给 ReAct，不在此短路。"""
    return None


def _match_docker_cleanup(bundle: Any, _ctx: dict[str, Any]) -> bool:
    """
    远程/本机 Docker 镜像与空间清理类意图（非 Dockerfile/教程/编排编写）。
    命中后须补齐 cleanup_strategy / target_scope，避免「冗余」一词被模型擅自解读为 prune -a。
    """
    ui = (bundle.user_input or "").strip()
    if len(ui) < 6:
        return False
    if re.search(
        r"dockerfile|compose\.ya?ml|如何安装|教程|什么是|解释|原理|文档|编写|写一个",
        ui,
        re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(
            r"(?:docker|k8s|kubectl|容器).{0,48}(?:清理|清除|回收|瘦身|删(?:掉|除)?|prune|冗余|旧镜像|无用镜像)|"
            r"(?:清理|清除|回收|瘦身).{0,32}(?:docker|容器).{0,20}(?:镜像|空间|冗余)|"
            r"(?:冗余|无用|旧版).{0,16}(?:docker|容器)?.{0,12}镜像",
            ui,
            re.IGNORECASE,
        )
    )


async def _handle_bi(bundle: Any, _ctx: dict[str, Any]) -> Optional[str]:
    if not _bi_capability_available():
        return None
    try:
        from l3_node.primitives.skills.bi.bi_daily_report.main_skill import run_bi_daily_report

        result = await asyncio.to_thread(run_bi_daily_report)
        if result.get("success"):
            lines = ["✅ BI 分析已完成"]
            lines.append(f"输出文件: {len(result.get('output_paths', []))} 个")
            lines.append(f"Lark 同步: {result.get('lark_sync_ok', 0)} 个表")
            if result.get("lark_sync_errors"):
                lines.append(
                    f"同步警告: {', '.join(str(e)[:50] for e in result['lark_sync_errors'][:3])}"
                )
            if result.get("strategic_report_sent"):
                lines.append("战略分析战报: 已推送到 Lark")
            elif result.get("strategic_report_error"):
                lines.append(f"战略分析: 生成或推送异常 ({str(result['strategic_report_error'])[:40]}...)")
            return "\n".join(lines)
        return f"❌ BI 分析失败: {result.get('error', '未知错误')}"
    except Exception as e:
        logger.warning("[IntentRegistry] BI 分析失败: %s", e)
        return f"❌ BI 分析异常: {e}"


def ensure_default_intent_registry() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    from l3_node.intent_gateway.registry import get_intent_registry

    reg = get_intent_registry()
    reg.register_preflight(
        "core.stop_automated_recruitment",
        priority=10,
        match=_match_stop_recruitment,
        handle=_handle_stop_recruitment,
    )
    reg.register_preflight(
        "core.slot_gated_restart_demo",
        priority=12,
        match=_match_slot_demo_restart,
        handle=_handle_slot_demo_defer,
        required_slots=[
            {
                "name": "server_ip",
                "pattern": r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d{1,2})\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d{1,2})\b",
                "prompt_template": "好的，请提供需要重启的服务器的 IP 地址（IPv4）。",
                "hint": "IPv4 点分十进制",
            },
        ],
        defer_to_react_on_success=True,
    )
    reg.register_preflight(
        "core.docker_cleanup_gated",
        priority=11,
        match=_match_docker_cleanup,
        handle=_handle_slot_demo_defer,
        required_slots=[
            {
                "name": "cleanup_strategy",
                "pattern": r"(悬空|dangling|未使用镜像|全部未使用|prune\s*-a|prune\s+-a|"
                r"dry[\s\-_]?run|只读|先列|列出|列举|方案\s*[abc]|安全策略|激进策略|"
                r"仅清理悬空|清理所有未使用)",
                "prompt_template": "请明确清理策略：例如仅悬空镜像（dangling）、全部未使用镜像（含未跑容器的可用镜像），或只要只读列举/ dry-run。",
                "hint": "A 安全 / B 激进 / C 只读探查",
            },
            {
                "name": "target_scope",
                "pattern": r"(?i)(titan|本机|localhost|127\.0\.0\.1|远端|远程|生产|预发|staging|prod|"
                r"(?:\d{1,3}\.){3}\d{1,3}|内网|跳板|ssh\b|主机名)",
                "prompt_template": "请说明执行范围：哪台主机/环境（如 Titan、某 IP、本机）？",
                "hint": "主机名、IP 或本机",
            },
        ],
        defer_to_react_on_success=True,
    )
    if _bi_capability_available():
        reg.register_preflight(
            "skill.bi_daily_report",
            priority=20,
            match=_match_bi,
            handle=_handle_bi,
        )
    else:
        logger.info("[IntentRegistry] BI capability not installed/enabled; skip BI intent")
    try:
        from l3_node.intent_gateway.compensation_registry import get_compensation_registry

        async def _noop_compensation(payload: dict[str, Any]) -> dict[str, Any]:
            logger.info("[Saga] noop_audit_log keys=%s", list(payload.keys()))
            return {"ok": True, "noop": True}

        get_compensation_registry().register("noop_audit_log", _noop_compensation)
    except Exception as e:
        logger.debug("[IntentRegistry] 默认补偿注册跳过: %s", e)
    _BOOTSTRAPPED = True
