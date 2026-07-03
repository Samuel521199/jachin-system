"""PMO Copilot agent policy and guards.

This module intentionally keeps PMO business-specific ReAct guards out of
``agent_core.py``.  The core agent calls the wrapper functions below; the PMO
skill/runtime package can own and evolve these rules independently.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from l3_node.engine.hooks_pipeline import PipelineContext

logger = logging.getLogger(__name__)

def _pmo_lark_push_guard_channel_active(ctx: PipelineContext) -> bool:
    """PMO-Copilot CLI 或网关注入 PMO Skill 时启用「禁止谎称已发飞书」守卫。"""
    ch = str(ctx.metadata.get("_implicit_channel") or "").strip()
    if ch == "pmo_copilot_cli":
        return True
    inj = str(ctx.metadata.get("_gw_inject_stored") or "")
    return "PMO-Copilot" in inj or "pmo-copilot-enterprise" in inj


_PMO_MD_SECTION_DEMAND = ("需求进度全览", "需求进度", "📊 需求", "📊")
_PMO_MD_SECTION_PEOPLE = ("人员任务矩阵", "人员预警矩阵", "人员任务", "👥")
_PMO_MD_SECTION_VERSION = ("版本发布需求映射", "版本需求映射", "版本映射", "📦")
_PMO_MD_SECTION_SNIPPET_CHARS = 600


def _pmo_markdown_extract_content(inp: str) -> str:
    try:
        args = json.loads(inp) if (inp or "").strip().startswith("{") else {}
        return str(args.get("markdown_content") or "")
    except Exception:
        return str(inp or "")


def _pmo_markdown_section_has_gfm_table(mc: str, section_keywords: tuple[str, ...]) -> bool:
    """区块标题存在且其后片段含 GFM 管道符（允许空数据占位行）。"""
    if not mc:
        return False
    for kw in section_keywords:
        idx = mc.find(kw)
        if idx < 0:
            continue
        snippet = mc[idx : idx + _PMO_MD_SECTION_SNIPPET_CHARS]
        if "|" in snippet:
            return True
    return False


def _pmo_branch_a_notifier_markdown_is_complete(inp: str) -> bool:
    """分支 A 的 notifier markdown_content 必须含三张核心表，否则视为残卡，不标记成功。"""
    from l3_node.pmo_report_format import pmo_demand_table_column_issues

    mc = _pmo_markdown_extract_content(inp)
    if not mc:
        return False
    if not (
        _pmo_markdown_section_has_gfm_table(mc, _PMO_MD_SECTION_DEMAND)
        and _pmo_markdown_section_has_gfm_table(mc, _PMO_MD_SECTION_PEOPLE)
        and _pmo_markdown_section_has_gfm_table(mc, _PMO_MD_SECTION_VERSION)
    ):
        return False
    return not pmo_demand_table_column_issues(mc, _PMO_MD_SECTION_DEMAND)


def _pmo_notifier_markdown_missing_sections(inp: str) -> list[str]:
    from l3_node.pmo_report_format import pmo_demand_table_column_issues

    mc = _pmo_markdown_extract_content(inp)
    missing: list[str] = []
    if not any(k in mc for k in _PMO_MD_SECTION_DEMAND):
        missing.append("📊 需求进度全览")
    elif not _pmo_markdown_section_has_gfm_table(mc, _PMO_MD_SECTION_DEMAND):
        missing.append("📊 需求进度全览（须有 GFM 表格 |）")
    else:
        col_issues = pmo_demand_table_column_issues(mc, _PMO_MD_SECTION_DEMAND)
        if col_issues:
            missing.append(
                "📊 需求进度全览（列须且仅为：优先级|需求名称|时间跨度|参与人|完成度|状态；"
                f"当前问题：{'；'.join(col_issues)}；见 PMO_WAR_REPORT_LAYOUT_CONTRACT / format_demand_table_gfm_row）"
            )
    if not any(k in mc for k in _PMO_MD_SECTION_PEOPLE):
        missing.append("👥 人员任务矩阵")
    elif not _pmo_markdown_section_has_gfm_table(mc, _PMO_MD_SECTION_PEOPLE):
        missing.append("👥 人员任务矩阵（须有 GFM 表格 |）")
    if not any(k in mc for k in _PMO_MD_SECTION_VERSION):
        missing.append("📦 版本发布需求映射")
    elif not _pmo_markdown_section_has_gfm_table(mc, _PMO_MD_SECTION_VERSION):
        missing.append("📦 版本发布需求映射（须有 GFM 表格 |，Version Goal 全空时也须占位行）")
    return missing


def _pmo_notifier_markdown_section_format_examples(missing: list[str]) -> str:
    """为缺失区块附最小 GFM 模板，便于 Agent 一次补全。"""
    examples: list[str] = []
    for item in missing:
        if "需求进度" in item or "📊" in item:
            examples.append(
                "📊 最简示例（图1~5 五列；P0 在需求名称格首）：\n"
                "| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| 【P0】Epic 示例 | 05/18→05/25 | Ethan; Celine | [▓▓▓▓▓░░░░░] 51% | "
                "🔵 开发/验收 · 技术开发 |"
            )
        elif "人员" in item or "👥" in item:
            from l3_node.pmo_report_format import (
                PMO_PERSONNEL_MATRIX_ROW_SORT_SPEC,
                PMO_PERSONNEL_TASK_CELL_FORMAT_SPEC,
            )

            examples.append(
                "👥 最简示例（行序：🚨 在前，✅ 在最后；任务列每行一条，用 <br> 换行，禁止 **）：\n"
                "| 人员 | 负责需求（含优先级） | 状态预警 |\n"
                "| --- | --- | --- |\n"
                "| Gavin | 【P0】任务 A · 开发中<br>【P1】任务 B · 待开始 | "
                "🚨 进度落后（时间已过约 80%，完成 0%）|\n"
                "| Baojing | 【P1】任务 C · 开发中 | ✅ 正常（本周计划 4/完成 3）|\n"
                f"{PMO_PERSONNEL_MATRIX_ROW_SORT_SPEC}\n"
                f"{PMO_PERSONNEL_TASK_CELL_FORMAT_SPEC}"
            )
        elif "版本" in item or "📦" in item:
            examples.append(
                "📦 最简示例（Version Goal 全空时仍须建表）：\n"
                "| 视图 | 记录总数 | Version Goal 填写数 | 填写率 | 说明 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| vew8TxMcSh / vewL9Mofgd | 100 | 0 | 0% | ⚠️ 原表字段全空，建议 PMO 补充版本目标 |"
            )
    return "\n\n".join(examples)


def _pmo_extract_sql_from_tool_inp(inp: str) -> str:
    try:
        from l3_node.tools.pmo_db_tools import parse_db_query_action_input

        return str(parse_db_query_action_input(inp).get("sql") or "")
    except Exception:
        return str(inp or "")


def _pmo_sql_is_step1_map(sql: str) -> bool:
    sl = (sql or "").lower()
    return "pmo_views_meta" in sl and ("columns_json" in sl or "record_count" in sl)


def _pmo_extract_thought_from_assistant(response: str) -> str:
    text = str(response or "")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("thought:"):
            idx = text.find(line)
            if idx >= 0:
                return text[idx + len(line.split(":", 1)[0]) + 1 :].strip()
            return stripped.split(":", 1)[1].strip()
    m = re.search(r"Thought:\s*(.+)", text, re.I | re.S)
    return m.group(1).strip() if m else ""


def _pmo_assistant_has_gfm_draft(response: str) -> bool:
    thought = _pmo_extract_thought_from_assistant(response)
    if _pmo_thought_has_gfm_draft(thought):
        return True
    return _pmo_thought_has_gfm_draft(str(response or ""))


def _pmo_thought_has_gfm_draft(thought: str) -> bool:
    if not thought or "待填充" in thought:
        return False
    pipe_lines = [ln for ln in thought.splitlines() if "|" in ln]
    return len(pipe_lines) >= 2


def _pmo_extract_gfm_draft_fingerprint(text: str) -> str:
    """Thought 中 GFM 数据行指纹，用于检测机械复制上轮草稿。"""
    data_lines: list[str] = []
    for ln in str(text or "").splitlines():
        stripped = ln.strip()
        if "|" not in stripped:
            continue
        if re.match(r"^\|\s*[-:]+\s*\|", stripped):
            continue
        data_lines.append(stripped)
    if not data_lines:
        return ""
    payload = "\n".join(data_lines)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _pmo_markdown_fix_phase(ctx: PipelineContext) -> str | None:
    phase = ctx.metadata.get("_pmo_markdown_fix_phase")
    if phase in ("supplemental", "final"):
        return str(phase)
    if ctx.metadata.get("_pmo_markdown_fix_only"):
        return "final"
    return None


def _pmo_markdown_fix_supplemental_remaining(ctx: PipelineContext) -> int:
    used = int(ctx.metadata.get("_pmo_markdown_fix_supplemental_count") or 0)
    return max(0, PMO_MARKDOWN_FIX_SUPPLEMENTAL_MAX - used)


def _pmo_assistant_thought_has_all_three_tables(assistant_response: str) -> bool:
    """仅用于组装轮门禁：检查 Thought 是否已含三张 GFM 表（不提取、不拼装内容）。"""
    thought = _pmo_extract_thought_from_assistant(assistant_response)
    source = thought or str(assistant_response or "")
    if not source:
        return False
    return (
        _pmo_markdown_section_has_gfm_table(source, _PMO_MD_SECTION_DEMAND)
        and _pmo_markdown_section_has_gfm_table(source, _PMO_MD_SECTION_PEOPLE)
        and _pmo_markdown_section_has_gfm_table(source, _PMO_MD_SECTION_VERSION)
    )


def _pmo_assembly_phase(ctx: PipelineContext) -> str | None:
    """组装轮状态：writing=只写 Thought；ready=可推送 markdown。"""
    phase = ctx.metadata.get("_pmo_assembly_phase")
    if phase in ("writing", "ready"):
        return str(phase)
    return None


def _pmo_ensure_assembly_phase(ctx: PipelineContext) -> None:
    if not _pmo_db_analysis_mode(ctx) or _pmo_branch_a_delivery_complete(ctx):
        return
    if _pmo_markdown_fix_phase(ctx):
        return
    if not _pmo_branch_a_push_prerequisites_met(ctx):
        return
    if not _pmo_assembly_phase(ctx):
        ctx.metadata["_pmo_assembly_phase"] = "writing"


def _pmo_sync_assembly_phase_from_thought(ctx: PipelineContext, assistant_response: str) -> None:
    if _pmo_assembly_phase(ctx) != "writing":
        return
    if _pmo_assistant_thought_has_all_three_tables(assistant_response):
        ctx.metadata["_pmo_assembly_phase"] = "ready"


def _pmo_markdown_incomplete_system_nudge(
    ctx: PipelineContext, observation: str, tool: str, missing: list[str] | None = None
) -> str:
    """markdown_incomplete 时在 Observation 后追加系统校验提示（不解析 Thought 代劳组装）。"""
    if not _pmo_lark_push_guard_channel_active(ctx):
        return ""
    if _pmo_canonical_tool_id(tool) != "atom_lark_notifier":
        return ""
    reason = ""
    try:
        if (observation or "").strip().startswith("{"):
            payload = json.loads(observation)
            reason = str(payload.get("reason") or payload.get("error") or "")
            if not missing:
                missing = payload.get("missing_sections")
    except Exception:
        pass
    if reason not in ("markdown_incomplete", "pmo_premature_notifier_blocked"):
        return ""
    missing_txt = "、".join(missing) if missing else "§1.4 三张核心表"
    return (
        "\n\n【系统校验 · PMO 战报组装】\n"
        f"你的 `markdown_content` 缺少：{missing_txt}。\n"
        "**宿主不会**从 Thought 自动提取或拼装 markdown；你必须亲自动手完成渲染。\n"
        "请立刻翻阅你前 10 轮对话中的 **Thought 历史与 Observation**，"
        "把其中已写的 GFM 草稿数据完整整理为三张表："
        "「📊 需求进度全览」「👥 人员任务矩阵」「📦 版本发布需求映射」，"
        "每表须含 `| 列 |` 表头与至少 1 行数据（缺口用 ⚠️ 占位）。\n"
        "下一步：**不要**调用 core:db_query；"
        "将完整 GFM 三表 **全文** 写入 atom_lark_notifier 的 `markdown_content` 后再推送。"
    )


def _pmo_sql_is_step2_sample(sql: str) -> bool:
    sl = (sql or "").lower()
    if "pmo_views_meta" in sl or "count(" in sl or "group by" in sl:
        return False
    return bool(
        "pmo_raw_records" in sl
        and re.search(r"\blimit\s+1\b", sl)
        and "fields" in sl
    )


def _pmo_sql_is_supplemental_allowed(sql: str) -> bool:
    sl = (sql or "").lower()
    if not (sql or "").strip():
        return False
    if _pmo_sql_is_step1_map(sql):
        return False
    if _pmo_sql_is_step2_sample(sql):
        return False
    if "count(" in sl and "group by" in sl:
        return True
    if "json_each" in sl and "vewcz1ffji" in sl:
        return True
    if "[0].text" in sql and "父记录" in sql and "vewpi8lyyw" in sl:
        return True
    if "fields like" in sl and "vewcz1ffji" in sl:
        return True
    lim = re.search(r"\blimit\s+(\d+)\b", sl)
    if lim and int(lim.group(1)) <= 20:
        return True
    return False


def _pmo_analysis_incomplete_recovery_hint(
    ctx: PipelineContext, qn: int, missing_probes: list[str]
) -> str:
    missing_txt = "、".join(missing_probes) if missing_probes else "无"
    if qn >= PMO_BRANCH_A_MIN_DB_QUERIES:
        return (
            f"你已执行 {qn} 次 db_query，分析 Observation 应已在上下文中。"
            f"❌ **禁止从 Step1 重跑七步**；请仅补跑缺失探针：{missing_txt}，"
            "或直接进入 §1.4 三表组装后再推送。"
        )
    if qn > 0:
        return (
            f"已执行 {qn}/{PMO_BRANCH_A_MIN_DB_QUERIES} 次 db_query。"
            f"请继续完成缺失探针：{missing_txt}；"
            "禁止重复已完成的步骤（尤其 Step1 数据地图）。"
        )
    return ""


def _pmo_notifier_no_rerun_analysis_hint(ctx: PipelineContext) -> str:
    """分析探针已满足时，提示无需重跑查库。"""
    qn = int(ctx.metadata.get("_pmo_db_query_count") or 0)
    if qn < PMO_BRANCH_A_MIN_DB_QUERIES:
        return ""
    if _pmo_branch_a_missing_cross_analysis(ctx):
        return ""
    probes = _pmo_ensure_analysis_probes(ctx)
    core_ok = all(probes.get(k) for k in ("sprint", "status", "personnel", "version", "epic"))
    if not core_ok or not probes.get("personnel_kanban"):
        return ""
    return (
        "❌ **禁止重跑 Step 1–7 的 core:db_query**。"
        "已完成的分析 Observation 仍然有效；"
        "请直接基于已有结果组装 §1.4 三表 markdown_content 后再推送。"
    )


def _pmo_markdown_action_input_hint() -> str:
    return (
        "📋 注意：`markdown_content` 是 Action Input JSON 参数字段，"
        "Thought 里的表格草稿 **不会自动传入** notifier；"
        "须将完整 GFM 三表文本 **全文写入** `markdown_content`。"
    )


def _pmo_append_react_budget_warning(
    ctx: PipelineContext,
    observation: str,
    *,
    iteration: int,
    max_iterations: int,
) -> str:
    """PMO 分支 A：剩余轮次不足时注入节奏警告，避免 panic SQL。"""
    if not _pmo_db_analysis_mode(ctx) or _pmo_branch_a_delivery_complete(ctx):
        return observation
    remaining = max(0, int(max_iterations) - int(iteration) - 1)
    if remaining > 3 or _pmo_branch_a_push_prerequisites_met(ctx):
        return observation
    return (
        f"{observation}\n\n"
        f"⚠️ **预算警告**：剩余 {remaining} 轮。"
        "避免复杂 JOIN/子查询——跨视图检验请拆成 Step 6a + 6b 两步简单查询。"
        "若本轮无法完成剩余步骤，用 ⚠️ 标注缺口后直接进入 §1.4 三表组装与推送。"
    )


def _pmo_ensure_views_queried(ctx: PipelineContext) -> set[str]:
    raw = ctx.metadata.get("_pmo_views_queried")
    if isinstance(raw, set):
        return raw
    if isinstance(raw, list):
        out = {str(x).strip() for x in raw if str(x).strip()}
        ctx.metadata["_pmo_views_queried"] = out
        return out
    out: set[str] = set()
    ctx.metadata["_pmo_views_queried"] = out
    return out


def _pmo_extract_view_ids_from_sql(sql: str) -> set[str]:
    ids: set[str] = set()
    for m in re.finditer(r"['\"]?(vew[A-Za-z0-9]{6,})['\"]?", sql or ""):
        ids.add(m.group(1))
    for m in re.finditer(r"source_view\s*=\s*['\"]([^'\"]+)['\"]", sql or "", re.I):
        vid = m.group(1).strip()
        if vid.startswith("vew"):
            ids.add(vid)
    return ids


def _pmo_sql_excludes_dept_epic_placeholders(sql: str) -> bool:
    sl = (sql or "").lower()
    if any(x in sql for x in ("开发", "美术", "产品")):
        if "not in" in sl or "not like" in sl or "!=" in sql or "<>" in sql:
            return True
    # 顶层 Epic：父记录链接 text 为空（非 json_extract(父记录) IS NULL）
    if "父记录" in sql and "[0].text" in sql and re.search(r"\bis null\b", sl):
        return True
    return False


def _pmo_sql_uses_invalid_parent_null_epic_filter(sql: str) -> bool:
    """json_extract(fields, '$.\"父记录\"') IS NULL 在镜像库恒 0 行。"""
    if "父记录" not in sql:
        return False
    if "[0].text" in sql:
        return False
    return bool(re.search(r"\bis null\b", (sql or "").lower()))


def _pmo_branch_a_missing_cross_analysis(ctx: PipelineContext) -> list[str]:
    missing: list[str] = []
    vq = _pmo_ensure_views_queried(ctx)
    view_labels = {
        "vewpI8lyYw": "开发计划核心(vewpI8lyYw)",
        "vewCz1FFJi": "人工看板人员SSOT(vewCz1FFJi)",
    }
    for vid in sorted(PMO_BRANCH_A_REQUIRED_CROSS_VIEWS - vq):
        missing.append(view_labels.get(vid, vid))
    if not (vq & PMO_BRANCH_A_PRODUCT_VIEW_ALTS):
        missing.append("产品视图(vew8TxMcSh 或 vewL9Mofgd)")
    if len(vq) < PMO_BRANCH_A_CROSS_MIN_VIEW_COUNT:
        missing.append(
            f"至少 {PMO_BRANCH_A_CROSS_MIN_VIEW_COUNT} 个不同 source_view（当前 {len(vq)}）"
        )
    probes = _pmo_ensure_analysis_probes(ctx)
    if not probes.get("personnel_kanban"):
        missing.append(
            f"人员矩阵须查 {PMO_BRANCH_A_PERSONNEL_SSOT_VIEW}（禁止仅用 vewpI8lyYw 负责人条数）"
        )
    return missing


def _pmo_branch_a_missing_probes(ctx: PipelineContext) -> list[str]:
    probes = _pmo_ensure_analysis_probes(ctx)
    labels = {
        "sprint": "Sprint/工作周期",
        "status": "状态分布(GROUP BY 聚合)",
        "personnel": "人员任务(json_each 明细)",
        "version": "Version Goal 填写率(COUNT 聚合)",
        "epic": "Epic/顶层需求(排除部门占位)",
    }
    missing = [labels[k] for k in ("sprint", "status", "personnel", "version", "epic") if not probes.get(k)]
    if not (probes.get("cross_view_6a") and probes.get("cross_view_6b")):
        missing.append("跨视图矛盾检验(Step6a vewpI8lyYw 延期 TOP5 + Step6b vewCz1FFJi 逐条核对)")
    missing.extend(_pmo_branch_a_missing_cross_analysis(ctx))
    seen: set[str] = set()
    deduped: list[str] = []
    for item in missing:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _pmo_notifier_extract_markdown_and_title(inp: str) -> tuple[str, str]:
    raw = (inp or "").strip()
    if not raw.startswith("{"):
        return "", ""
    try:
        args = json.loads(raw)
        if not isinstance(args, dict):
            return "", ""
        return str(args.get("markdown_content") or ""), str(args.get("title") or "")
    except json.JSONDecodeError:
        return "", ""


def _pmo_notifier_falsely_claims_critical_sync_failure(markdown_content: str, title: str) -> bool:
    """BI 已成功写入磁盘时，禁止卡片谎称核心种子表『未同步/同步失败』（多为读错了无前导零的文件名）。"""
    t = f"{markdown_content}\n{title}"
    deny = ("未成功同步", "未能同步", "同步失败", "同步可能没有完全成功", "没有被正确同步")
    if not any(p in t for p in deny):
        return False
    scopes = (
        "开发计划核心版本需求",
        "vewpI8lyYw",
        "需求池",
        "人工看板",
        "按员工任务",
        "vewCz1FFJi",
        "产品任务需求完成度",
        "tblfK9gk6vTQpJtB",
        "tblNdv7DIlycuqxp",
    )
    return any(s in t for s in scopes)


def _pmo_branch_a_blocked_premature_lark_observation(inp: str, ctx: PipelineContext) -> str | None:
    """
    分支 A：不向飞书中途推送试错/半成品。返回 JSON observation 字符串则跳过真实 notifier 调用。
    """
    chat_blk = _pmo_blocked_invalid_war_report_chat_observation("mcp:atom_lark_notifier", inp, ctx)
    if chat_blk:
        return chat_blk
    timeout_blk = _pmo_macro_dashboard_push_timeout_blocks_notifier(ctx)
    if timeout_blk:
        return timeout_blk
    if _pmo_db_analysis_mode(ctx):
        chats_ok = [str(x).strip() for x in (ctx.metadata.get("_pmo_notifier_chats_success") or []) if str(x).strip()]
        cid = _pmo_notifier_chat_id_from_inp(inp) or _pmo_resolve_primary_chat_id(ctx)
        if _pmo_branch_a_delivery_complete(ctx) and cid in chats_ok:
            return json.dumps(
                {
                    "status": "error",
                    "error": "pmo_duplicate_delivery_blocked",
                    "msg": (
                        "【宿主拦截】该群本轮已成功推送过完整战报，禁止重复发送。"
                        "请输出 ≤3 句 Final Answer 确认已推送（**禁止**向用户提及监控群或 oc_ chat_id）。"
                    ),
                },
                ensure_ascii=False,
            )
        if not _pmo_branch_a_notifier_markdown_is_complete(inp):
            missing = _pmo_notifier_markdown_missing_sections(inp)
            examples = _pmo_notifier_markdown_section_format_examples(missing)
            no_rerun = _pmo_notifier_no_rerun_analysis_hint(ctx)
            qn_md = int(ctx.metadata.get("_pmo_db_query_count") or 0)
            prev_preview = str(ctx.metadata.get("_pmo_last_notifier_markdown_preview") or "").strip()
            mc_now = _pmo_markdown_extract_content(inp)
            try:
                block_count = int(ctx.metadata.get("_pmo_notifier_block_count") or 0) + 1
            except (TypeError, ValueError):
                block_count = 1
            ctx.metadata["_pmo_notifier_block_count"] = block_count
            if no_rerun or qn_md >= PMO_BRANCH_A_MIN_DB_QUERIES:
                phase = _pmo_markdown_fix_phase(ctx)
                if not phase:
                    ctx.metadata["_pmo_markdown_fix_phase"] = "supplemental"
                elif phase == "supplemental" and block_count >= 2:
                    ctx.metadata["_pmo_markdown_fix_phase"] = "final"
                if _pmo_markdown_fix_phase(ctx) == "final":
                    ctx.metadata["_pmo_markdown_fix_only"] = True
            msg_parts: list[str] = []
            if no_rerun:
                msg_parts.append(no_rerun)
            msg_parts.extend([
                "【宿主拦截】本条 **未发往飞书**。",
                f"`markdown_content` 缺少：{('、'.join(missing) if missing else '§1.4 三张核心表')}。",
                "须同时含「📊 需求进度全览」「👥 人员任务矩阵」「📦 版本发布需求映射」"
                "及各自 GFM 表格（| 列 |）。",
                "即使 Version Goal 等字段在原表全空，也须在 📦 区块建占位 GFM 表并写 ⚠️ 数据待补，禁止只写一行文字。",
                _pmo_markdown_action_input_hint(),
            ])
            fix_phase = _pmo_markdown_fix_phase(ctx)
            if fix_phase == "supplemental":
                remaining = _pmo_markdown_fix_supplemental_remaining(ctx)
                msg_parts.append(
                    f"📝 **补缺模式**（剩余 {remaining}/{PMO_MARKDOWN_FIX_SUPPLEMENTAL_MAX} 次补缺 SQL）："
                    "允许 COUNT+GROUP BY 聚合、json_each 人员明细、Step6b LIKE 核对、LIMIT≤20；"
                    "❌ 禁止 Step1 地图 / Step2 LIMIT 1 样本重跑。"
                    "优先将 Thought 草稿全文写入 markdown_content 再推送。"
                )
                probes = _pmo_ensure_analysis_probes(ctx)
                if any("📊" in m for m in missing) and not probes.get("status"):
                    msg_parts.append(
                        "可先做 1 次 Step5 状态 GROUP BY 聚合补缺 SQL，再组装 📊 表。"
                    )
            elif fix_phase == "final":
                msg_parts.append(
                    "⛔ **最终整合阶段**：禁止 core:db_query；"
                    "请基于已有 Observation 补写完整 GFM 三表 markdown_content 后重新推送。"
                )
            if prev_preview:
                msg_parts.append(
                    f"上轮 markdown_content 摘要（请在其基础上补缺，勿整段重写）：{prev_preview!r}"
                )
            if mc_now.strip():
                ctx.metadata["_pmo_last_notifier_markdown_preview"] = mc_now[:200]
            if examples:
                msg_parts.append(f"格式参考：\n{examples}")
            if not no_rerun and fix_phase not in ("supplemental", "final"):
                msg_parts.append("请补全 markdown_content 后再推送，勿重复已完成的查库步骤。")
            return json.dumps(
                {
                    "status": "error",
                    "error": "pmo_premature_notifier_blocked",
                    "reason": "markdown_incomplete",
                    "missing_sections": missing,
                    "markdown_fix_phase": fix_phase,
                    "msg": " ".join(msg_parts),
                },
                ensure_ascii=False,
            )
        if not _pmo_branch_a_push_prerequisites_met(ctx):
            qn = int(ctx.metadata.get("_pmo_db_query_count") or 0)
            missing_probes = _pmo_branch_a_missing_probes(ctx)
            no_rerun = _pmo_notifier_no_rerun_analysis_hint(ctx)
            recovery = _pmo_analysis_incomplete_recovery_hint(ctx, qn, missing_probes)
            try:
                ctx.metadata["_pmo_notifier_block_count"] = (
                    int(ctx.metadata.get("_pmo_notifier_block_count") or 0) + 1
                )
            except (TypeError, ValueError):
                ctx.metadata["_pmo_notifier_block_count"] = 1
            msg_parts = [
                "【宿主拦截】分析未完成，禁止推送。",
                f"当前 core:db_query={qn}/{PMO_BRANCH_A_MIN_DB_QUERIES}；",
                f"探针/交叉分析缺口：{('、'.join(missing_probes) if missing_probes else '无')}。",
            ]
            if recovery:
                msg_parts.append(recovery)
            else:
                msg_parts.extend([
                    "须：① 先 SELECT pmo_views_meta 读 columns_json；",
                    "② 至少查 vewpI8lyYw + vewCz1FFJi + 产品视图；",
                    "③ 人员矩阵以 vewCz1FFJi 为 SSOT（json_each 解析负责人）；",
                    "④ Epic 须排除开发/美术/产品部门占位行；",
                    "⑤ 完成 Sprint/状态/版本探针后补全 §1.4 三表再 atom_lark_notifier。",
                ])
            if no_rerun:
                msg_parts.append(no_rerun)
            return json.dumps(
                {
                    "status": "error",
                    "error": "pmo_premature_notifier_blocked",
                    "reason": "analysis_incomplete",
                    "db_query_count": qn,
                    "min_db_queries": PMO_BRANCH_A_MIN_DB_QUERIES,
                    "missing_probes": missing_probes,
                    "msg": " ".join(msg_parts),
                },
                ensure_ascii=False,
            )
        return None
    if not _pmo_branch_a_requires_bi_pull(ctx):
        return None
    mc, title = _pmo_notifier_extract_markdown_and_title(inp)
    if not _pmo_branch_a_notifier_markdown_is_complete(inp):
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_premature_notifier_blocked",
                "msg": (
                    "【宿主拦截】本条 **未发往飞书**（避免试错过程中打扰用户）。"
                    "`markdown_content` 须 **同时包含** SKILL §1.4 三张核心区块：「📊 需求进度全览」「👥 人员任务矩阵」"
                    "「📦 版本发布需求映射」；缺一不可时禁止推送。"
                    "请根据本轮 `atom_bi_project_context` 的 Observation，从 `files[]` 或目录内 `00_SYNC_MANIFEST.json` 取得**确切文件名**"
                    "后再 `mcp:read_file` / `core:fs_read`（注意常见坑：`03_...md` **不是** `3_...md`）。"
                    "读全并重算后 **一次性**调用 `atom_lark_notifier`。"
                    "需求进度表中须写入 `vewpI8lyYw` **全部**符合条件的一级大需求（非仅四条），分页由原生表 `native_table_page_size` 承担。"
                ),
            },
            ensure_ascii=False,
        )
    if ctx.metadata.get("_pmo_bi_project_context_ok") and _pmo_notifier_falsely_claims_critical_sync_failure(
        mc, title
    ):
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_false_sync_claim_blocked",
                "msg": (
                    "【宿主拦截】本条 **未发往飞书**：本轮已成功执行 `atom_bi_project_context`，**禁止**谎称核心表「未成功同步」「同步失败」等。"
                    "若读盘报错路径不存在，请核对 manifest **带前导零** 的文件名。"
                    "在确认数据与三节表齐备前不要再次推送试错卡片。"
                ),
            },
            ensure_ascii=False,
        )
    return None


def _pmo_forbidden_lark_title(title: str) -> bool:
    """PMO 场景下禁止与自动化「冒烟测试」战报撞名的卡片标题（与 k11_lark_smoke_report 区分）。"""
    t = str(title or "").strip()
    if not t:
        return True
    if "冒烟" in t:
        return True
    low = t.lower()
    if "smoke" in low and ("test" in low or "report" in low):
        return True
    return False


def _pmo_default_lark_card_title() -> str:
    from datetime import datetime

    return f"【K11 · PMO 宏观看板】{datetime.now():%Y-%m-%d}"


def _pmo_session_lark_chat_id(ctx: PipelineContext | None = None) -> str:
    if ctx is not None:
        cid = str(ctx.metadata.get("_lark_chat_id") or "").strip()
        if cid:
            return cid
    from l3_node.channels.lark.turn_chat_context import peek_lark_chat_id_for_tools

    return peek_lark_chat_id_for_tools()


def _pmo_resolve_primary_chat_id(ctx: PipelineContext | None = None) -> str:
    from l3_node.pmo_lark_env import pmo_effective_primary_chat_id

    return pmo_effective_primary_chat_id(_pmo_session_lark_chat_id(ctx))


def _pmo_resolve_monitor_chat_id() -> str:
    from l3_node.pmo_lark_env import pmo_monitor_chat_id

    return pmo_monitor_chat_id()


def _pmo_delivery_targets_from_push_observation(observation: str) -> tuple[str, ...]:
    o = _pmo_parse_tool_observation_json(observation)
    if not o:
        return ()
    chat_ids = o.get("chat_ids")
    if isinstance(chat_ids, list):
        ids = [str(x).strip() for x in chat_ids if str(x).strip()]
        if ids:
            return tuple(ids)
    pushes = o.get("pushes")
    if not isinstance(pushes, list):
        return ()
    ids = [
        str(p.get("chat_id") or "").strip()
        for p in pushes
        if isinstance(p, dict) and str(p.get("chat_id") or "").strip()
    ]
    if not ids:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return tuple(out)


def _pmo_required_delivery_chat_ids(ctx: PipelineContext | None = None) -> tuple[str, ...]:
    if ctx is not None:
        stored = ctx.metadata.get("_pmo_delivery_required_chats")
        if isinstance(stored, list) and stored:
            return tuple(str(x).strip() for x in stored if str(x).strip())
    from l3_node.pmo_lark_env import pmo_required_delivery_chat_ids

    return pmo_required_delivery_chat_ids(_pmo_session_lark_chat_id(ctx))


def _pmo_delivery_targets_user_hint(ctx: PipelineContext | None = None) -> str:
    ids = _pmo_required_delivery_chat_ids(ctx)
    if not ids:
        return "飞书触发群（`.env` 未设 `PMO_PRIMARY_CHAT_ID` 时用当前会话 `oc_…`）"
    if len(ids) <= 1:
        return f"主群 `{ids[0]}`"
    return f"主群 `{ids[0]}`、监控群 `{ids[1]}`"


def _pmo_macro_dashboard_push_succeeded(
    observation: str,
    ctx: PipelineContext | None = None,
) -> bool:
    """解析 core:pmo_macro_dashboard_push 返回：配置的投递目标均 success。"""
    o = _pmo_parse_tool_observation_json(observation)
    if not o:
        return False
    st = str(o.get("status") or "").lower()
    if st not in ("success", "ok", "partial"):
        return False
    pushes = o.get("pushes")
    if not isinstance(pushes, list) or not pushes:
        return False
    ok_chats: set[str] = set()
    for p in pushes:
        if not isinstance(p, dict):
            continue
        if str(p.get("status") or "").lower() != "success":
            continue
        cid = str(p.get("chat_id") or "").strip()
        if cid:
            ok_chats.add(cid)
    required = set(_pmo_required_delivery_chat_ids(ctx))
    if not required:
        required = set(_pmo_delivery_targets_from_push_observation(observation))
    return bool(required) and required <= ok_chats


def _pmo_fixup_atom_lark_notifier_inp(inp: str, ctx: PipelineContext | None = None) -> str:
    """
    PMO 推送：禁止无效 webhook（含把 oc_ chat_id 填进 webhook_url）；
    缺 chat_id 时注入主群（.env 或飞书触发群）；PMO 走 IM API（app_id/secret）。
    """
    raw = (inp or "").strip()
    if not raw.startswith("{"):
        return inp
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return inp
    if not isinstance(args, dict):
        return inp
    from l3_node.channels.lark.webhook_url import (
        is_valid_lark_incoming_webhook_url,
        looks_like_lark_chat_id,
    )

    wh = str(args.get("webhook_url") or "").strip()
    cid = str(args.get("chat_id") or "").strip()
    if wh and not is_valid_lark_incoming_webhook_url(wh):
        if looks_like_lark_chat_id(wh) and not cid:
            cid = wh
        args.pop("webhook_url", None)
    elif wh:
        args["webhook_url"] = wh

    if not cid:
        cid = _pmo_resolve_primary_chat_id(ctx)
    args["chat_id"] = cid
    return json.dumps(args, ensure_ascii=False)


def _pmo_sanitize_atom_lark_notifier_inp(inp: str, ctx: PipelineContext | None = None) -> str:
    """修正 atom_lark_notifier 的 title / webhook / chat_id（PMO 双群 IM 推送）。"""
    raw = (inp or "").strip()
    if not raw.startswith("{"):
        return inp
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return inp
    if not isinstance(args, dict):
        return inp
    title = args.get("title")
    if not isinstance(title, str):
        title = ""
    if _pmo_forbidden_lark_title(title):
        args["title"] = _pmo_default_lark_card_title()
        inp = json.dumps(args, ensure_ascii=False)
    mc = str(args.get("markdown_content") or "")
    if mc:
        from l3_node.pmo_report_format import polish_pmo_war_report_markdown

        fixed = polish_pmo_war_report_markdown(mc)
        if fixed != mc:
            args["markdown_content"] = fixed
            inp = json.dumps(args, ensure_ascii=False)
    inp = _pmo_fixup_atom_lark_notifier_inp(inp, ctx)
    return inp


def _lark_notifier_observation_suggests_success(observation_full: str) -> bool:
    """atom_lark_notifier / send_lark_markdown 典型返回 {\"status\": \"success\", ...}。"""
    s = str(observation_full or "").strip()
    if not s:
        return False
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and str(obj.get("status") or "").lower() == "success":
            return True
    except json.JSONDecodeError:
        pass
    compact = s.replace(" ", "")
    return "飞书已送达" in s and '"status"' in compact and "error" not in s[:400].lower()


def _pmo_final_answer_falsely_claims_lark_sent(ans: str) -> bool:
    """Final Answer 口头声称已发飞书/群，用于拦截未调 notifier 的幻觉。"""
    s = str(ans or "").strip()
    if not s:
        return False
    if re.search(
        r"(未成功|发送失败|未能发送|无法发送|未调用|未发送|跳过.{0,6}推送|notifier.*失败|"
        r"status[\"']?\s*:\s*[\"']?error|\"status\"\s*:\s*\"error\")",
        s,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"(已成功|已经).{0,80}(飞书|发送|送达|群聊)|"
            r"通过飞书.{0,40}(发送|推送)|"
            r"推送(到|至).{0,24}(主群|监控群|飞书|群聊)|"
            r"发到(了)?指定群|"
            r"发往.{0,12}群|"
            r"卡片已发.{0,12}群|"
            r"飞书.{0,16}成功|"
            r"已发(?:往|送).{0,8}(?:群|飞书)|"
            r"战报.{0,24}(已|已经).{0,16}(推送|发送|送达)|"
            r"已通过.{0,240}(atom_lark_notifier|mcp:atom_lark_notifier)|"
            r"(atom_lark_notifier|mcp:atom_lark_notifier).{0,160}已(经)?(发送|推送|送达)|"
            r"已推送.{0,80}(飞书|主群|监控群|群聊)",
            s,
            re.I,
        )
    )


def _pmo_final_answer_looks_like_premature_branch_a_delivery(ans: str) -> bool:
    """v7 仅分析/DB 就绪：未完成双群推送却输出宏观看板式收工摘要。"""
    s = str(ans or "").strip()
    if not s or len(s) < 60:
        return False
    if _pmo_final_answer_falsely_claims_lark_sent(s):
        return True
    markers = (
        "战报主要",
        "需求进度全览",
        "人员任务矩阵",
        "版本发布需求映射",
        "宏观看板",
        "分支A",
        "分支 A",
        "分支a",
        "已成功完成PMO",
        "已完成PMO",
        "PMO-Copilot v7",
        "识别出",
        "存在过载",
    )
    hits = sum(1 for m in markers if m in s)
    return hits >= 2 or (hits >= 1 and len(s) > 180)


def _reject_pmo_branch_a_analysis_incomplete_delivery_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """
    v7 DB 仅分析：禁止在未双群 notifier 成功时以 Final Answer 输出战报摘要或声称已推送。
    """
    if not _pmo_lark_push_guard_channel_active(ctx):
        return False
    if not (_pmo_analysis_only_mode(ctx) or _pmo_db_analysis_mode(ctx)):
        return False
    if _pmo_branch_a_delivery_complete(ctx):
        return False
    if not _pmo_final_answer_looks_like_premature_branch_a_delivery(ans):
        return False
    try:
        n = int(ctx.metadata.get("_pmo_analysis_incomplete_delivery_guard_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 5:
        return False
    ctx.metadata["_pmo_analysis_incomplete_delivery_guard_count"] = n + 1
    missing = _pmo_branch_a_missing_probes(ctx)
    chats_ok = ctx.metadata.get("_pmo_notifier_chats_success") or []
    logger.warning(
        "[L3 Agent][PMO 分析交付] trace=%s via=%s 未完成双群推送却 Final Answer 收工 probes=%s chats=%s",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
        missing,
        chats_ok,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【系统校验·PMO·v7 仅分析】你尚未完成全部投递目标的 `mcp:atom_lark_notifier` 成功推送"
            f"（须送达：{_pmo_delivery_targets_user_hint(ctx)}；当前已成功群：{chats_ok or '无'}），"
            "禁止用 Final Answer 输出战报摘要或声称已推送。\n"
            f"探针/交叉分析缺口：{('、'.join(missing) if missing else '无')}。\n"
            "请继续 ReAct（勿写 Final Answer）：\n"
            "① 按 SKILL §1.2.1 七步框架补完 db_query（≤10 次）；\n"
            "② 组装 §1.4 三表 markdown_content；\n"
            f"③ 对每个投递目标各调用一次 atom_lark_notifier（{_pmo_delivery_targets_user_hint(ctx)}），各须 Observation success；\n"
            "④ 全部目标 success 后才可 ≤3 句 Final Answer 确认。"
        ),
    })
    return True


def _reject_pmo_branch_a_force_push_exit_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """
    v7 仅分析：双群推送未成功时，禁止以任何措辞（含「数据质量差」「无法分析」）直接 Final Answer 退出。
    """
    if not _pmo_lark_push_guard_channel_active(ctx):
        return False
    if not _pmo_db_analysis_mode(ctx):
        return False
    if _pmo_branch_a_delivery_complete(ctx):
        return False
    s = str(ans or "").strip()
    if len(s) < 20:
        return False
    try:
        n = int(ctx.metadata.get("_pmo_force_push_exit_guard_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 6:
        return False
    ctx.metadata["_pmo_force_push_exit_guard_count"] = n + 1
    chats_ok = ctx.metadata.get("_pmo_notifier_chats_success") or []
    qn = int(ctx.metadata.get("_pmo_db_query_count") or 0)
    missing = _pmo_branch_a_missing_probes(ctx)
    logger.warning(
        "[L3 Agent][PMO 强制推送] trace=%s via=%s 未推送却 Final Answer 退出 db_query=%s chats=%s",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
        qn,
        chats_ok,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【系统校验·PMO·v7 仅分析】无论分析结果质量如何，分支 A **必须先尝试** "
            f"`mcp:atom_lark_notifier` 推送到全部投递目标（{_pmo_delivery_targets_user_hint(ctx)}）后才能 Final Answer。\n"
            f"当前 db_query={qn}/{PMO_BRANCH_A_MIN_DB_QUERIES}；"
            f"已成功群：{chats_ok or '无（一次都未推送）'}。\n"
            f"探针缺口：{('、'.join(missing) if missing else '无')}。\n"
            "数据有缺口时须在战报中写 ⚠️ 占位行，**禁止**以「数据质量差」「无法形成洞察」为由跳过推送。\n"
            "字段名写错导致 null 时须修正 SQL，不得归因为数据源问题。\n"
            "请继续 ReAct（勿写 Final Answer）：\n"
            "① 补完缺失探针（Step3 须 json_each；Step5 须 GROUP BY；Step6a+6b 跨视图；Step7 须 COUNT 聚合）；\n"
            "② 组装 §1.4 三表 markdown_content（含 ⚠️ 占位行）；\n"
            f"③ 对每个投递目标各 atom_lark_notifier（{_pmo_delivery_targets_user_hint(ctx)}）；\n"
            "④ 全部目标 success 后才可 ≤3 句 Final Answer 确认。"
        ),
    })
    return True


def _reject_pmo_false_lark_sent_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """
    PMO 场景：禁止未调用 atom_lark_notifier 成功却在 Final Answer 中声称已推送飞书。
    返回 True 表示已注入 user 纠偏消息，外层须 continue。
    """
    if not _pmo_lark_push_guard_channel_active(ctx):
        return False
    if _pmo_db_analysis_mode(ctx):
        if _pmo_branch_a_delivery_complete(ctx):
            return False
    elif ctx.metadata.get("_pmo_atom_lark_notify_ok"):
        return False
    if not _pmo_final_answer_falsely_claims_lark_sent(ans):
        return False
    try:
        n = int(ctx.metadata.get("_pmo_false_lark_sent_guard_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 4:
        logger.warning(
            "[L3 Agent][PMO 飞书校验] trace=%s via=%s 纠偏已达 %s 次，停止拦截",
            str(ctx.metadata.get("_react_step_trace") or ""),
            via,
            n,
        )
        return False
    ctx.metadata["_pmo_false_lark_sent_guard_count"] = n + 1
    logger.warning(
        "[L3 Agent][PMO 飞书校验] trace=%s via=%s Final Answer 声称已发飞书但未记录 notifier 成功，已注入纠偏",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【系统校验·PMO】你的 Final Answer 声称已通过飞书/群发报送，但本轮 **尚未**出现 "
            "`mcp:atom_lark_notifier` 的成功 Observation（应含 `\"status\": \"success\"` 或「飞书已送达」）。\n"
            "**禁止**在未调用该工具的情况下声称已推送。\n"
            "请立即输出 ReAct（勿写 Final Answer）：\n"
            "Thought: …\n"
            "Action: mcp:atom_lark_notifier\n"
            "Action Input: JSON，须含 `markdown_content`（§1.4 战报全文）、`title`、`chat_id`；**禁止** `webhook_url`。\n"
            f"**SKILL §1.3 投递目标**：{', '.join(_pmo_required_delivery_chat_ids(ctx)) or _pmo_delivery_targets_user_hint(ctx)}；"
            "须对每个目标各调用一次 notifier（IM API），或一次 `core:pmo_macro_dashboard_push` 且 Observation 显示全部 success。\n"
            "（v7 仅分析：全部目标 success 后才可 Final Answer 确认送达。）\n"
            "若尚未拉表，可先 `mcp:atom_bi_project_context` 再发 notifier；若任一推送失败须在 Final Answer **如实**写明 error，不得写全部已成功。"
        ),
    })
    return True


def _bi_project_context_observation_suggests_success(observation_full: str) -> bool:
    """atom_bi_project_context 典型返回 {\"status\": \"success\", \"files\": [...]}。"""
    s = str(observation_full or "").strip()
    if not s:
        return False
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and str(obj.get("status") or "").lower() == "success":
            return True
    except json.JSONDecodeError:
        pass
    compact = s.replace(" ", "").lower()
    return '"status":"success"' in compact


def _pmo_user_message_suggests_branch_b(user_text: str) -> bool:
    t = (user_text or "").strip().lower()
    t2 = re.sub(r"\s+", "", t)
    if "分支b" in t2 or "分支 b" in t:
        return True
    if "webhook_table_change" in t:
        return True
    if any(x in t for x in ("表格变更", "变更预警", "熔断预警", "webhook_table")):
        return True
    return False


def _pmo_user_intent_suggests_branch_a_macro(user_text: str) -> bool:
    s = (user_text or "").strip()
    if not s:
        return False
    sl = s.lower()
    markers = (
        "分支 a",
        "分支a",
        "宏观看板",
        "定时宏观看板",
        "cron_daily_report",
        "§1.1",
        "atom_bi_project_context",
        "全部种子",
        "种子链接",
        "tblfk9",
        "pmo-copilot",
    )
    if any(m in sl for m in markers):
        return True
    if "拉取" in s and ("§1.1" in s or "wiki" in sl or "飞书" in s or "lark" in sl):
        return True
    return False


def _pmo_branch_a_requires_bi_pull(ctx: PipelineContext) -> bool:
    """分支 A（宏观看板）链路：须先调用 atom_bi_project_context，禁止只用 Final Answer 交代「下一步」。"""
    if not _pmo_lark_push_guard_channel_active(ctx):
        return False
    if ctx.metadata.get("pmo_db_ready"):
        return False
    ut = str(ctx.intent or "").strip()
    if _pmo_user_message_suggests_branch_b(ut):
        return False
    ch = str(ctx.metadata.get("_implicit_channel") or "").strip()
    if ch == "pmo_copilot_cli":
        return True
    return _pmo_user_intent_suggests_branch_a_macro(ut)


MAX_PMO_REACT_ITERATIONS = 32
PMO_BRANCH_A_MIN_DB_QUERIES = 10
PMO_MARKDOWN_FIX_SUPPLEMENTAL_MAX = 3
PMO_BRANCH_A_PERSONNEL_SSOT_VIEW = "vewCz1FFJi"
PMO_BRANCH_A_CROSS_MIN_VIEW_COUNT = 3
PMO_BRANCH_A_REQUIRED_CROSS_VIEWS = frozenset({
    "vewpI8lyYw",
    "vewCz1FFJi",
})
PMO_BRANCH_A_PRODUCT_VIEW_ALTS = frozenset({"vew8TxMcSh", "vewL9Mofgd"})

_PMO_BRANCH_A_INIT_TOOL_CANON = frozenset({
    "atom_bi_project_context",
    "core:fs_read",
    "core:db_write",
    "core:pmo_import_json",
    "core:pmo_mirror_import",
    "atom_web_scraper",
    "read_file",
})


def _pmo_db_analysis_mode(ctx: PipelineContext) -> bool:
    return bool(ctx.metadata.get("pmo_db_ready"))


def _pmo_analysis_only_mode(ctx: PipelineContext) -> bool:
    return _pmo_db_analysis_mode(ctx) and bool(ctx.metadata.get("pmo_analysis_only"))


def _pmo_init_mode(ctx: PipelineContext) -> bool:
    return bool(ctx.metadata.get("pmo_init"))


def _pmo_blocked_analysis_tools_during_init(tool: str, ctx: PipelineContext) -> str | None:
    """INIT 模式：仅允许拉表 + mirror_import；禁止 db_query 与分支 A 分析工具。"""
    if not _pmo_init_mode(ctx):
        return None
    canon = _pmo_canonical_tool_id(tool)
    if canon in ("atom_bi_project_context", "pmo_mirror_import"):
        return None
    if canon == "db_query":
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_init_analysis_blocked",
                "msg": (
                    "【宿主拦截 · INIT 模式】当前为 **镜像入库**，禁止 core:db_query 交叉分析。"
                    "请仅调用 core:pmo_mirror_import 完成入库；"
                    "若 mirror_import 超时，请重试 mirror_import 或使用 "
                    "`python scripts/run_pmo_copilot_skill.py --init`（确定性零 ReAct 路径）。"
                ),
            },
            ensure_ascii=False,
        )
    blocked = (
        "atom_lark_notifier",
        "fs_read",
        "read_file",
        "db_write",
        "pmo_import_json",
        "atom_web_scraper",
        "web_scraper",
    )
    if canon in blocked:
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_init_tool_blocked",
                "msg": (
                    f"【宿主拦截 · INIT 模式】禁止在入库阶段调用 {tool}。"
                    "INIT 仅允许 atom_bi_project_context + core:pmo_mirror_import。"
                ),
            },
            ensure_ascii=False,
        )
    return None


def _pmo_notifier_chat_id_from_inp(inp: str) -> str:
    try:
        args = json.loads(inp) if (inp or "").strip().startswith("{") else {}
        if isinstance(args, dict):
            return str(args.get("chat_id") or "").strip()
    except Exception:
        pass
    return ""


def _pmo_blocked_invalid_war_report_chat_observation(
    tool: str,
    inp: str,
    ctx: PipelineContext | None,
) -> str | None:
    """战报推送守卫：拦截 dev 遗留群与非白名单 chat_id。"""
    if ctx is None or not _pmo_lark_push_guard_channel_active(ctx):
        return None
    canon = _pmo_canonical_tool_id(tool)
    if canon not in ("atom_lark_notifier", "pmo_macro_dashboard_push"):
        return None
    from l3_node.pmo_lark_push_guard import (
        pmo_guard_blocked_push_chat_payload,
        pmo_guard_observation_json,
    )

    session = _pmo_session_lark_chat_id(ctx)
    primary = _pmo_resolve_primary_chat_id(ctx)
    tool_label = str(tool or "").strip()

    if canon == "pmo_macro_dashboard_push":
        try:
            args = json.loads(inp) if (inp or "").strip().startswith("{") else {}
        except json.JSONDecodeError:
            args = {}
        if isinstance(args, dict):
            for key in ("chat_id", "monitor_chat_id"):
                cid = str(args.get(key) or "").strip()
                if not cid:
                    continue
                payload = pmo_guard_blocked_push_chat_payload(
                    cid,
                    session_chat_id=session,
                    tool=tool_label,
                    configured_primary=primary,
                )
                if payload:
                    return pmo_guard_observation_json(payload)
        return None

    cid = _pmo_notifier_chat_id_from_inp(inp)
    if not cid:
        return None
    payload = pmo_guard_blocked_push_chat_payload(
        cid,
        session_chat_id=session,
        tool=tool_label,
        configured_primary=primary,
    )
    if payload:
        return pmo_guard_observation_json(payload)
    return None


def _pmo_canonical_tool_id(tool: str) -> str:
    t = (tool or "").replace("mcp:", "").strip().lower()
    if t.startswith("core:"):
        return t[5:]
    return t


def _pmo_ensure_analysis_probes(ctx: PipelineContext) -> dict[str, bool]:
    probes = ctx.metadata.get("_pmo_analysis_probes")
    if not isinstance(probes, dict):
        probes = {}
        ctx.metadata["_pmo_analysis_probes"] = probes
    return probes


def _pmo_parse_tool_observation_json(observation: str) -> dict[str, Any] | None:
    """
    从 Observation 解析工具 JSON；忽略 context_prefetch 等后缀 Markdown。
    build_prefetch_attachment 会在工具 JSON 后追加「【relevant_context_prefetch】…」，
    若对整段 json.loads 会失败，导致 macro_dashboard_push 双群 success 无法被宿主识别。
    """
    raw = str(observation or "").strip()
    if not raw:
        return None
    marker = "【relevant_context_prefetch】"
    if marker in raw:
        raw = raw.split(marker, 1)[0].strip()
    try:
        o = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return o if isinstance(o, dict) else None


def _pmo_observation_is_foreground_tool_timeout(observation: str) -> bool:
    """前台同步预算超时 JSON（工具可能仍在线程中继续执行）。"""
    o = _pmo_parse_tool_observation_json(observation)
    if not o:
        return False
    return str(o.get("reason") or "") == "foreground_sync_budget_exceeded"


def _pmo_track_macro_dashboard_push_observation(ctx: PipelineContext, observation: str) -> None:
    """记录 macro_dashboard_push 结果：成功 / 超时 / 永久失败。"""
    obs = str(observation or "")
    if _pmo_observation_is_foreground_tool_timeout(obs):
        ctx.metadata["_pmo_macro_dashboard_push_timeout"] = True
        ctx.metadata.pop("_pmo_macro_dashboard_push_failed", None)
        return
    ctx.metadata.pop("_pmo_macro_dashboard_push_timeout", None)
    o = _pmo_parse_tool_observation_json(obs)
    if not o:
        return
    st = str(o.get("status") or "").lower()
    if st in ("success", "ok", "partial"):
        ctx.metadata.pop("_pmo_macro_dashboard_push_failed", None)
        return
    if st in ("failed", "error"):
        ctx.metadata["_pmo_macro_dashboard_push_failed"] = True


def _pmo_macro_dashboard_push_timeout_blocks_notifier(ctx: PipelineContext) -> str | None:
    """
    macro_dashboard_push 前台超时后，后台线程可能仍在推送。
    多 Agent 阶段三禁止 atom_lark_notifier 兜底，避免双份不同版战报。
    """
    if not ctx.metadata.get("pmo_multi_agent_complete"):
        return None
    if ctx.metadata.get("_pmo_macro_dashboard_push_ok"):
        return None
    if not ctx.metadata.get("_pmo_macro_dashboard_push_timeout"):
        return None
    if ctx.metadata.get("_pmo_macro_dashboard_push_failed"):
        return None
    return json.dumps(
        {
            "status": "error",
            "error": "pmo_macro_dashboard_push_timeout_no_notifier",
            "msg": (
                "【宿主拦截】`core:pmo_macro_dashboard_push` 曾触发前台超时，"
                "但工具可能仍在后台组装并推送完整 native_table 战报。"
                "**禁止** 使用 atom_lark_notifier 发送精简兜底版（会导致群内出现两份不同内容）。"
                "请 **仅** 再次调用 `core:pmo_macro_dashboard_push` + `{}` 等待 success；"
                "或输出 ≤3 句说明「推送可能已在途中，请稍后在群内核对 message_id」。"
            ),
        },
        ensure_ascii=False,
    )


def _pmo_track_macro_dashboard_push_success(ctx: PipelineContext, observation: str) -> None:
    """macro_dashboard_push 双群成功 → 等同 notifier 双群送达，允许 Final Answer 收工。"""
    _pmo_track_macro_dashboard_push_observation(ctx, observation)
    if not _pmo_macro_dashboard_push_succeeded(observation, ctx):
        return
    targets = _pmo_delivery_targets_from_push_observation(observation) or _pmo_required_delivery_chat_ids(
        ctx
    )
    if targets:
        ctx.metadata["_pmo_delivery_required_chats"] = list(targets)
    ctx.metadata["_pmo_macro_dashboard_push_ok"] = True
    ctx.metadata.pop("_pmo_macro_dashboard_push_timeout", None)
    ctx.metadata["_pmo_atom_lark_notify_ok"] = True
    chats = [
        str(x).strip()
        for x in (ctx.metadata.get("_pmo_notifier_chats_success") or [])
        if str(x).strip()
    ]
    for cid in targets:
        if cid not in chats:
            chats.append(cid)
    ctx.metadata["_pmo_notifier_chats_success"] = chats
    ctx.metadata.pop("_pmo_markdown_fix_phase", None)
    ctx.metadata.pop("_pmo_markdown_fix_only", None)


def _pmo_append_macro_dashboard_delivery_hint(observation: str) -> str:
    if "宏观看板已双群推送成功" in str(observation or ""):
        return observation
    return (
        f"{observation.rstrip()}\n\n"
        "【宿主·PMO】`core:pmo_macro_dashboard_push` 已完成内部双收件送达。"
        "请 **立即** 输出 ≤3 句 Final Answer（可引用 message_id）；"
        "**禁止**向用户提及「监控群」或任何 `oc_` chat_id，仅写「战报已推送至飞书/请在本群查看卡片」。"
        "**禁止**再调用任何工具。"
    )


def _pmo_branch_a_delivery_complete(ctx: PipelineContext) -> bool:
    if ctx.metadata.get("_pmo_macro_dashboard_push_ok"):
        return True
    chats = {str(x).strip() for x in (ctx.metadata.get("_pmo_notifier_chats_success") or []) if str(x).strip()}
    required = set(_pmo_required_delivery_chat_ids(ctx))
    return bool(required) and required <= chats


def _pmo_branch_a_push_prerequisites_met(ctx: PipelineContext) -> bool:
    if ctx.metadata.get("pmo_multi_agent_complete"):
        return True
    if int(ctx.metadata.get("_pmo_db_query_count") or 0) < PMO_BRANCH_A_MIN_DB_QUERIES:
        return False
    probes = _pmo_ensure_analysis_probes(ctx)
    for key in ("sprint", "status", "personnel", "version", "epic"):
        if not probes.get(key):
            return False
    if not probes.get("personnel_kanban"):
        return False
    if not (probes.get("cross_view_6a") and probes.get("cross_view_6b")):
        return False
    if _pmo_branch_a_missing_cross_analysis(ctx):
        return False
    return True


def _pmo_track_db_query_sql(ctx: PipelineContext, tool_id: str, inp: str) -> None:
    if _pmo_canonical_tool_id(tool_id) != "db_query":
        return
    try:
        ctx.metadata["_pmo_db_query_count"] = int(ctx.metadata.get("_pmo_db_query_count") or 0) + 1
    except (TypeError, ValueError):
        ctx.metadata["_pmo_db_query_count"] = 1
    sql = _pmo_extract_sql_from_tool_inp(inp)
    sl = sql.lower()
    if _pmo_sql_is_step1_map(sql):
        ctx.metadata["_pmo_step1_map_done"] = True
    view_ids = _pmo_extract_view_ids_from_sql(sql)
    if view_ids:
        _pmo_ensure_views_queried(ctx).update(view_ids)
    probes = _pmo_ensure_analysis_probes(ctx)
    if (
        "pmo_raw_records" in sl
        or "vewpi8lyyw" in sl
        or "pmo_dev_requirements" in sl
        or "pmo_product_requirements" in sl
    ):
        has_requirement = re.search(r"\brequirement\b", sl) or "epic" in sl
        if has_requirement and not _pmo_sql_uses_invalid_parent_null_epic_filter(sql):
            if _pmo_sql_excludes_dept_epic_placeholders(sql):
                probes["epic"] = True
            elif "vewpi8lyyw" in sl and "父记录" in sql and "[0].text" in sql:
                probes["epic"] = True
    if "work_cycle" in sl or "sprint" in sl:
        if "count(" in sl or "group by" in sl:
            probes["sprint"] = True
        elif "sprint" in sl and "json_extract" in sl:
            probes["sprint_detail_only"] = True
    if (
        "execution_stage" in sl
        or "current_status" in sl
        or re.search(r"\bstatus\b", sl)
        or "状态" in sql
    ):
        if "count(" in sl and "group by" in sl:
            probes["status"] = True
        elif re.search(r"\bstatus\b", sl) or "状态" in sql:
            probes["status_detail_only"] = True
    if "vewcz1ffji" in sl and "json_each" in sl:
        probes["personnel_kanban"] = True
        probes["personnel"] = True
    elif "vewcz1ffji" in sl and (
        "person in charge" in sl or "participant" in sl or "en_name" in sl
    ):
        probes["personnel_kanban_partial"] = True
    elif "vewl9mofgd" in sl and (
        "person" in sl or "负责人" in sql or "json_each" in sl or "participant" in sl
    ):
        if "json_each" in sl:
            probes["personnel_kanban"] = True
            probes["personnel"] = True
    elif (
        "person in charge" in sl or "person_name" in sl or "personnel_task_progress" in sl
    ) and "vewpi8lyyw" in sl and "vewcz1ffji" not in sl:
        probes["personnel_vewp_only"] = True
    elif "json_each" in sl and re.search(r"person|participant|负责人", sl):
        probes["personnel"] = True
    if "vewpi8lyyw" in sl and (
        "延期" in sql or "🔴" in sql or re.search(r"status.*延期|延期.*status", sl)
    ):
        probes["cross_view_6a"] = True
    if "vewcz1ffji" in sl and "fields like" in sl:
        probes["cross_view_6b"] = True
    if "count(" in sl and (
        "version goal" in sl
        or "vew8txmcsh" in sl
        or "vewl9mofgd" in sl
    ):
        probes["version"] = True
    elif "version goal" in sl and "limit 1" in sl:
        probes["version_sample_only"] = True


def _pmo_append_draft_gfm_hint_after_db_query(
    ctx: PipelineContext, assistant_response: str, observation: str
) -> str:
    """db_query 后检查 Thought 是否含 GFM 草稿行，缺失或重复则注入纠正提示。"""
    if not _pmo_db_analysis_mode(ctx) or _pmo_branch_a_delivery_complete(ctx):
        return observation
    _pmo_sync_assembly_phase_from_thought(ctx, assistant_response)
    if _pmo_markdown_fix_phase(ctx) == "final":
        return observation
    hints: list[str] = []
    thought = _pmo_extract_thought_from_assistant(assistant_response) or str(assistant_response or "")
    if not _pmo_assistant_has_gfm_draft(assistant_response):
        hints.append(
            "⚠️ **草稿提醒**：上一步 Thought 未包含 GFM 表格行（须含 `|` 分隔符，禁止写「待填充」）。"
            "请在本步 Thought **开头**补写上一步对应表的至少 1 行 GFM 草稿"
            "（📊 Step4/5 · 👥 Step3 · 📦 Step7），再继续下一步。"
        )
    else:
        fp = _pmo_extract_gfm_draft_fingerprint(thought)
        if fp:
            last_fp = str(ctx.metadata.get("_pmo_last_gfm_draft_fingerprint") or "")
            if last_fp and fp == last_fp:
                hints.append(
                    "⚠️ **草稿重复**：本轮 Thought 的 GFM 草稿内容与上轮完全相同。"
                    "请基于本步 Observation **追加至少 1 行新数据**（禁止复制旧行）；"
                    "Step5 应写状态汇总行（如 `| 🔴 延期 | N 条 |`），Step4 写 Epic 行，Step7 写 Version Goal 行。"
                )
            ctx.metadata["_pmo_last_gfm_draft_fingerprint"] = fp
    if not hints:
        return observation
    return f"{observation}\n\n" + "\n".join(hints)


def _pmo_branch_a_blocked_invalid_field_sql(
    tool: str, inp: str, ctx: PipelineContext
) -> str | None:
    """开发主表使用错误中文字段名时提前拦截，避免误判为数据质量问题。"""
    if not _pmo_db_analysis_mode(ctx):
        return None
    if _pmo_canonical_tool_id(tool) != "db_query":
        return None
    sql = _pmo_extract_sql_from_tool_inp(inp)
    sl = sql.lower()
    bad_fields: list[str] = []
    if "vewpi8lyyw" in sl:
        if "负责人" in sql:
            bad_fields.append("$.负责人")
        if "需求名称" in sql:
            bad_fields.append("$.需求名称")
    if "vewcz1ffji" in sl and "责任人" in sql and "person in charge" not in sl:
        bad_fields.append("责任人")
    if not bad_fields:
        return None
    return json.dumps(
        {
            "status": "error",
            "error": "pmo_invalid_field_name_blocked",
            "msg": (
                "【宿主拦截】SQL 使用了不存在的字段名："
                f"{('、'.join(bad_fields))}。"
                "vewpI8lyYw / vewCz1FFJi 开发表字段为 "
                "Person in charge/Participant 和 Requirement（非「负责人」「需求名称」「责任人」）。"
                "请先 SELECT columns_json FROM pmo_views_meta 核对字段名后重写 SQL。"
            ),
            "hints": [
                "请核对 Step1 的 columns_json；"
                "Person 用 json_each(json_extract(fields, '$.$\"Person in charge/Participant\"'))；"
                "Requirement 用 json_extract(fields, '$.Requirement')。",
            ],
        },
        ensure_ascii=False,
    )


def _pmo_branch_a_blocked_force_assembly_round(
    tool: str, ctx: PipelineContext, assistant_response: str = ""
) -> str | None:
    """七步探针完成后强制组装轮：writing 阶段禁止查库/推送；ready 阶段禁止查库、允许推送。"""
    if not _pmo_db_analysis_mode(ctx):
        return None
    if _pmo_branch_a_delivery_complete(ctx):
        return None
    if _pmo_markdown_fix_phase(ctx):
        return None
    if not _pmo_branch_a_push_prerequisites_met(ctx):
        return None
    _pmo_ensure_assembly_phase(ctx)
    phase = _pmo_assembly_phase(ctx)
    if not phase:
        return None
    canon = _pmo_canonical_tool_id(tool)
    if phase == "writing" and canon == "atom_lark_notifier":
        if _pmo_assistant_thought_has_all_three_tables(assistant_response):
            ctx.metadata["_pmo_assembly_phase"] = "ready"
            return json.dumps(
                {
                    "status": "error",
                    "error": "pmo_assembly_round_notifier_blocked",
                    "msg": (
                        "【宿主拦截 · 组装轮】Thought 三表 GFM 预览 **已就绪**。"
                        "本轮禁止推送；下一轮请将 Thought 中的完整 GFM 三表"
                        " **全文复制** 到 atom_lark_notifier.markdown_content 后再调用推送。"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_assembly_round_notifier_blocked",
                "msg": (
                    "【宿主拦截 · 组装轮】禁止推送：Thought 中尚未含完整三张 GFM 表。"
                    "请翻阅前 10 轮 Thought/Observation，在本轮 Thought 写出"
                    "📊/👥/📦 三表完整预览（各含 | 表头与数据行），本轮不要调用 notifier。"
                ),
            },
            ensure_ascii=False,
        )
    if phase == "writing" and canon == "db_query":
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_force_assembly_round_blocked",
                "msg": (
                    "【宿主拦截 · 组装轮】§1.2.1 七步探针 **已完成**，禁止 core:db_query。"
                    "请在本轮 **仅** 于 Thought 中写出 §1.4 完整三表 GFM 预览"
                    "（📊 需求进度全览 + 👥 人员任务矩阵 + 📦 版本发布需求映射，各含 | 表格）。"
                    "❌ 本轮禁止 atom_lark_notifier；Thought 写完后下一轮再推送。"
                ),
            },
            ensure_ascii=False,
        )
    if phase == "writing":
        _pmo_sync_assembly_phase_from_thought(ctx, assistant_response)
    if _pmo_assembly_phase(ctx) == "ready" and canon == "db_query":
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_force_assembly_round_blocked",
                "msg": (
                    "【宿主拦截 · 组装轮】Thought 三表预览已完成，禁止继续 core:db_query。"
                    "请将 Thought 中的 GFM 三表全文写入 markdown_content 后调用 atom_lark_notifier。"
                ),
            },
            ensure_ascii=False,
        )
    return None


def _pmo_branch_a_blocked_duplicate_step1_map(
    tool: str, inp: str, ctx: PipelineContext
) -> str | None:
    """分析中途重复 Step1 地图查询时阻止盲目重跑七步。"""
    if not _pmo_db_analysis_mode(ctx):
        return None
    if _pmo_canonical_tool_id(tool) != "db_query":
        return None
    sql = _pmo_extract_sql_from_tool_inp(inp)
    if not _pmo_sql_is_step1_map(sql):
        return None
    if not ctx.metadata.get("_pmo_step1_map_done"):
        return None
    qn = int(ctx.metadata.get("_pmo_db_query_count") or 0)
    if qn < 5:
        return None
    try:
        restart = int(ctx.metadata.get("_pmo_restart_count") or 0) + 1
    except (TypeError, ValueError):
        restart = 1
    ctx.metadata["_pmo_restart_count"] = restart
    missing = _pmo_branch_a_missing_probes(ctx)
    missing_txt = "、".join(missing) if missing else "无"
    if restart >= 2:
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_step1_rerun_blocked",
                "msg": (
                    "【宿主拦截 · ExecutionBrief】你已在本轮重复执行 Step1 数据地图 ≥2 次。"
                    f"当前 db_query={qn}/{PMO_BRANCH_A_MIN_DB_QUERIES}；仍缺：{missing_txt}。"
                    "❌ 禁止从 Step1 重跑七步。"
                    "请核对上下文中已有 Observation，仅补跑缺失探针对应步骤，"
                    "或直接组装 §1.4 三表 markdown_content 再推送。"
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "error",
            "error": "pmo_step1_rerun_warn_blocked",
            "msg": (
                "【宿主拦截】Step1 数据地图在本轮已执行过。"
                f"当前 db_query={qn}/{PMO_BRANCH_A_MIN_DB_QUERIES}；仍缺：{missing_txt}。"
                "❌ 禁止盲目重跑七步；请对照上下文已有数据，仅补跑缺失项。"
            ),
        },
        ensure_ascii=False,
    )


def _pmo_extract_lark_message_id(observation: str) -> str:
    o = _pmo_parse_tool_observation_json(observation)
    if not o:
        return ""
    mid = str(o.get("message_id") or "").strip()
    if mid:
        return mid
    data = o.get("data")
    if isinstance(data, dict):
        mid = str(data.get("message_id") or "").strip()
        if mid:
            return mid
    return ""


def _pmo_track_notifier_chat_success(ctx: PipelineContext, inp: str, observation: str) -> None:
    if not _lark_notifier_observation_suggests_success(observation):
        return
    cid = _pmo_notifier_chat_id_from_inp(inp)
    if not cid:
        return
    from l3_node.pmo_push_audit_log import log_pmo_lark_push

    log_pmo_lark_push(
        tool="mcp:atom_lark_notifier",
        chat_id=cid,
        status="success",
        message_id=_pmo_extract_lark_message_id(observation),
    )
    chats = [str(x).strip() for x in (ctx.metadata.get("_pmo_notifier_chats_success") or []) if str(x).strip()]
    if cid not in chats:
        chats.append(cid)
    ctx.metadata["_pmo_notifier_chats_success"] = chats
    if _pmo_branch_a_delivery_complete(ctx):
        ctx.metadata.pop("_pmo_markdown_fix_phase", None)
        ctx.metadata.pop("_pmo_markdown_fix_only", None)


def _pmo_branch_a_blocked_rerun_db_after_markdown_block(
    tool: str, ctx: PipelineContext, inp: str = ""
) -> str | None:
    """markdown 修复阶段：final 禁止查库；supplemental 允许有限补缺 SQL。"""
    if not _pmo_db_analysis_mode(ctx):
        return None
    if _pmo_canonical_tool_id(tool) != "db_query":
        return None
    phase = _pmo_markdown_fix_phase(ctx)
    if not phase:
        return None
    if _pmo_branch_a_delivery_complete(ctx):
        ctx.metadata.pop("_pmo_markdown_fix_phase", None)
        ctx.metadata.pop("_pmo_markdown_fix_only", None)
        return None
    if phase == "supplemental":
        sql = _pmo_extract_sql_from_tool_inp(inp)
        if _pmo_sql_is_supplemental_allowed(sql):
            used = int(ctx.metadata.get("_pmo_markdown_fix_supplemental_count") or 0) + 1
            ctx.metadata["_pmo_markdown_fix_supplemental_count"] = used
            if used >= PMO_MARKDOWN_FIX_SUPPLEMENTAL_MAX:
                ctx.metadata["_pmo_markdown_fix_phase"] = "final"
                ctx.metadata["_pmo_markdown_fix_only"] = True
            return None
        remaining = _pmo_markdown_fix_supplemental_remaining(ctx)
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_markdown_fix_supplemental_db_blocked",
                "msg": (
                    "【宿主拦截】markdown 补缺阶段：此类 SQL 视为重跑七步，禁止执行。"
                    f"剩余补缺额度 {remaining}/{PMO_MARKDOWN_FIX_SUPPLEMENTAL_MAX}。"
                    "允许：COUNT+GROUP BY、json_each 人员、Step6b LIKE、Epic 正确写法、LIMIT≤20。"
                    "禁止：Step1 地图、Step2 LIMIT 1 样本。"
                    "若数据已够，请补写 markdown_content 再推送。"
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "status": "error",
            "error": "pmo_markdown_fix_only_db_blocked",
            "msg": (
                "【宿主拦截】markdown 最终整合阶段，且 §1.2.1 七步分析探针 **已完成**。"
                "❌ **禁止** 任何 core:db_query。"
                "请直接基于已有 Observation 将完整 GFM 三表写入 "
                "`atom_lark_notifier.markdown_content` 后再推送。"
            ),
        },
        ensure_ascii=False,
    )


def _pmo_branch_a_blocked_init_tools_during_analysis(tool: str, ctx: PipelineContext) -> str | None:
    """DB 就绪 · 仅分析模式：禁止 INIT/拉表/读盘；推送完成后禁止继续查库。"""
    if not _pmo_analysis_only_mode(ctx):
        return None
    canon = _pmo_canonical_tool_id(tool)
    _pmo_phase3_publish_allowed = frozenset({
        "pmo_macro_dashboard_push",
        "pmo_macro_dashboard_preview",
        "atom_lark_notifier",
    })
    _pmo_sqlite_mcp_canon = frozenset({
        "read_query",
        "list_tables",
        "get_table_schema",
        "create_table",
        "write_query",
        "query",
        "db_info",
        "read_records",
    })
    if ctx.metadata.get("pmo_multi_agent_complete") and canon == "pmo_macro_dashboard_push":
        ctx.metadata["_pmo_macro_dashboard_push_attempted"] = True
        if ctx.metadata.get("_pmo_macro_dashboard_push_ok"):
            return json.dumps(
                {
                    "status": "error",
                    "error": "pmo_macro_dashboard_duplicate_blocked",
                    "msg": (
                        "【宿主拦截】本轮已通过 core:pmo_macro_dashboard_push 完成双群推送，"
                        "禁止重复调用。请输出 ≤3 句 Final Answer 确认 message_id 即可。"
                    ),
                },
                ensure_ascii=False,
            )
    if ctx.metadata.get("pmo_multi_agent_complete") and canon == "atom_lark_notifier":
        timeout_blk = _pmo_macro_dashboard_push_timeout_blocks_notifier(ctx)
        if timeout_blk:
            return timeout_blk
        if (
            ctx.metadata.get("_pmo_macro_dashboard_push_attempted")
            and not ctx.metadata.get("_pmo_macro_dashboard_push_failed")
            and not ctx.metadata.get("_pmo_macro_dashboard_push_ok")
        ):
            return json.dumps(
                {
                    "status": "error",
                    "error": "pmo_macro_dashboard_push_pending_no_notifier",
                    "msg": (
                        "【宿主拦截 · 多 Agent 阶段三】已调用过 core:pmo_macro_dashboard_push 但未确认双群 success。"
                        "禁止 atom_lark_notifier 兜底（易与工具内推送重复且版式不一致）。"
                        "请重试 macro_dashboard_push；仅当 Observation 明确 status=failed 且无 timeout 时才允许 notifier。"
                    ),
                },
                ensure_ascii=False,
            )
    if ctx.metadata.get("pmo_multi_agent_complete") and (
        canon == "db_query" or canon in _pmo_sqlite_mcp_canon
    ):
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_multi_agent_publish_db_blocked",
                "msg": (
                    "【宿主拦截 · 多 Agent 阶段三】前序 FanOut/Pipeline 已完成查库与审计。"
                    "⛔ 禁止 db_query / MCP SQLite（read_query、list_tables 等）。"
                    "请优先 `core:pmo_macro_dashboard_push` + `{}`；仅 push 失败时回退 atom_lark_notifier。"
                ),
            },
            ensure_ascii=False,
        )
    if ctx.metadata.get("pmo_multi_agent_complete") and canon not in _pmo_phase3_publish_allowed:
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_multi_agent_publish_tool_blocked",
                "msg": (
                    "【宿主拦截 · 多 Agent 阶段三】禁止 INIT/读盘/无关 MCP。"
                    "仅允许 core:pmo_macro_dashboard_push、preview 或 mcp:atom_lark_notifier（兜底）。"
                ),
            },
            ensure_ascii=False,
        )
    if _pmo_branch_a_delivery_complete(ctx):
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_post_delivery_tool_blocked",
                "msg": (
                    "【宿主拦截】内部双收件均已推送成功，本轮交付完成。"
                    "请输出 ≤3 句 Final Answer 确认（**禁止**向用户提及监控群或 oc_ chat_id），禁止再调用任何工具。"
                ),
            },
            ensure_ascii=False,
        )
    chats = [str(x).strip() for x in (ctx.metadata.get("_pmo_notifier_chats_success") or []) if str(x).strip()]
    if chats and not _pmo_branch_a_delivery_complete(ctx) and canon == "db_query":
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_post_push_analysis_blocked",
                "msg": (
                    "【宿主拦截】已向至少一个群推送卡片，请 **先完成全部投递目标推送**（"
                    f"{_pmo_delivery_targets_user_hint(ctx)}），"
                    "禁止在此阶段继续 core:db_query。"
                ),
            },
            ensure_ascii=False,
        )
    if canon in _PMO_BRANCH_A_INIT_TOOL_CANON:
        return json.dumps(
            {
                "status": "error",
                "error": "pmo_branch_a_init_switch_blocked",
                "msg": (
                    "【宿主拦截】当前为 **DB 仅分析模式**（pmo_db_ready）。"
                    "禁止 atom_bi_project_context / fs_read / db_write / web_scraper。"
                    "请仅用 core:db_query 查询 SQLite，完成大颗粒度探针与 §1.4 三表后再 notifier。"
                ),
            },
            ensure_ascii=False,
        )
    return None


def _reject_pmo_branch_a_init_completion_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """禁止在仅分析模式下用 Final Answer 冒充 INIT/入库已完成并收工。"""
    if not _pmo_lark_push_guard_channel_active(ctx):
        return False
    if _pmo_branch_a_delivery_complete(ctx):
        return False
    s = str(ans or "").strip()
    if len(s) < 12:
        return False
    markers = ("INIT 镜像", "pmo_mirror_import", "镜像入库", "INIT 完成", "init 镜像")
    if not any(m.lower() in s.lower() for m in markers):
        return False
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【宿主·PMO 仅分析模式】禁止以 Final Answer 声称 INIT/镜像入库已完成。"
            "请继续 core:db_query 大颗粒度探针（Sprint/状态/人员/版本/Epic），"
            "完成 §1.4 三表后双群 atom_lark_notifier，再简短确认。"
        ),
    })
    return True


def _pmo_final_answer_looks_like_futile_plan_only(ans: str) -> bool:
    """仅承诺「接下来/将」要做，或假「路径/文件名不明」stall，常见于未按 Observation 读盘就收尾。"""
    s = str(ans or "").strip()
    if not s or len(s) > 800:
        return False
    if re.search(r"(```|\|.{0,3}---|\| :---)", s):
        return False
    if re.search(
        r"(接下来|下一步|将要|准备).{0,40}(拉取|拉表|同步|atom_bi|notifier|推送|发飞书|发群|§1\.\d)",
        s,
    ):
        return True
    if re.search(r"尚未收到结果", s) and re.search(
        r"(目录|列表|ls|文件名|路径)", s, re.I
    ):
        return True
    if re.search(
        r"(路径错误|读不到|无法读取|找不到文件|不正确|未能成功读取|未能读取)",
        s,
    ) and re.search(r"(美术|文件名|确认|目录列表|设计专用|视图文件)", s):
        return True
    if re.search(r"(权限|访问限制)", s) and re.search(
        r"(美术|设计专用|pmo_lark_pull|\.jachin\\workspace\\pmo)", s, re.I
    ):
        return True
    if re.search(r"(获得|确认).{0,16}(正确|准确).{0,10}(文件名|路径)", s) and re.search(
        r"(我将|然后再|再).{0,24}(读取|拉取|汇总|生成)", s
    ):
        return True
    # 「先列目录 / 先确认文件名」——拉表 Observation 已含 files[]，禁止 Final Answer 空转
    if re.search(r"pmo_lark_pull", s, re.I) and re.search(
        r"(需要先|须先|让我先|先要|准备先).{0,32}(确认|列出|罗列|查看|核对|检查)",
        s,
    ):
        return True
    if re.search(r"pmo_lark_pull", s, re.I) and re.search(
        r"(列出|罗列).{0,28}(该|此|以下|其中)?.{0,12}(目录|文件夹)",
        s,
    ):
        return True
    if (
        re.search(r"我需要先", s)
        and re.search(r"(确认|列出|核对)", s)
        and re.search(r"(pmo|目录|文件名|需求池|产品任务|设计专用|workspace)", s, re.I)
    ):
        return True
    return False


def _pmo_final_answer_looks_like_lark_card_body(ans: str) -> bool:
    """判定是否把应按 SKILL §1.4 发到群里的战报写在 Final Answer 里（须走 notifier）。"""
    s = str(ans or "")
    if not s.strip():
        return False
    if any(x in s for x in ("📊", "👥", "📦", "关键 Epic", "需求进度全览", "人员任务矩阵", "版本发布需求映射", "资源任务负荷", "Executive Summary")):
        return True
    if re.search(r"\|\s* :---\s*\|", s) or re.search(r"\|\s*-{3,}\s*\|", s):
        return True
    lines = [ln.strip() for ln in s.splitlines() if ln.strip().startswith("|")]
    if len(lines) >= 4:
        return True
    if len(s) >= 1600 and ("🟢" in s or "🔴" in s or "▓" in s or "░" in s):
        return True
    return False


def _reject_pmo_branch_a_missing_bi_pull_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """
    分支 A：禁止在未执行 atom_bi_project_context 前用 Final Answer 结束（含「接下来再拉表」式废话）。
    """
    if not _pmo_branch_a_requires_bi_pull(ctx):
        return False
    if ctx.metadata.get("_pmo_bi_project_context_invoked"):
        return False
    s = str(ans or "").strip()
    if len(s) <= 280 and re.search(
        r"(缺少|无法|不能|失败|错误|未配置|密钥|permission|403|401|超时|timeout)",
        s,
        re.I,
    ):
        return False
    if not (
        _pmo_final_answer_looks_like_futile_plan_only(ans)
        or _pmo_final_answer_looks_like_lark_card_body(ans)
        or len(s) > 360
    ):
        return False
    try:
        n = int(ctx.metadata.get("_pmo_branch_a_bi_pull_guard_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 6:
        logger.warning(
            "[L3 Agent][PMO 拉表校验] trace=%s via=%s 纠偏已达 %s 次，停止拦截",
            str(ctx.metadata.get("_react_step_trace") or ""),
            via,
            n,
        )
        return False
    ctx.metadata["_pmo_branch_a_bi_pull_guard_count"] = n + 1
    logger.warning(
        "[L3 Agent][PMO 拉表校验] trace=%s via=%s 拒绝无 atom_bi_project_context 的 Final Answer，已注入纠偏",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【系统校验·PMO·分支A】当前任务要求 **先拉取 §1.1 飞书表**（`mcp:atom_bi_project_context`，"
            "`wiki_urls` 须覆盖产品 + 开发多 view + 美术等），但你尚未产生该工具的成功/失败 Observation。\n"
            "**禁止**仅用 Final Answer 写「接下来再拉表」「准备去同步」之类的话糊弄结束。\n"
            "请立即输出 ReAct（勿写 Final Answer）：\n"
            "Thought: …\n"
            "Action: mcp:atom_bi_project_context\n"
            "Action Input: JSON，至少含 `wiki_urls` 字符串数组（与 SKILL §1.1 一致），可按需含 "
            "`output_dir_relative` 等。\n"
            "拉表拿到 Observation 后，再聚合并对每个投递目标调用 `Action: mcp:atom_lark_notifier` 推送 §1.4 卡片：\n"
            f"{_pmo_delivery_targets_user_hint(ctx)}；最后才用简短 Final Answer 确认。"
        ),
    })
    return True


def _reject_pmo_branch_a_post_bi_fs_stall_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """
    拉表已成功，但 Final Answer 仍假装「路径/美术文件名不明」「目录列表未回」等 stall ——
    常见误因：§1.1 美术 = 飞书节点「设计专用」，落盘名含 `设计专用_DiSnwVB1`，非「美术.md」。
    """
    if not _pmo_branch_a_requires_bi_pull(ctx):
        return False
    if not ctx.metadata.get("_pmo_bi_project_context_ok"):
        return False
    if ctx.metadata.get("_pmo_atom_lark_notify_ok"):
        return False
    if not _pmo_final_answer_looks_like_futile_plan_only(ans):
        return False
    try:
        n = int(ctx.metadata.get("_pmo_branch_a_post_bi_stall_guard_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 5:
        return False
    ctx.metadata["_pmo_branch_a_post_bi_stall_guard_count"] = n + 1
    logger.warning(
        "[L3 Agent][PMO 读盘纠偏] trace=%s via=%s 拉表已成功但 Final Answer 仍 stall，已注入纠偏",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【系统校验·PMO·读盘】`atom_bi_project_context` 已 **success**，Observation 里 **`files[]` + `output_dir` 已是完整文件名清单**，"
            "**禁止**用 Final Answer 说「要先列出目录 / 要先确认文件名」——应直接按 `files[]` **逐字**拼绝对路径读盘。\n"
            "**常见纠错**：「产品任务需求完成度」应对 `files[]` 中含 **`产品任务需求完成度`** + **`vew8TxMcSh`** 的那一条；前缀序号 **`NN_`（如 01_/02_/…）必须与清单完全一致**，禁止把 `02_…` 臆改成 `01_…`。\n"
            "**美术** = **「设计专用」**：路径取 `files[]` 中带 **`设计专用`**、**`DiSnwVB1`**、**`vew5taB9H1`** 的条目。**不是**整张大开发计划表（`tblfK9`）；开发计划里 **`vewpI8lyYw`/`vewswB05Wi` 落盘 md** 仅作补充。\n"
            "若 **`mcp:read_file`** 报错不在允许目录，改用 **`core:fs_read`** 传**同一路径**；读完再 **`mcp:atom_lark_notifier`**，勿再在 Final Answer 里承诺「下一步再 ls」。"
        ),
    })
    return True


def _reject_pmo_branch_a_board_without_notifier_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """
    已拉表但未发 notifier：禁止把完整战报写在 Final Answer（应写入 notifier 的 markdown_content）。
    v7 DB 仅分析模式同样适用（不要求 atom_bi_project_context）。
    """
    if not _pmo_lark_push_guard_channel_active(ctx):
        return False
    if _pmo_db_analysis_mode(ctx):
        if not _pmo_analysis_only_mode(ctx) and not _pmo_user_intent_suggests_branch_a_macro(
            str(ctx.intent or "")
        ):
            return False
    elif not _pmo_branch_a_requires_bi_pull(ctx):
        return False
    if not _pmo_db_analysis_mode(ctx) and not ctx.metadata.get("_pmo_bi_project_context_invoked"):
        return False
    if ctx.metadata.get("_pmo_atom_lark_notify_ok") or _pmo_branch_a_delivery_complete(ctx):
        return False
    if not _pmo_final_answer_looks_like_lark_card_body(ans):
        return False
    try:
        n = int(ctx.metadata.get("_pmo_branch_a_notifier_dump_guard_count") or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 5:
        return False
    ctx.metadata["_pmo_branch_a_notifier_dump_guard_count"] = n + 1
    logger.warning(
        "[L3 Agent][PMO 推送校验] trace=%s via=%s 战报式 Final Answer 但未 notifier 成功，已注入纠偏",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({
        "role": "user",
        "content": (
            "【系统校验·PMO】你已执行拉表，但 **宏观看板正文**须通过 **`mcp:atom_lark_notifier`** "
            "发到群内（`markdown_content` + `title` + `chat_id`），**禁止**把完整 §1.4 战报只写在 Final Answer。\n"
            "**分支 A 的 `markdown_content` 必须同时包含三张核心表**（缺一不可）：\n"
            "① `📊 需求进度全览`（**6 列**：优先级|需求名称|时间跨度|参与人|完成度|状态；"
            "完成度列写 workflow_completion_pct 进度条+%（与泳道 rank 同源，禁止条数占比）；"
            "状态须写泳道步骤如「开发/验收·技术开发」，"
            "**禁止**待开始/进行中/已完成；**禁止**优先级/风险说明列）\n"
            "② `👥 人员任务矩阵`（每人一行；任务列每条独立一行用 `<br>`，格式 `【P0】任务名 · 状态`，"
            "禁止 ** 与分号挤一段；**行序**：🚨落后→🟡偏闲→✅正常）\n"
            "③ `📦 版本发布需求映射`（按版本/Sprint归集，含每条需求当前状态）\n"
            "**注意**：分支 D 的精简格式（只含人员矩阵）仅适用于 `resource_monitor` 触发词，**对分支 A 完全无效**。\n"
            "请立即输出 ReAct（勿写 Final Answer）：\n"
            "Thought: …\n"
            "Action: mcp:atom_lark_notifier\n"
            "Action Input: JSON（全文三表战报送 `markdown_content`，`title` 用 `【K11 · PMO 宏观看板】` 类）。\n"
            f"**须对每个投递目标各调用一次**（{_pmo_delivery_targets_user_hint(ctx)}）。\n"
            "推送成功后再用 ≤3 句 Final Answer 确认 Observation 状态即可。"
        ),
    })
    return True




_PMO_POLICY_METADATA_KEYS = (
    "_pmo_atom_lark_notify_ok",
    "_pmo_false_lark_sent_guard_count",
    "_pmo_bi_project_context_invoked",
    "_pmo_bi_project_context_ok",
    "_pmo_branch_a_bi_pull_guard_count",
    "_pmo_branch_a_notifier_dump_guard_count",
    "_pmo_branch_a_post_bi_stall_guard_count",
    "_pmo_db_query_count",
    "_pmo_analysis_probes",
    "_pmo_step1_map_done",
    "_pmo_restart_count",
    "_pmo_notifier_block_count",
    "_pmo_markdown_fix_only",
    "_pmo_markdown_fix_phase",
    "_pmo_markdown_fix_supplemental_count",
    "_pmo_assembly_phase",
    "_pmo_last_notifier_markdown_preview",
    "_pmo_notifier_chats_success",
    "_pmo_last_gfm_draft_fingerprint",
    "_pmo_force_push_exit_guard_count",
)


def reset_pmo_policy_metadata(ctx: PipelineContext) -> None:
    for key in _PMO_POLICY_METADATA_KEYS:
        ctx.metadata.pop(key, None)


def capture_pmo_debug_thought(ctx: PipelineContext, thought: str) -> None:
    if thought:
        ctx.metadata["_pmo_debug_thought"] = str(thought or "").strip()


def append_pmo_debug_action(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    iteration: int,
) -> None:
    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_debug_action

        append_pmo_debug_action(
            tool=str(tool or ""),
            inp=str(inp or ""),
            iteration=iteration,
            run_id=str(ctx.run_id or ""),
            thought=str(ctx.metadata.pop("_pmo_debug_thought", "") or ""),
        )
    except Exception:
        return


def append_pmo_debug_observation(
    ctx: PipelineContext,
    *,
    tool: str,
    observation_full: str,
    iteration: int,
) -> None:
    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_debug_observation

        append_pmo_debug_observation(
            tool=str(tool or ""),
            observation_full=str(observation_full or ""),
            iteration=iteration,
            run_id=str(ctx.run_id or ""),
        )
    except Exception:
        return


def pmo_publisher_tool_lock_enabled(implicit_attribution: Any) -> bool:
    return bool(
        implicit_attribution
        and isinstance(implicit_attribution, dict)
        and implicit_attribution.get("pmo_publisher_tool_lock")
    )


def apply_pmo_metadata_seed(metadata: dict[str, Any], implicit_attribution: Any) -> None:
    if not implicit_attribution or not isinstance(implicit_attribution, dict):
        return
    for key in ("pmo_analysis_only", "pmo_db_ready", "pmo_multi_agent_complete", "pmo_init"):
        if key in implicit_attribution:
            metadata[key] = bool(implicit_attribution[key])
    try:
        from l3_node.pmo_multi_agent_orchestrator import apply_pmo_multi_agent_metadata_seed

        apply_pmo_multi_agent_metadata_seed(metadata, implicit_attribution)
    except Exception:
        return


def reject_pmo_final_answer_guards(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """Return True when PMO policy injected a corrective turn."""
    return any(
        guard(ctx, messages, response, ans, via=via)
        for guard in (
            _reject_pmo_branch_a_missing_bi_pull_guard,
            _reject_pmo_branch_a_post_bi_fs_stall_guard,
            _reject_pmo_branch_a_board_without_notifier_guard,
            _reject_pmo_branch_a_init_completion_guard,
            _reject_pmo_branch_a_force_push_exit_guard,
            _reject_pmo_branch_a_analysis_incomplete_delivery_guard,
            _reject_pmo_false_lark_sent_guard,
        )
    )


def before_pmo_tool_exec(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    response: str,
) -> tuple[str, str | None, bool, bool]:
    """Apply PMO pre-tool policy.

    Returns ``(possibly_sanitized_input, observation, skip_tool_invoke,
    skip_lark_invoke)``.  ``observation`` is set when the policy blocks the tool
    and wants to feed a synthetic observation back into ReAct.
    """
    observation: str | None = None
    skip_tool_invoke = False
    skip_lark_invoke = False

    if _pmo_lark_push_guard_channel_active(ctx):
        try:
            chat_guard = _pmo_blocked_invalid_war_report_chat_observation(str(tool or ""), inp, ctx)
            if chat_guard:
                observation = chat_guard
                skip_tool_invoke = True
                logger.warning("[L3 Agent][PMO] blocked invalid push chat_id tool=%s", tool)
        except Exception as e:
            logger.debug("[L3 Agent][PMO] push chat_id guard skipped: %s", e)

    if _pmo_lark_push_guard_channel_active(ctx):
        try:
            _pmo_sync_assembly_phase_from_thought(ctx, response)
            init_analysis = _pmo_blocked_analysis_tools_during_init(str(tool or ""), ctx)
            if init_analysis:
                observation = init_analysis
                skip_tool_invoke = True
                logger.info("[L3 Agent][PMO] blocked analysis/non-import tool in INIT mode tool=%s", tool)
            init_block = _pmo_branch_a_blocked_init_tools_during_analysis(str(tool or ""), ctx)
            if init_block and observation is None:
                observation = init_block
                skip_tool_invoke = True
                logger.info("[L3 Agent][PMO] blocked init/pull tool during DB-only analysis tool=%s", tool)
            if observation is None:
                rerun_block = _pmo_branch_a_blocked_rerun_db_after_markdown_block(str(tool or ""), ctx, inp)
                if rerun_block:
                    observation = rerun_block
                    skip_tool_invoke = True
                    logger.info("[L3 Agent][PMO] blocked duplicate DB query during markdown fix tool=%s", tool)
            if observation is None:
                assembly_block = _pmo_branch_a_blocked_force_assembly_round(str(tool or ""), ctx, response)
                if assembly_block:
                    observation = assembly_block
                    skip_tool_invoke = True
                    logger.info("[L3 Agent][PMO] blocked tool during assembly round tool=%s", tool)
            if observation is None:
                invalid_field = _pmo_branch_a_blocked_invalid_field_sql(str(tool or ""), inp, ctx)
                if invalid_field:
                    observation = invalid_field
                    skip_tool_invoke = True
                    logger.info("[L3 Agent][PMO] blocked invalid SQL field tool=%s", tool)
            if observation is None:
                step1_block = _pmo_branch_a_blocked_duplicate_step1_map(str(tool or ""), inp, ctx)
                if step1_block:
                    observation = step1_block
                    skip_tool_invoke = True
                    logger.info("[L3 Agent][PMO] blocked duplicate Step1 map query tool=%s", tool)
        except Exception as e:
            logger.debug("[L3 Agent][PMO] init switch guard skipped: %s", e)

    if (tool or "").replace("mcp:", "").strip() == "atom_lark_notifier" and _pmo_lark_push_guard_channel_active(ctx):
        try:
            sanitized = _pmo_sanitize_atom_lark_notifier_inp(inp, ctx)
            if sanitized != inp:
                inp = sanitized
                logger.info("[L3 Agent][PMO] atom_lark_notifier input sanitized")
        except Exception as e:
            logger.debug("[L3 Agent][PMO] atom_lark_notifier sanitize skipped: %s", e)
        try:
            assembly_block = _pmo_branch_a_blocked_force_assembly_round("mcp:atom_lark_notifier", ctx, response)
            if assembly_block:
                observation = assembly_block
                skip_lark_invoke = True
                logger.info("[L3 Agent][PMO] blocked premature notifier during assembly round")
            else:
                premature = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
                if premature:
                    observation = premature
                    skip_lark_invoke = True
                    logger.info("[L3 Agent][PMO] blocked premature Lark push before PMO report completion")
        except Exception as e:
            logger.debug("[L3 Agent][PMO] premature Lark guard skipped: %s", e)

    return inp, observation, skip_tool_invoke, skip_lark_invoke


def after_pmo_tool_exec(
    ctx: PipelineContext,
    *,
    tool: str,
    inp: str,
    response: str,
    observation_full: str,
    iteration: int,
    max_iterations: int,
) -> str:
    """Track PMO tool results and append PMO guidance to observation text."""
    try:
        observation_full = _pmo_append_react_budget_warning(
            ctx,
            str(observation_full or ""),
            iteration=iteration,
            max_iterations=max_iterations,
        )
    except Exception as e:
        logger.debug("[L3 Agent][PMO] budget warning skipped: %s", e)

    try:
        canon = _pmo_canonical_tool_id(tool or "")
        if canon == "atom_bi_project_context":
            ctx.metadata["_pmo_bi_project_context_invoked"] = True
            if _bi_project_context_observation_suggests_success(observation_full):
                ctx.metadata["_pmo_bi_project_context_ok"] = True
        if canon == "db_query":
            _pmo_track_db_query_sql(ctx, str(tool or ""), inp)
            observation_full = _pmo_append_draft_gfm_hint_after_db_query(
                ctx, response, str(observation_full or "")
            )
        if canon == "pmo_macro_dashboard_push":
            _pmo_track_macro_dashboard_push_success(ctx, observation_full)
            if _pmo_observation_is_foreground_tool_timeout(observation_full):
                observation_full = (
                    f"{observation_full}\n\n"
                    "[PMO policy] macro_dashboard_push timed out in the foreground, but it may still be sending in the background. "
                    "Do not fall back to atom_lark_notifier; retry macro_dashboard_push or verify delivery."
                )
            elif ctx.metadata.get("_pmo_macro_dashboard_push_ok"):
                observation_full = _pmo_append_macro_dashboard_delivery_hint(observation_full)
        if canon == "atom_lark_notifier":
            if _lark_notifier_observation_suggests_success(observation_full):
                _pmo_track_notifier_chat_success(ctx, inp, observation_full)
                if _pmo_db_analysis_mode(ctx):
                    if (
                        _pmo_branch_a_notifier_markdown_is_complete(inp)
                        and _pmo_branch_a_push_prerequisites_met(ctx)
                        and _pmo_branch_a_delivery_complete(ctx)
                    ):
                        ctx.metadata["_pmo_atom_lark_notify_ok"] = True
                elif _pmo_branch_a_requires_bi_pull(ctx):
                    if _pmo_branch_a_notifier_markdown_is_complete(inp):
                        ctx.metadata["_pmo_atom_lark_notify_ok"] = True
                    else:
                        logger.warning("[L3 Agent][PMO] notifier succeeded but markdown_content misses required Branch A sections")
                else:
                    ctx.metadata["_pmo_atom_lark_notify_ok"] = True
    except Exception as e:
        logger.debug("[L3 Agent][PMO] observation tracking skipped: %s", e)

    return str(observation_full or "")


def pmo_observation_nudge(ctx: PipelineContext, observation_full: str, tool: str) -> str:
    try:
        return _pmo_markdown_incomplete_system_nudge(ctx, str(observation_full or ""), str(tool or "")) or ""
    except Exception as e:
        logger.debug("[L3 Agent][PMO] markdown nudge skipped: %s", e)
        return ""

