"""
入站确定性预检（招聘/BI/分支短路等）：从 agent_core 外提，供 run_agent 与路由层共用。
说明见 docs/L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md §3.2、docs/L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md §〇。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def apply_inbound_preflight(
    *,
    user_input: str,
    messages: list[dict[str, Any]],
    prior_messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    allowed: Optional[list[str]],
    lark_cid: str,
    gateway_bundle: Any = None,
    engine: Any = None,
) -> Optional[str]:
    """
    若应短路直接返回用户可见字符串；否则返回 None（可能已就地改写 messages[-1]）。
    惰性导入 agent_core 私有助手，避免模块循环依赖。
    gateway_bundle: 若提供，先走 Intent Registry（§4）插件化 preflight，再执行下列 HR/分支逻辑。
    """
    try:
        from l3_node.intent_gateway.bootstrap import ensure_default_intent_registry
        from l3_node.intent_gateway.registry import run_registered_preflights

        ensure_default_intent_registry()
        if gateway_bundle is not None:
            _ctx = {
                "user_input": user_input,
                "messages": messages,
                "prior_messages": prior_messages,
                "tools": tools,
                "allowed": allowed,
                "lark_cid": lark_cid,
                "engine": engine,
            }
            _reg_early = await run_registered_preflights(gateway_bundle, _ctx)
            if _reg_early is not None:
                return _reg_early
    except Exception as e:
        logger.warning("[AgentPreflight] Intent Registry preflight 跳过: %s", e)

    from l3_node.agent_core import (
        _branch_b_user_ab_choice,
        _execute_branch_b_harvest_bypass,
        _execute_publish_bypass,
        _extract_branch_b_add_task_payload,
        _extract_branch_b_scheduler_hints_from_markdown,
        _extract_jd_config_from_conversation,
        _hr_branch_b_recruitment_context,
        _hr_user_input_is_solitary_boss_job_select_line,
        _last_assistant_asks_ab_scheduler_choice,
        _load_last_jd_pending,
        tools_include_recruitment,
    )

    ui = (user_input or "").strip()

    # 招聘停止、BI 分析已迁至 l3_node.intent_gateway.registry（插件表）；此处保留 HR 文案改写与分支 B 等逻辑。

    _vague_recruitment = re.search(r"我要(?:招聘|发布|招人?)|发布(?:一个)?职位|招聘", ui)
    _has_jd_in_history = bool(_extract_jd_config_from_conversation(messages, ""))
    if _vague_recruitment and not _has_jd_in_history:
        prefix = "【系统】用户要发布职位，但尚未提供完整配置。你必须**仅做询问**，禁止臆想、禁止杜撰、禁止调用 atom_post_job_boss。请用 Final Answer 向 HR 依次询问：1.岗位名称是什么？2.社招、校招、实习还是兼职？3.薪资待遇大概多少？4.学历要求？5.经验要求？若 HR 第一轮未给某项，下一轮**单独追问**该项，直到收集齐再输出完整 JD 配置供确认。\n\n"
        messages[-1]["content"] = prefix + (user_input or "")

    if (
        tools_include_recruitment(tools)
        and _hr_user_input_is_solitary_boss_job_select_line(user_input or "")
        and _extract_branch_b_add_task_payload(messages) is None
        and not _extract_jd_config_from_conversation(prior_messages, "")
    ):
        _pfx1b = (
            "【系统】本条为 **Boss 选岗／换绑**（jd_select 与工作指针已在外层合并），**不是** HR 已确认的无人值守参数表。\n"
            "你必须 **先** 用 Final Answer 追问并等 HR 明确：① **推荐牛人 ↔ 沟通收简历** 是否交替，还是 **仅收网**；"
            "② **累计收网目标**（份）；③ **透析触发份数**（可与收网目标相同）；④ 若开交替：**每轮打招呼人数**、**轮换间隔（分钟，默认 10）**。\n"
            "**在 HR 逐项确认前禁止调用** mcp:add_automated_recruitment_task；禁止用磁盘 jd 缺省（如示例 4 份、或历史「仅收网」快照）代替 HR 决策。\n\n"
        )
        messages[-1]["content"] = _pfx1b + (messages[-1].get("content") or user_input or "")

    if re.search(r"关闭|停止|取消", ui) and re.search(r"招聘|无人值守|自动化", ui):
        prefix = "【系统】用户要求关闭招聘流程。你必须输出 Action: mcp:stop_automated_recruitment，Action Input: {\"job_name\": \"\"}，以真正停止后台任务。禁止仅回复「已关闭」而不调用工具。\n\n"
        messages[-1]["content"] = prefix + (messages[-1].get("content") or "")

    _branch_b_ctx = _hr_branch_b_recruitment_context(messages)
    _branch_b_confirm = re.search(
        r"同意|确认启动|确认|确认发布|就按这个发|^\s*同\s*$|^同$|好的|可以|开始|启动|直接发布",
        ui,
        re.I,
    )
    _ui0 = ui
    if re.search(r"收网|打招呼|推荐间隔|抓简历|无人值守|调度|透析|简历目标", _ui0) and not re.search(
        r"发布|发帖|新职位|重新发布|force_republish|再发.*职位",
        _ui0,
        re.I,
    ):
        _pfx3b = (
            "【系统】当前表述仅为 **收网/打招呼/调度参数**，与 Boss **发帖**无关。"
            "**禁止**调用 mcp:atom_post_job_boss（除非 HR 明确说重新发布职位并传 force_republish）。"
            "请用：飞书短指令（收网改成N人、打招呼改成N人、推荐间隔N分钟）、或 mcp:add_automated_recruitment_task、"
            "或 mcp:hr_scheduler_send_confirm_prompt；已发帖岗位勿再发帖。\n\n"
        )
        messages[-1]["content"] = _pfx3b + (messages[-1].get("content") or _ui0)

    _ab_choice = _branch_b_user_ab_choice(user_input or "")
    if _branch_b_ctx and _ab_choice and _last_assistant_asks_ab_scheduler_choice(messages):
        _pl: dict[str, Any] = dict(_extract_branch_b_add_task_payload(messages) or {})
        for k, v in _extract_branch_b_scheduler_hints_from_markdown(messages).items():
            if v is not None:
                _pl[k] = v
        if _pl.get("job_name"):
            _direct_ab = await _execute_branch_b_harvest_bypass(_pl, allowed_skills=allowed)
            if _direct_ab:
                return _direct_ab

    if _branch_b_ctx and _branch_b_confirm:
        _b_payload = _extract_branch_b_add_task_payload(messages)
        if _b_payload:
            _direct_b = await _execute_branch_b_harvest_bypass(_b_payload, allowed_skills=allowed)
            if _direct_b:
                return _direct_b
        _b_prefix = (
            "【系统·分支B】当前为「已有岗位·轻量收网」，**严禁** mcp:atom_post_job_boss。"
            "用户已确认启动，请立即输出 Action: mcp:add_automated_recruitment_task，"
            "Action Input 为上一轮「配置总览」中的完整 JSON（含 job_name、enable_greet_recommend、resume_collect_target 等）。\n\n"
        )
        messages[-1]["content"] = _b_prefix + (messages[-1].get("content") or user_input or "")

    _agree_match = re.search(r"同意|确认|确认发布|就按这个发|直接发布", ui)
    _agree_jd_cfg = None
    if (not _branch_b_ctx) and _agree_match:
        fallback = _extract_jd_config_from_conversation(messages, "")
        if fallback:
            try:
                obj = json.loads(fallback)
                _agree_jd_cfg = obj.get("jd_config") if isinstance(obj, dict) else obj
                if not isinstance(_agree_jd_cfg, dict):
                    _agree_jd_cfg = None
            except json.JSONDecodeError:
                pass
        if not _agree_jd_cfg:
            _agree_jd_cfg = _load_last_jd_pending(lark_cid)
            if _agree_jd_cfg:
                logger.info(
                    "[AgentPreflight] 从 pending 恢复 JD job_title=%s chat=%s",
                    _agree_jd_cfg.get("job_title"),
                    (lark_cid[:20] + "…") if len(lark_cid) > 20 else (lark_cid or "(无)"),
                )
        if _agree_jd_cfg:
            _direct_publish = await _execute_publish_bypass(
                _agree_jd_cfg, allowed_skills=allowed, lark_chat_id=lark_cid
            )
            if _direct_publish:
                return _direct_publish
            _jd_str = json.dumps({"jd_config": _agree_jd_cfg}, ensure_ascii=False)
            prefix = "【系统】用户已确认以下 JD，请直接调用 mcp:atom_post_job_boss，Action Input 填：{}\n勿输出「没有配置」或「新对话」类提示。\n\n".format(_jd_str)
            messages[-1]["content"] = prefix + (user_input or "")

    try:
        from l3_node.intent_gateway.slot_subintent_gate import maybe_subintent_slot_gate_async

        if gateway_bundle is not None:
            _sub_ctx = {
                "user_input": user_input,
                "messages": messages,
                "prior_messages": prior_messages,
                "tools": tools,
                "allowed": allowed,
                "lark_cid": lark_cid,
                "engine": engine,
            }
            _sub_slot = await maybe_subintent_slot_gate_async(gateway_bundle, _sub_ctx)
            if _sub_slot is not None:
                return _sub_slot
    except Exception as e:
        logger.debug("[AgentPreflight] SubIntent slot gate 跳过: %s", e)

    return None
