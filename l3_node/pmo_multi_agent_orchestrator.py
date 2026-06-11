"""
PMO-Copilot 方案 B：三阶段多 Agent 编排。

阶段一 FanOut（并行捞数）→ 阶段三 run_agent（排版发报）。阶段二交叉审计（Auditor）默认关闭。

代码锚点：scripts/run_pmo_copilot_skill.py --multi-agent
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKER_C_SPEC_PATH = _REPO_ROOT / "docs" / "architecture" / "PMO_WORKER_C_SPEC.md"
_WORKER_B_SPEC_PATH = _REPO_ROOT / "docs" / "architecture" / "PMO_WORKER_B_SPEC.md"
_WORKER_D_SPEC_PATH = _REPO_ROOT / "docs" / "architecture" / "PMO_WORKER_D_SPEC.md"

from l3_node.pmo_multi_agent_queries import (
    WORKER_A_MAX_ITERATIONS,
    WORKER_A_TASK,
    WORKER_A_TASK_PREVIEW,
    WORKER_B_AGENT_MAX_ITERATIONS,
    WORKER_B_AGENT_TASK_PREVIEW,
    WORKER_C_MAX_ITERATIONS,
    WORKER_C_TASK,
    WORKER_C_TASK_PREVIEW,
    WORKER_D_AGENT_MAX_ITERATIONS,
    WORKER_D_TASK,
    WORKER_D_TASK_PREVIEW,
    build_worker_b_agent_task,
    build_worker_d_agent_task,
)
from l3_node.pmo_report_format import (
    PMO_DEMAND_TABLE_PUBLISHER_SPEC,
    PMO_WAR_REPORT_LAYOUT_CONTRACT,
)
from l3_node.agent_core import PMO_BRANCH_A_MIN_DB_QUERIES, PMO_BRANCH_A_PERSONNEL_SSOT_VIEW
from l3_node.agent_core import (
    PMO_BRANCH_A_PRODUCT_VIEW_ALTS,
    PMO_BRANCH_A_REQUIRED_CROSS_VIEWS,
)

logger = logging.getLogger("pmo_multi_agent")

PMO_AUDITOR_CONTEXT_MAX_CHARS = 28000

# SubAgent 默认 system_prefix 截断 1200 字会裁掉 Worker 专有规则；PMO 角色显式放宽。
PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS = 3200

# 三 Worker 共用：不含 Person/Epic/状态 写法（避免与 queries.py SSOT / 各 Worker 专有块矛盾）。
_PMO_WORKER_SHARED = (
    "你是 PMO 数据搬砖工（Analyst）。\n"
    "职责：用 core:db_query 执行 **本 Worker 任务体中的编号 SQL**，Observation → **合法 JSON** Final Answer。\n"
    "通用：字段以任务体 SQL / columns_json 为准，禁止臆造键名；"
    "Sprint 用 json_extract(fields,'$.Sprint')（禁止 Sprint 的 [0].text）。\n"
    "db_query Action Input：**只写裸 SELECT**（从 SELECT 到 `;`），禁止 ``` 围栏、禁止 `{\"sql\":...}`。\n"
    "Thought 开头「已完成: …」；同编号 SQL 仅 1 次；hints/error 同编号最多重试 2 次。\n"
    "禁止捏造；Final Answer **仅 JSON**（禁止 GFM 战报）。\n"
    "编号只认任务体：B-S1/B-4/B-SUP 或 C-1/C-2/C-3（**禁止**单 Agent 旧称 Step3/Step4/Step5）。\n"
    "SQL 细则以 **user 任务体逐字 SQL** 为准，本 system 不重复旧版单表写法。\n"
)

_PMO_WORKER_A_RULES = (
    "【Worker A · Step 1+2 字典】\n"
    "- **唯一**负责：`pmo_views_meta`（五视图 IN）+ 各视图 `fields` 样本（GROUP BY source_view）。\n"
    "- Final Answer：views_meta[]、samples[]、field_mapping（按视图 JSON 键路径）。\n"
    "- **禁止** B-S1/B-4/B-SUP / C-1～C-3 业务明细 SQL。\n"
)

_PMO_WORKER_B_RULES_INLINE = (
    "【Worker B · vewCz1FFJi + vewpI8lyYw · B-TOOL 优先】\n"
    "- **步骤 0（必须）**：`core:pmo_personnel_report` + `{\"recent_window\": true}`；"
    "宿主已预取 personnel_tasks[] 时禁止重跑。\n"
    "- **current_sprint**：宿主已按 **sd≤today** 算出；**禁止**用 recent_sprints[0] 覆盖。\n"
    "- **兜底**：仅步骤 0 缺 requirement_context 时执行 B-SUP（db_query）；禁止重跑 B-S1/B-4。\n"
    "- Final Answer：current_sprint + recent_sprints[] + personnel_tasks[] + requirement_context[]。\n"
)


def _load_worker_b_system_prefix() -> str:
    spec = ""
    try:
        if _WORKER_B_SPEC_PATH.is_file():
            spec = _WORKER_B_SPEC_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if spec:
        return _PMO_WORKER_B_RULES_INLINE + "\n" + spec + "\n"
    return _PMO_WORKER_B_RULES_INLINE


_PMO_WORKER_C_RULES_INLINE = (
    "【Worker C · vewpI8lyYw · C-TOOL 优先】\n"
    "- **步骤 0（必须）**：`core:pmo_sprint_epic_report` + `{\"recent_window\": true}`；"
    "宿主已预取 epics[] 时禁止重跑。\n"
    "- **兜底**：仅步骤 0 失败后执行 user 任务体 C-1→C-2→C-3（逐字 SQL）。\n"
    "⛔ 禁止仅用 `父记录[0].text IS NULL`；禁止 Person/状态 的 [0].text（malformed JSON）。\n"
    "epics[] 仅大需求；子任务进 epic_children[]。\n"
)


def _load_worker_c_system_prefix() -> str:
    """PMO_WORKER_C_SPEC.md + 内联护栏（截断时内联置前）。"""
    spec = ""
    try:
        if _WORKER_C_SPEC_PATH.is_file():
            spec = _WORKER_C_SPEC_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if spec:
        return _PMO_WORKER_C_RULES_INLINE + "\n" + spec + "\n"
    return _PMO_WORKER_C_RULES_INLINE


_PMO_WORKER_D_RULES_INLINE = (
    "【Worker D · 发版 Epic 清单 · D-TOOL 优先】\n"
    "- **步骤 0（必须）**：`core:pmo_release_epic_mapping` + `{}`；"
    "宿主已预取 markdown_section 时禁止重跑。\n"
    "- **禁止** core:db_query / Version Goal 统计 / 人员表查询。\n"
    "Final Answer：completed_epics[] + markdown_section + window_* + completed_sql_ids 含 **D-TOOL**。\n"
)


def _load_worker_d_system_prefix() -> str:
    spec = ""
    try:
        if _WORKER_D_SPEC_PATH.is_file():
            spec = _WORKER_D_SPEC_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if spec:
        return _PMO_WORKER_D_RULES_INLINE + "\n" + spec + "\n"
    return _PMO_WORKER_D_RULES_INLINE


PMO_WORKER_A_ROLE: dict[str, Any] = {
    "id": "analyst",
    "system_prefix_max_chars": PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS,
    "system_prefix": _PMO_WORKER_A_RULES + "\n" + _PMO_WORKER_SHARED,
    "allowed_tools": ["core:db_query"],
}

PMO_WORKER_B_ROLE: dict[str, Any] = {
    "id": "analyst",
    "system_prefix_max_chars": PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS,
    "system_prefix": _load_worker_b_system_prefix() + "\n" + _PMO_WORKER_SHARED,
    "allowed_tools": ["core:pmo_personnel_report", "core:db_query"],
}

PMO_WORKER_C_ROLE: dict[str, Any] = {
    "id": "analyst",
    "system_prefix_max_chars": PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS,
    "system_prefix": _load_worker_c_system_prefix() + "\n" + _PMO_WORKER_SHARED,
    "allowed_tools": ["core:pmo_sprint_epic_report", "core:db_query"],
}

PMO_WORKER_D_ROLE: dict[str, Any] = {
    "id": "analyst",
    "system_prefix_max_chars": PMO_WORKER_SYSTEM_PREFIX_MAX_CHARS,
    "system_prefix": _load_worker_d_system_prefix() + "\n" + _PMO_WORKER_SHARED,
    "allowed_tools": ["core:pmo_release_epic_mapping"],
}

# 兼容旧引用（等同 Worker B，FanOut 应使用 A/B/C/D 分角色）
PMO_WORKER_DB_ROLE = PMO_WORKER_B_ROLE

PMO_AUDITOR_ROLE: dict[str, Any] = {
    "id": "reviewer",
    "system_prefix": (
        "你是 PMO 交叉审计员（Reviewer / Auditor）。\n"
        "⛔ **你无任何可用工具**——禁止调用 db_query / read_file / shell_exec / 任何 MCP。\n"
        "阶段一 Worker A/B/C 的全部 JSON **已在本条 user 消息中**（带 ## 小节标题），"
        "请 **直接阅读文本** 做 Step 6 交叉分析，**禁止**尝试读取本地文件或 context_data 路径。\n"
        "须检查：\n"
        "1. **大需求层级**：Worker C 的 epics[] 是否仅为顶层大需求（非部门小需求重复占行）；"
        "epic_children[] 是否通过 parent_epic 正确挂接\n"
        "2. 幽灵需求：Epic/主表有、人员看板（vewCz1FFJi）无（或反之）——须引用具体 Requirement 名称\n"
        "3. 状态倒挂：同一需求在不同视图状态矛盾\n"
        "4. 人员超载：👥 须以 Worker B 的 personnel_tasks[]（vewCz1FFJi）为准；"
        "按 §1.4.1b「计划周期×完成进度×当前时间」节奏判定 🚨/🟡/✅（**禁止** task_cnt 排名定过载）\n"
        "5. Sprint 集合差：两视图 Sprint 不一致项\n"
        "输出：**项目风险诊断书**（Markdown，分 ## 章节；每条风险含 ⚠️ 与依据）。\n"
        "降级规则：若某节数据不足，须标注「数据不足·结论仅供参考」，**禁止**假装已完成完整分析。\n"
        "禁止因任何原因调用工具；数据不足时基于已有 JSON 推理并标注可信度。\n"
    ),
    "allowed_tools": [],
}

PMO_PUBLISHER_USER_TEMPLATE = """【PMO 多 Agent · 阶段三 · 排版发报】
前序阶段已完成数据捞取（FanOut Worker A/B/C）。**禁止** core:db_query / mirror_import / bi_project_context。

**第一步（默认 · 宏观看板）**：
- `Action: core:pmo_macro_dashboard_push`
- `Action Input: {{}}` **仅此**（禁止传 chat_id；宿主注入主群 + 代码内置监控群）
- 工具内按 .env / 触发群推送；Observation `status` 为 success/partial 后 **Final Answer ≤3 句**，引用 message_id；**禁止**再调 atom_lark_notifier。
- 仅当 push 失败或用户明确要求「风险写入表内/自定义版式」时，执行下方手工任务。

**兜底任务（push 失败或特殊需求时）**：
1. 将下方 JSON 填入 §1.4 三张 **GFM Markdown 表**（语义见 §1.2.3）；**风险诊断书不得写入 📊 表内列**：
{demand_table_spec}
   - 📊 仅 `current_sprint`（本周）大需求，每行一个 Epic；子任务汇总进「参与人/完成度/状态」三列（参与人须含 Epic 父记录链接链子任务，见 `pmo_epic_aggregate.epic_participants`）
   - 👥 人员任务矩阵 — **以 Worker B 的 by_person / personnel_tasks[] 为准**；**每人一行**；任务列全量 `<br>` + `row_height=low`（表内一行，hover 多行）；**禁止**「等N项」；**行序** 🚨延期→🚨进度落后→🟡→✅
   - {layout_contract}
   - 📦 版本发布需求映射 — **以 Worker D 的 markdown_section 为准**（发版邮件窗内已完成 Epic）；**禁止** Version Goal 填写率
2. 每表须含表头行 + `|---|---|` 分隔 + 至少 3 行数据（缺口用 ⚠️ 占位行）
3. 将 **完整 markdown_content 全文** 写入 mcp:atom_lark_notifier（**两次**：宿主分别注入主群与代码内置监控群；**禁止** Action Input 手写 `oc_…` 或 `webhook_url`）：
   - 均须 `native_table_card: true`
4. Final Answer 仅在全部目标 notifier success 后 ≤3 句确认

【阶段一 · 字段映射 JSON（Worker A）】
{worker_a}

【阶段一 · 人员任务 JSON（Worker B · personnel_tasks 主表=vewCz1FFJi）】
{worker_b}

【阶段一 · 大需求与子任务 JSON（Worker C · epics=周汇报大需求）】
{worker_c}

【阶段一 · 发版 Epic 清单 JSON（Worker D · completed_epics[]）】
{worker_d}
"""


def build_pmo_multi_agent_implicit_attribution() -> dict[str, Any]:
    """阶段三 run_agent 用：标记多 Agent 已完成探针，允许直推。"""
    views = set(PMO_BRANCH_A_REQUIRED_CROSS_VIEWS)
    views.add(PMO_BRANCH_A_PERSONNEL_SSOT_VIEW)
    views.update(PMO_BRANCH_A_PRODUCT_VIEW_ALTS)
    probes = {
        "sprint": True,
        "status": True,
        "personnel": True,
        "personnel_kanban": True,
        "version": True,
        "epic": True,
        "cross_view_6a": True,
        "cross_view_6b": True,
    }
    return {
        "channel": "pmo_copilot_cli",
        "source": "pmo_multi_agent_phase3",
        "pmo_publisher_tool_lock": True,
        "pmo_analysis_only": True,
        "pmo_db_ready": True,
        "pmo_multi_agent_complete": True,
        "pmo_multi_agent_seed": {
            "_pmo_db_query_count": PMO_BRANCH_A_MIN_DB_QUERIES,
            "_pmo_analysis_probes": probes,
            "_pmo_views_queried": sorted(views),
            "_pmo_step1_map_done": True,
        },
    }


def apply_pmo_multi_agent_metadata_seed(metadata: dict[str, Any], implicit: dict[str, Any]) -> None:
    """将 implicit_attribution.pmo_multi_agent_seed 合并进 run_agent metadata。"""
    if not implicit.get("pmo_multi_agent_complete"):
        return
    metadata["pmo_multi_agent_complete"] = True
    seed = implicit.get("pmo_multi_agent_seed")
    if not isinstance(seed, dict):
        return
    for k, v in seed.items():
        metadata[k] = v


@dataclass
class PmoMultiAgentResult:
    status: str  # completed | partial | failed
    phase1: Any = None
    phase2_output: str = ""
    phase3_answer: str = ""
    worker_a: str = ""
    worker_b: str = ""
    worker_c: str = ""
    worker_d: str = ""
    audit_report: str = ""
    errors: list[str] = field(default_factory=list)

    def format_summary(self) -> str:
        lines = [f"[PMO Multi-Agent] status={self.status}"]
        if self.errors:
            lines.append("  错误: " + "; ".join(self.errors[:5]))
        if self.phase3_answer:
            lines.append(f"  Final: {self.phase3_answer[:200]}…")
        return "\n".join(lines)


def _phase1_fanout_items(
    host_b_seed: dict[str, Any] | None = None,
    host_c_seed: dict[str, Any] | None = None,
    host_d_seed: dict[str, Any] | None = None,
    *,
    include_workers: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    include = include_workers or frozenset({"a", "b", "c", "d"})
    worker_b_task = build_worker_b_agent_task(host_b_seed)
    worker_b_preview = WORKER_B_AGENT_TASK_PREVIEW
    worker_b_max_iter = WORKER_B_AGENT_MAX_ITERATIONS
    worker_b_context: dict[str, Any] | None = None
    worker_b_context_max = 0
    if host_b_seed:
        worker_b_context = {
            "说明": "宿主预取 JSON（core:pmo_personnel_report recent_window 已执行，勿重跑步骤 0）",
            **host_b_seed,
        }
        worker_b_context_max = 22000

    workers: list[tuple[str, str, int, str, dict[str, Any], dict[str, Any], str]] = [
        ("Worker A", WORKER_A_TASK_PREVIEW, WORKER_A_MAX_ITERATIONS, WORKER_A_TASK, PMO_WORKER_A_ROLE, {}, "a"),
        (
            "Worker B",
            worker_b_preview,
            worker_b_max_iter,
            worker_b_task,
            PMO_WORKER_B_ROLE,
            {
                "context_data": worker_b_context,
                "context_max_chars": worker_b_context_max,
            },
            "b",
        ),
        ("Worker C", WORKER_C_TASK_PREVIEW, WORKER_C_MAX_ITERATIONS, WORKER_C_TASK, PMO_WORKER_C_ROLE, {}, "c"),
        (
            "Worker D",
            WORKER_D_TASK_PREVIEW,
            WORKER_D_AGENT_MAX_ITERATIONS,
            build_worker_d_agent_task(host_d_seed),
            PMO_WORKER_D_ROLE,
            {},
            "d",
        ),
    ]
    items: list[dict[str, Any]] = []
    worker_c_extra: dict[str, Any] = {}
    if host_c_seed:
        worker_c_extra = {
            "context_data": {
                "说明": "宿主预取 JSON（core:pmo_sprint_epic_report recent_window 已执行，勿重跑步骤 0）",
                **host_c_seed,
            },
            "context_max_chars": 22000,
        }
    worker_d_extra: dict[str, Any] = {}
    if host_d_seed:
        worker_d_extra = {
            "context_data": {
                "说明": "宿主预取 JSON（core:pmo_release_epic_mapping 已执行，勿重跑步骤 0）",
                **host_d_seed,
            },
            "context_max_chars": 16000,
        }
    for agent_label, task_short, max_iter, task_body, role, extra, worker_key in workers:
        if worker_key not in include:
            continue
        if agent_label == "Worker C" and host_c_seed:
            extra = {**extra, **worker_c_extra}
        if agent_label == "Worker D" and host_d_seed:
            extra = {**extra, **worker_d_extra}
        item: dict[str, Any] = {
            "role": role,
            "task": task_body,
            "max_iterations": max_iter,
            "_debug_phase": 1,
            "_debug_phase_label": "并行捞数",
            "_debug_agent_label": agent_label,
            "_debug_role_label": "analyst · 数据搬砖工",
            "_debug_task_preview": task_short,
        }
        if extra.get("context_data"):
            item["context_data"] = extra["context_data"]
            if extra.get("context_max_chars"):
                item["context_max_chars"] = extra["context_max_chars"]
        items.append(item)
    return items


def _worker_d_mail_delay_sec() -> float:
    raw = os.environ.get("PMO_WORKER_D_MAIL_DELAY_SEC", "5").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


def _merge_fanout_results(*parts: Any) -> Any:
    from l3_node.primitives.multi_agent.fanout import FanoutResult

    items: list[Any] = []
    ok_count = 0
    failed_count = 0
    total = 0
    elapsed = 0.0
    for part in parts:
        if not part:
            continue
        items.extend(part.items)
        ok_count += part.ok_count
        failed_count += part.failed_count
        total += part.total
        elapsed += part.elapsed_sec
    if total == 0:
        status = "completed"
    elif failed_count == 0:
        status = "completed"
    elif ok_count == 0:
        status = "failed"
    else:
        status = "partial"
    return FanoutResult(
        status=status,
        ok_count=ok_count,
        failed_count=failed_count,
        total=total,
        degraded=failed_count > 0,
        items=items,
        elapsed_sec=elapsed,
    )


def _fanout_worker_label(item: Any) -> str:
    preview = str(getattr(item, "task_preview", "") or "")
    if "Worker D" in preview or "D-TOOL" in preview or "发版" in preview:
        return "Worker D"
    idx = getattr(item, "index", 0)
    return {1: "Worker A", 2: "Worker B", 3: "Worker C", 4: "Worker D"}.get(idx, f"Worker {idx}")


async def run_pmo_multi_agent_workflow(
    engine: Any,
    *,
    parent_allowed_skills: list[str],
    on_status: Any | None = None,
    refresh_pull_markdown: bool | None = None,
) -> PmoMultiAgentResult:
    """执行 PMO 三阶段多 Agent 工作流；阶段三由调用方 run_agent 完成。"""
    from l3_node.primitives.multi_agent.fanout import fanout_parallel
    result = PmoMultiAgentResult(status="failed")

    def _status(msg: str) -> None:
        logger.info("[PMO Multi-Agent] %s", msg)
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    pull_skip_reason = ""
    if refresh_pull_markdown is None:
        from l3_node.pmo_init_runner import pmo_resolve_refresh_pull_markdown

        refresh_pull_markdown, pull_skip_reason = pmo_resolve_refresh_pull_markdown()
    if not refresh_pull_markdown:
        _status(f"阶段零：跳过拉表（{pull_skip_reason or '今日已有数据'}）")
    elif refresh_pull_markdown:
        import asyncio

        from l3_node.pmo_init_runner import format_pmo_init_direct_summary, run_pmo_init_direct

        _status("阶段零：飞书拉表 → JSON 落盘 md/records.json + mirror_import…")
        try:
            refresh_result = await asyncio.to_thread(run_pmo_init_direct)
            _status(format_pmo_init_direct_summary(refresh_result).replace("\n", " | "))
            if str(refresh_result.get("status") or "").lower() != "ok":
                result.status = "failed"
                result.errors.append(
                    str(refresh_result.get("message") or "拉表/镜像入库失败，已中止 FanOut")
                )
                return result
        except Exception as e:
            logger.exception("[PMO Multi-Agent] refresh pull markdown failed")
            result.status = "failed"
            result.errors.append(f"拉表落盘异常: {e}")
            return result

    _status("阶段一：FanOut 并行捞数（Worker A/B/C）…")
    host_b_seed: dict[str, Any] = {}
    host_c_seed: dict[str, Any] = {}
    host_d_seed: dict[str, Any] = {}
    try:
        from l3_node.pmo_worker_result_backfill import run_worker_b_host_bootstrap

        host_b_seed = run_worker_b_host_bootstrap()
        _status(
            f"Worker B 宿主预取：current_sprint={host_b_seed.get('current_sprint')} "
            f"personnel_tasks={len(host_b_seed.get('personnel_tasks') or [])} 行"
        )
    except Exception:
        logger.exception("[PMO Multi-Agent] Worker B host bootstrap failed")
    try:
        from l3_node.pmo_worker_result_backfill import run_worker_c_host_bootstrap

        host_c_seed = run_worker_c_host_bootstrap()
        _status(
            f"Worker C 宿主预取：epics={len(host_c_seed.get('epics') or [])} "
            f"epic_children={len(host_c_seed.get('epic_children') or [])}"
        )
    except Exception:
        logger.exception("[PMO Multi-Agent] Worker C host bootstrap failed")
    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_debug_phase_begin, append_pmo_debug_status

        append_pmo_debug_phase_begin(
            1,
            "并行捞数 · FanOut",
            detail="Worker A(字典) / B(B-TOOL) / C(C-TOOL) 并行；D(D-TOOL) 错开于 A/B/C 之后",
        )
        if host_b_seed.get("personnel_tasks"):
            append_pmo_debug_status(
                f"Worker B 宿主预取完成：current_sprint={host_b_seed.get('current_sprint')} "
                f"personnel_tasks={len(host_b_seed['personnel_tasks'])} 行，"
                f"Sprint={host_b_seed.get('sprint_names_for_in')}"
            )
    except Exception:
        pass

    import asyncio

    phase1_abc = await fanout_parallel(
        _phase1_fanout_items(
            host_b_seed or None,
            host_c_seed or None,
            include_workers=frozenset({"a", "b", "c"}),
        ),
        engine,
        max_concurrent=3,
        delegate_depth=1,
        item_max_iterations=16,
        parent_allowed_skills=parent_allowed_skills,
    )

    delay_sec = _worker_d_mail_delay_sec()
    if delay_sec > 0:
        _status(
            f"Worker D 邮件 API 错开 {delay_sec:.0f}s（避开阶段零拉表与 A/B/C FanOut 并发）…"
        )
        await asyncio.sleep(delay_sec)

    try:
        from l3_node.pmo_worker_result_backfill import run_worker_d_host_bootstrap_with_retry

        host_d_seed = run_worker_d_host_bootstrap_with_retry()
        _status(
            f"Worker D 宿主预取：completed_count={host_d_seed.get('completed_count')} "
            f"release_mails={host_d_seed.get('release_mails_found')}"
            + ("（部分邮件详情失败，已降级）" if host_d_seed.get("degraded") else "")
        )
    except Exception:
        logger.exception("[PMO Multi-Agent] Worker D host bootstrap failed")

    phase1_d = await fanout_parallel(
        _phase1_fanout_items(
            host_d_seed=host_d_seed or None,
            include_workers=frozenset({"d"}),
        ),
        engine,
        max_concurrent=1,
        delegate_depth=1,
        item_max_iterations=16,
        parent_allowed_skills=parent_allowed_skills,
    )
    for it in phase1_d.items:
        it.index = 4
    phase1 = _merge_fanout_results(phase1_abc, phase1_d)
    result.phase1 = phase1

    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_debug_phase_summary

        worker_labels = {1: "Worker A", 2: "Worker B", 3: "Worker C", 4: "Worker D"}
        summary_lines: list[str] = []
        for it in phase1.items:
            label = _fanout_worker_label(it) or worker_labels.get(it.index, f"Worker {it.index}")
            if it.ok:
                preview = _clip(it.result, 80).replace("\n", " ")
                summary_lines.append(f"✅ {label}: {preview or '（有输出）'}")
            else:
                summary_lines.append(f"❌ {label}: {_clip(it.error, 120)}")
        append_pmo_debug_phase_summary(
            1,
            "并行捞数 · FanOut",
            ok_count=phase1.ok_count,
            total=phase1.total,
            elapsed_sec=phase1.elapsed_sec,
            item_lines=summary_lines,
        )
    except Exception:
        pass

    if phase1.ok_count == 0:
        result.errors.append("阶段一全部失败")
        result.status = "failed"
        return result

    # A/B/C 至少一项成功即可继续；Worker D 单独失败不阻断发报
    abc_ok = sum(1 for it in phase1_abc.items if it.ok)
    if abc_ok == 0:
        result.errors.append("阶段一 A/B/C 全部失败")
        result.status = "failed"
        return result

    by_idx = {item.index: item for item in phase1.items}
    result.worker_a = by_idx[1].result if by_idx.get(1) and by_idx[1].ok else ""
    raw_worker_b = by_idx[2].result if by_idx.get(2) and by_idx[2].ok else ""
    raw_worker_c = by_idx[3].result if by_idx.get(3) and by_idx[3].ok else ""
    raw_worker_d = by_idx[4].result if by_idx.get(4) and by_idx[4].ok else ""
    result.worker_c = raw_worker_c

    try:
        from l3_node.pmo_worker_result_backfill import merge_worker_b_result

        if host_b_seed:
            result.worker_b = merge_worker_b_result(host_b_seed, raw_worker_b)
        else:
            result.worker_b = raw_worker_b
    except Exception:
        logger.exception("[PMO Multi-Agent] Worker B merge failed")
        result.worker_b = raw_worker_b

    try:
        from l3_node.pmo_worker_result_backfill import backfill_worker_outputs, merge_worker_c_result

        result.worker_b, result.worker_c = backfill_worker_outputs(
            result.worker_b, result.worker_c
        )
        if host_c_seed:
            result.worker_c = merge_worker_c_result(host_c_seed, result.worker_c)
    except Exception:
        logger.exception("[PMO Multi-Agent] worker result backfill failed")

    try:
        from l3_node.pmo_worker_result_backfill import merge_worker_d_result

        if host_d_seed:
            result.worker_d = merge_worker_d_result(host_d_seed, raw_worker_d)
        else:
            result.worker_d = raw_worker_d
    except Exception:
        logger.exception("[PMO Multi-Agent] Worker D merge failed")
        result.worker_d = raw_worker_d

    if phase1.failed_count:
        result.errors.extend(
            f"Worker {it.index}({it.role}): {it.error[:120]}"
            for it in phase1.failed_items
        )

    result.audit_report = ""
    result.phase2_output = ""
    result.status = "partial" if phase1.failed_count else "completed"
    _status(
        f"阶段一 {phase1.ok_count}/{phase1.total} 完成"
        f"（A/B/C {abc_ok}/3 · D {sum(1 for it in phase1_d.items if it.ok)}/1）"
        f" · 跳过交叉审计 · 待阶段三排版发报"
    )
    return result


def build_publisher_user_message(workflow: PmoMultiAgentResult) -> str:
    return PMO_PUBLISHER_USER_TEMPLATE.format(
        demand_table_spec=PMO_DEMAND_TABLE_PUBLISHER_SPEC,
        layout_contract=PMO_WAR_REPORT_LAYOUT_CONTRACT,
        worker_a=_clip(workflow.worker_a, 6000),
        worker_b=_clip(workflow.worker_b, 12000),
        worker_c=_clip(workflow.worker_c, 12000),
        worker_d=_clip(workflow.worker_d, 8000),
    )


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _build_auditor_context(worker_a: str, worker_b: str, worker_c: str) -> str:
    """阶段二 Auditor 专用：带小节标题的结构化上下文（非文件路径）。"""
    sections = [
        (
            "## Worker A · 视图字典与字段映射（Step 1+2）",
            _clip(worker_a, 8000),
        ),
        (
            "## Worker B · 人员看板 SSOT + 需求辅表（B-S1 / B-4 / B-SUP）",
            _clip(worker_b, 18000),
        ),
        (
            "## Worker C · 近三周 Sprint · Epic 与子任务（C-1～C-3）",
            _clip(worker_c, 18000),
        ),
    ]
    parts = [
        "以下是阶段一 FanOut 采集的全部结构化数据（已内联于本消息，**不是文件路径**）。",
        "请仅基于下列 JSON 文本做 Step 6 交叉审计，**禁止** read_file / db_query。",
        "",
    ]
    for title, body in sections:
        parts.append(title)
        parts.append(body or "（本 Worker 无输出或失败）")
        parts.append("")
    return "\n".join(parts).strip()


def _audit_report_has_low_confidence(report: str) -> bool:
    """诊断书是否含数据不足/无法判定等低置信标记。"""
    if not (report or "").strip():
        return True
    markers = (
        "数据不足",
        "无法直接判定",
        "样本数据不完整",
        "无法判定",
        "缺乏",
        "数据不完整",
    )
    low = report.lower()
    return any(m.lower() in low for m in markers)


def _build_auditor_task(context: str) -> str:
    return (
        "【PMO 多 Agent · 阶段二 · 交叉审计】\n"
        "基于下方 **内联 JSON 数据**（非文件）执行 Step 6 跨视图矛盾检验，"
        "输出《项目风险诊断书》（Markdown）。\n"
        "⛔ 禁止调用任何工具；数据已在下方，直接分析并 Final Answer。\n\n"
        f"{context}"
    )
