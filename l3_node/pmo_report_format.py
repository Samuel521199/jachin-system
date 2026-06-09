"""
PMO 战报 §1.4 版式 SSOT（需求进度全览等 GFM 表列定义）。
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

PMO_WAR_REPORT_VISUAL_FIG1 = "fig1"
PMO_WAR_REPORT_VISUAL_COMPACT = "compact"

PMO_DEMAND_TABLE_HEADERS: tuple[str, ...] = (
    "优先级",
    "需求名称",
    "时间跨度",
    "参与人",
    "完成度",
    "状态",
)

PMO_DEMAND_TABLE_FORBIDDEN_HEADERS: tuple[str, ...] = (
    "风险说明",
    "进度条",
)

PMO_DEMAND_TABLE_GFM_TEMPLATE = (
    "| 优先级 | 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| **P0** | 游戏加载优化 | 06/01→06/03 | Ethan; Celine | ▓▓▓░░ 51% | "
    "🔵 开发/验收 · 技术开发（技术 2/5） |"
)

PMO_DEMAND_TABLE_ROW_SORT_SPEC = (
    "📊 数据行 **按优先级排序**：P0 → P1 → P2 → 其它；同档按需求名称。"
    "优先级 **独立列**（单元格 `**P0**`，勿写 `**【P0】**`），**禁止**写在需求名称前。"
)

# 飞书 native_table 列宽 %（SSOT；禁止 LLM / Agent 手写；修改须同步 SKILL §1.4.0c）
# 图1~5 战报展示：📊 **5 列**（【P0】并入「需求名称」首列，避免 6 列挤没「优先级」列）
PMO_DEMAND_TABLE_HEADERS_NATIVE: tuple[str, ...] = (
    "需求名称",
    "时间跨度",
    "参与人",
    "完成度",
    "状态",
)
PMO_DEMAND_TABLE_COLUMN_WIDTHS_NATIVE: tuple[str, ...] = (
    "28%",  # 【P0】+ 需求名（图2 首列可见）
    "12%",  # 06/01→06/03
    "14%",  # 参与人全名
    "20%",  # 10 格进度条 + %
    "26%",  # 泳道状态
)
# 六列 GFM（探针/LLM 草稿）；推送前 polish 折叠为 NATIVE 五列
PMO_DEMAND_TABLE_COLUMN_WIDTHS_PCT: tuple[str, ...] = (
    "12%",
    "20%",
    "14%",
    "15%",
    "18%",
    "21%",
)
PMO_PERSONNEL_TABLE_COLUMN_WIDTHS_PCT: tuple[str, ...] = (
    "20%",  # 人员 · 冻结首列 + 全名
    "52%",  # 负责需求 · lark_md 多行
    "28%",  # 状态预警
)

# 飞书 native_table 行高（📊/👥 均 low 单行；任务列用紧凑截断，禁止 <br> 撑高）
PMO_NATIVE_TABLE_ROW_HEIGHT = "low"
PMO_NATIVE_TABLE_ROW_HEIGHT_PERSONNEL = "low"
PMO_NATIVE_TABLE_PAGE_SIZE_DEMAND = 4
PMO_NATIVE_TABLE_PAGE_SIZE_PERSONNEL = 5
PMO_EPIC_NAME_CELL_MAX_LEN = 32
PMO_TIME_SPAN_CELL_MAX_LEN = 14
PMO_PARTICIPANTS_CELL_MAX_LEN = 42
PMO_WORKFLOW_STATUS_CELL_MAX_LEN = 36
PMO_PERSONNEL_TASKS_LIST_MAX = 99
PMO_PERSONNEL_TASK_LINE_MAX_LEN = 56
PMO_PERSONNEL_ALERT_CELL_MAX_LEN = 48
# 仅 legacy 紧凑模式（显式 compact_for_feishu=True）使用
PMO_PERSONNEL_TASKS_CELL_MAX_LEN = 120

PMO_WAR_REPORT_FIG_LAYOUT_SPEC = (
    "**图1~图5 飞书战报锚点（视觉 SSOT · 与产品截图一致）**\n"
    "1. **Executive Summary**：🎯 标题 + 当前 Sprint + 目标版本 K11 + 🟢/🟡 总体状况一句 + 本周期大需求/人员统计一行。\n"
    "2. **📊 需求进度全览**：区块下优先级图例一行；**native 表 5 列**（需求名称·时间·参与人·完成度·状态）；"
    "需求名称格=`【P0】`+纯名；完成度=10格条+%；状态=`🔵 阶段 · 步骤`（无职能括号长串）。\n"
    "3. **👥 人员任务矩阵**：3 列；节奏判定副标题；**freeze_first_column**；负责需求列 **全量**（`lark_md` + `<br>`/`\\n`），`row_height=low` 表内单行省略，**hover 展示全部**（飞书原生能力，禁止「等N项」）。\n"
    "4. **📦 版本映射**：1 行辅表统计即可。\n"
    "5. **禁止**：表头/单元格出现 `...` 省略、横向挤没列、👥「等N项」、手写列宽。\n"
)

PMO_NATIVE_TABLE_LAYOUT_SPEC = (
    "**飞书 native_table 尺寸（强制 · 代码常量 SSOT，禁止 Agent 猜列宽）**\n"
    + PMO_WAR_REPORT_FIG_LAYOUT_SPEC
    + f"\n- `row_height`：📊 **`{PMO_NATIVE_TABLE_ROW_HEIGHT}`** · 👥 **`{PMO_NATIVE_TABLE_ROW_HEIGHT_PERSONNEL}`**（禁止 `auto`）\n"
    f"- `page_size`：📊 **{PMO_NATIVE_TABLE_PAGE_SIZE_DEMAND}** 行/页 · 👥 **{PMO_NATIVE_TABLE_PAGE_SIZE_PERSONNEL}** 行/页\n"
    f"- 列宽 %：📊 五列 {PMO_DEMAND_TABLE_COLUMN_WIDTHS_NATIVE} · 👥 {PMO_PERSONNEL_TABLE_COLUMN_WIDTHS_PCT}\n"
    f"- 📊：需求名≤{PMO_EPIC_NAME_CELL_MAX_LEN}字（含【P0】前缀）· 时间 `{PMO_TIME_SPAN_CELL_MAX_LEN}` · "
    f"参与人≤{PMO_PARTICIPANTS_CELL_MAX_LEN}字 · 完成度列 `lark_md` · 10格条\n"
    f"- 👥：`freeze_first_column=true`；负责需求 **全量** `lark_md`（每条任务一行；表内 `row_height=low` 裁剪，hover 看全）\n"
    "- 推送前 **必** `polish_pmo_war_report_markdown`（含六列→五列折叠）+ `compact_pmo_table_matrix_for_native_table`\n"
)

_ISO_DATE_IN_SPAN_RE = re.compile(
    r"20\d{2}[-/](\d{1,2})[-/](\d{1,2})",
)

PMO_WAR_REPORT_LAYOUT_CONTRACT = (
    "**战报版式契约（确定性 · 禁止开盲盒）**\n"
    "1. 📊 **飞书展示（图1~5）**：`PMO_DEMAND_TABLE_HEADERS_NATIVE` **五列**；"
    "【P0】写在「需求名称」格首（`format_demand_table_gfm_row_native`）；"
    "六列 GFM 草稿由 `collapse_demand_table_to_native_fig_layout` 推送前自动折叠。\n"
    "2. 📊 数据：Worker C `epics[]` + `format_demand_table_gfm_row_native` / `sort_epics_for_demand_table`。\n"
    "3. 👥 行序：🚨 延期 → 🚨 进度落后 → 🟡 偏闲 → ⚠️ 数据不足 → ✅ 正常；"
    "任务列 `format_personnel_matrix_tasks_cell(compact_for_feishu=False)` 全量 + `row_height=low`（表内一行，hover 多行）。\n"
    "4. 推送：`native_table_card: true`；宿主/MCP 推送前 **必** `polish_pmo_war_report_markdown`。\n"
    "5. **禁止**手写列宽/行高、禁止 `row_height:auto`、禁止把三表放在 ``` 代码围栏内。\n"
    "6. 宏观看板确定性路径：`scripts/push_pmo_macro_dashboard_lark.py` 或 FanOut 后 Publisher 按上规则组装。\n"
    + PMO_NATIVE_TABLE_LAYOUT_SPEC
)

PMO_PMO_TABLE_BOLD_SPEC = (
    "战报 GFM 表：**仅** 区块标题、表头行（`| --- |` 上一行）、**数据行第一列** 可用 `**`；"
    "其余数据格禁止加粗（含需求名、参与人、完成度、状态、任务明细）。"
)

_PRIORITY_PREFIX_RE = re.compile(r"^【\s*P\s*(\d+)\s*】\s*", re.I)
_PRIORITY_PLAIN_RE = re.compile(r"^P\s*(\d+)\s*$", re.I)

PMO_DEMAND_TABLE_STATUS_FORBIDDEN = (
    "待开始",
    "进行中",
    "已完成",
    "未开始",
    "完成",
)

PMO_DEMAND_TABLE_STATUS_SPEC = (
    "**状态列（强制 · 泳道流程 SSOT）**：须按 `docs/pmo_bmo_plugin/项目开发全流程说明.md` §1 "
    "推断当前 **阶段 · 步骤**，格式 `{emoji} {阶段} · {步骤}（可选各职能 n/m）`。\n"
    "  阶段示例：立项/评审、开发/验收、上线发布；步骤示例：需求评审、美术开发、技术开发、"
    "环境部署、技术自测验收、产品验收、班车发布、总结复盘。\n"
    "  **禁止**仅写「待开始 / 进行中 / 已完成」或只抄 Progress 原文。\n"
    "  Worker C `epics[].workflow_status` 与 `l3_node/pmo_workflow_stage.infer_epic_workflow_status` 为代码 SSOT；"
    "Publisher **必须**抄写 `workflow_status`，禁止自行改成「需求评审」等立项文案。\n"
    "  推断须排除部门占位行（前端开发/开发/美术等空 Progress）；**含**已闭环子任务；"
    "Progress=开发中且已交付 → 环境部署（提交测试环境）。案例：PMO_WORK_ZONG §3.6.4。"
)

PMO_DEMAND_TABLE_PUBLISHER_SPEC = (
    PMO_WAR_REPORT_LAYOUT_CONTRACT
    + "\n**📊 需求进度全览 — 飞书五列（图1~5）**\n"
    "```\n"
    "| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| 【P0】游戏加载优化 | 06/01→06/03 | Ethan; Celine | [▓▓▓▓▓░░░░░] 51% | 🔵 开发/验收 · 技术开发 |\n"
    "```\n"
    "- **需求名称**：`format_demand_epic_name_with_priority` → `【P0】` + 纯名（**禁止**单独优先级列上飞书）\n"
    "- **飞书列宽**：`PMO_DEMAND_TABLE_COLUMN_WIDTHS_NATIVE`\n"
    "- **需求名称**：`epics[].epic_name` 纯名（大需求，每行一个 Epic）\n"
    "- **时间跨度**：`Start Date`～`Expected Delivery Date`，或当前 Sprint 周期；示例 `05/18→05/25`\n"
    "- **参与人**：子任务 person 汇总（`epic_participants`）；分号分隔，**不加粗**\n"
    "- **完成度**：10 格进度条 + `%`（`workflow_completion_pct` / 泳道 rank，禁止条数占比）\n"
    + PMO_DEMAND_TABLE_STATUS_SPEC
    + "\n"
    + PMO_DEMAND_TABLE_ROW_SORT_SPEC
    + "\n"
    + PMO_PMO_TABLE_BOLD_SPEC
    + "\n"
    "- **禁止列**：风险说明、审计长文、单独「进度条」列；表内不写项目风险诊断书全文。\n"
    "  行序与单元格：`sort_epics_for_demand_table` / `format_demand_table_gfm_row`。\n"
)


def pmo_demand_table_header_line(mc: str, section_keywords: tuple[str, ...]) -> str:
    """取 📊 区块后第一个含 | 且非 --- 分隔行的表头。"""
    if not mc:
        return ""
    start = -1
    for kw in section_keywords:
        i = mc.find(kw)
        if i >= 0:
            start = i if start < 0 else min(start, i)
    if start < 0:
        return ""
    for line in mc[start : start + 1200].splitlines():
        s = line.strip()
        if "|" in s and "---" not in s and s.count("|") >= 2:
            return s
    return ""


def pmo_demand_table_column_issues(
    mc: str,
    section_keywords: tuple[str, ...],
) -> list[str]:
    """📊 表头：接受图1~5 五列 native，或六列草稿（推送前会折叠）。"""
    header = pmo_demand_table_header_line(mc, section_keywords)
    if not header:
        return []
    issues: list[str] = []
    native_ok = all(col in header for col in PMO_DEMAND_TABLE_HEADERS_NATIVE)
    six_ok = all(col in header for col in PMO_DEMAND_TABLE_HEADERS)
    if not native_ok and not six_ok:
        for col in PMO_DEMAND_TABLE_HEADERS_NATIVE:
            if col not in header:
                issues.append(f"缺少列「{col}」")
    for col in PMO_DEMAND_TABLE_FORBIDDEN_HEADERS:
        if col in header:
            issues.append(f"禁止列「{col}」")
    return issues


def epic_priority_sort_key(epic: dict[str, Any]) -> tuple[int, str]:
    """P0 → P1 → P2 → 其它，同档按 epic_name。"""
    pr = str(epic.get("priority") or "").strip().upper()
    m = re.match(r"P(\d+)", pr)
    num = int(m.group(1)) if m else 99
    return (num, str(epic.get("epic_name") or "").lower())


def sort_epics_for_demand_table(epics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(epics, key=epic_priority_sort_key)


def split_priority_from_epic_name(name: str) -> tuple[str, str]:
    """从耦合在名称前的【P0】拆出 (priority_display, pure_name)。"""
    s = _strip_md_bold(name)
    m = _PRIORITY_PREFIX_RE.match(s)
    if m:
        return f"P{m.group(1)}", s[m.end() :].strip() or "—"
    return "", s


def get_pmo_war_report_visual_profile() -> str:
    """宏观看板推送默认 fig1（与产品截图一致）；Agent 紧凑版用 compact。"""
    return (os.environ.get("PMO_WAR_REPORT_VISUAL") or PMO_WAR_REPORT_VISUAL_FIG1).strip().lower()


def format_priority_cell(priority: Any) -> str:
    """📊 第一列：仅此列加粗（compact 卡片）。"""
    pr = str(priority or "").strip().upper()
    if not pr or pr in ("—", "-", "NULL"):
        return "—"
    m = _PRIORITY_PLAIN_RE.match(pr)
    if m:
        pr = f"P{m.group(1)}"
    if re.match(r"P\d+", pr):
        return f"**{pr}**"
    return f"**{pr}**"


def format_priority_cell_bracket(priority: Any) -> str:
    """📊 图1版式：优先级列 `【P0】`（无 Markdown 加粗）。"""
    pr = str(priority or "").strip().upper()
    if not pr or pr in ("—", "-", "NULL"):
        return "—"
    m = _PRIORITY_PLAIN_RE.match(pr)
    if m:
        pr = f"P{m.group(1)}"
    m2 = re.match(r"P(\d+)", pr)
    if m2:
        return f"【P{m2.group(1)}】"
    return pr[:8]


def format_workflow_status_cell(status: Any, *, max_len: int = PMO_WORKFLOW_STATUS_CELL_MAX_LEN) -> str:
    """📊 状态列：紧凑单行（去掉职能括号明细）。"""
    s = _strip_md_bold(str(status or "")).strip() or "—"
    if "（" in s:
        s = s.split("（", 1)[0].strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def format_compact_time_span_cell(span: Any) -> str:
    """`06/01→06/03` 单行，禁止 `2026/06/01→2026/06/07` 换行。"""
    raw = _strip_md_bold(str(span or "")).strip() or "—"
    if raw == "—":
        return raw
    s = raw.replace(" ", "").replace("～", "→").replace("~", "→").replace("-", "→")
    parts = re.split(r"→|—", s)
    out: list[str] = []
    for p in parts[:2]:
        p = p.strip()
        if not p:
            continue
        m = _ISO_DATE_IN_SPAN_RE.search(p)
        if m:
            mo, da = m.group(1).zfill(2), m.group(2).zfill(2)
            out.append(f"{mo}/{da}")
        elif re.match(r"\d{1,2}/\d{1,2}", p):
            out.append(p[: PMO_TIME_SPAN_CELL_MAX_LEN])
        else:
            out.append(p[:6])
    if len(out) >= 2:
        return f"{out[0]}→{out[1]}"[: PMO_TIME_SPAN_CELL_MAX_LEN]
    return (out[0] if out else raw)[: PMO_TIME_SPAN_CELL_MAX_LEN]


def format_compact_epic_name_cell(name: Any) -> str:
    s = _strip_md_bold(str(name or "")).strip() or "—"
    if len(s) <= PMO_EPIC_NAME_CELL_MAX_LEN:
        return s
    return s[: PMO_EPIC_NAME_CELL_MAX_LEN - 1] + "…"


def format_compact_participants_cell(label: Any) -> str:
    if not label or label == "—":
        return "—"
    names = [n.strip() for n in re.split(r"[;；]", _strip_md_bold(str(label))) if n.strip()]
    if not names:
        return "—"
    head = "; ".join(names)
    if len(head) > PMO_PARTICIPANTS_CELL_MAX_LEN:
        return head[: PMO_PARTICIPANTS_CELL_MAX_LEN - 1] + "…"
    return head


def format_fig1_completion_cell(progress: Any) -> str:
    """图1：10 格进度条 + %（`format_workflow_progress_bar`）。"""
    from l3_node.pmo_workflow_stage import format_workflow_progress_bar

    s = _strip_md_bold(str(progress or "")).strip() or "—"
    m = re.search(r"(\d+)\s*%", s)
    if m:
        return format_workflow_progress_bar(int(m.group(1)))
    return s[:20] if s != "—" else "—"


def format_compact_completion_cell(progress: Any) -> str:
    """5 格进度条 + % 同一行（飞书紧凑）。"""
    s = _strip_md_bold(str(progress or "")).strip() or "—"
    m = re.search(r"(\d+)\s*%", s)
    if not m:
        return s[:14] if s != "—" else "—"
    pct = max(0, min(100, int(m.group(1))))
    filled = round(pct / 20)
    bar = f"{'▓' * filled}{'░' * (5 - filled)}"
    return f"{bar} {pct}%"


def format_priority_plain_cell(priority: Any) -> str:
    """飞书 data_type=text 用纯文本 P0（无 **）。"""
    return _strip_md_bold(format_priority_cell(priority)) or "—"


def _fig1_demand_row_cells(cells: list[str], header: list[str]) -> list[str]:
    """图1 native_table：【P0】+ 10 格条 + 单行时间/参与人。"""
    p_idx, n_idx, s_idx = _demand_table_column_indices(header)
    time_idx = next((i for i, c in enumerate(header) if "时间" in c), 2)
    part_idx = next((i for i, c in enumerate(header) if "参与" in c), 3)
    prog_idx = next((i for i, c in enumerate(header) if "完成" in c), 4)
    out = list(cells)
    while len(out) < len(header):
        out.append("—")
    embedded, pure = split_priority_from_epic_name(out[n_idx])
    pr = normalize_priority_token(out[p_idx]) or embedded
    out[p_idx] = format_priority_cell_bracket(pr or "—")
    out[n_idx] = format_compact_epic_name_cell(pure or _strip_md_bold(out[n_idx]))
    out[time_idx] = format_compact_time_span_cell(out[time_idx])
    out[part_idx] = format_compact_participants_cell(out[part_idx])
    out[prog_idx] = format_fig1_completion_cell(out[prog_idx])
    out[s_idx] = format_workflow_status_cell(out[s_idx], max_len=40)
    return out


def _compact_demand_row_cells(cells: list[str], header: list[str]) -> list[str]:
    p_idx, n_idx, s_idx = _demand_table_column_indices(header)
    time_idx = next((i for i, c in enumerate(header) if "时间" in c), 2)
    part_idx = next((i for i, c in enumerate(header) if "参与" in c), 3)
    prog_idx = next((i for i, c in enumerate(header) if "完成" in c), 4)
    out = list(cells)
    while len(out) < len(header):
        out.append("—")
    embedded, pure = split_priority_from_epic_name(out[n_idx])
    pr = normalize_priority_token(out[p_idx]) or embedded
    out[p_idx] = format_priority_plain_cell(pr or "—")
    out[n_idx] = format_compact_epic_name_cell(pure)
    out[time_idx] = format_compact_time_span_cell(out[time_idx])
    out[part_idx] = format_compact_participants_cell(out[part_idx])
    out[prog_idx] = format_compact_completion_cell(out[prog_idx])
    out[s_idx] = format_workflow_status_cell(out[s_idx])
    return out


def format_demand_epic_name_with_priority(priority: Any, epic_name: str) -> str:
    """图1~5 首列：`【P0】 需求名`（单列展示，避免优先级列被挤没）。"""
    _pri, pure_name = split_priority_from_epic_name(epic_name)
    pr = format_priority_cell_bracket(priority or _pri)
    name = format_compact_epic_name_cell(pure_name)
    if pr != "—":
        return f"{pr} {name}"
    return name


def format_demand_table_gfm_row_native(
    *,
    priority: Any,
    epic_name: str,
    time_span: str,
    participants: str,
    progress_bar: str,
    workflow_status: str,
) -> str:
    """图1~5 飞书 native 📊 行（5 列）。"""
    cells = [
        format_demand_epic_name_with_priority(priority, epic_name),
        format_compact_time_span_cell(time_span),
        format_compact_participants_cell(participants),
        format_fig1_completion_cell(progress_bar),
        format_workflow_status_cell(workflow_status, max_len=PMO_WORKFLOW_STATUS_CELL_MAX_LEN),
    ]
    return _gfm_row_from_cells(cells)


def format_demand_table_gfm_row_fig1(
    *,
    priority: Any,
    epic_name: str,
    time_span: str,
    participants: str,
    progress_bar: str,
    workflow_status: str,
) -> str:
    """兼容别名：宏观看板推送统一走五列 native。"""
    return format_demand_table_gfm_row_native(
        priority=priority,
        epic_name=epic_name,
        time_span=time_span,
        participants=participants,
        progress_bar=progress_bar,
        workflow_status=workflow_status,
    )


def _fig_native_demand_row_cells(cells: list[str], header: list[str]) -> list[str]:
    """五列 native：合并遗留六列或压紧五列单元格。"""
    joined = "".join(header)
    if "优先级" in joined and len(header) >= 6:
        p_idx, n_idx, _st_idx = _demand_table_column_indices(header)
        time_idx = next((i for i, c in enumerate(header) if "时间" in c), 2)
        part_idx = next((i for i, c in enumerate(header) if "参与" in c), 3)
        prog_idx = next((i for i, c in enumerate(header) if "完成" in c), 4)
        st_idx = next((i for i, c in enumerate(header) if "状态" in c), 5)
        out = list(cells)
        while len(out) < len(header):
            out.append("—")
        merged_name = format_demand_epic_name_with_priority(out[p_idx], out[n_idx])
        return [
            merged_name,
            format_compact_time_span_cell(out[time_idx]),
            format_compact_participants_cell(out[part_idx]),
            format_fig1_completion_cell(out[prog_idx]),
            format_workflow_status_cell(out[st_idx], max_len=PMO_WORKFLOW_STATUS_CELL_MAX_LEN),
        ]
    name_idx = next((i for i, c in enumerate(header) if "需求" in c), 0)
    time_idx = next((i for i, c in enumerate(header) if "时间" in c), 1)
    part_idx = next((i for i, c in enumerate(header) if "参与" in c), 2)
    prog_idx = next((i for i, c in enumerate(header) if "完成" in c), 3)
    st_idx = next((i for i, c in enumerate(header) if "状态" in c), 4)
    out = list(cells)
    while len(out) < len(header):
        out.append("—")
    embedded, pure = split_priority_from_epic_name(out[name_idx])
    out[name_idx] = format_demand_epic_name_with_priority(embedded or "—", pure or out[name_idx])
    out[time_idx] = format_compact_time_span_cell(out[time_idx])
    out[part_idx] = format_compact_participants_cell(out[part_idx])
    out[prog_idx] = format_fig1_completion_cell(out[prog_idx])
    out[st_idx] = format_workflow_status_cell(out[st_idx], max_len=PMO_WORKFLOW_STATUS_CELL_MAX_LEN)
    return out


def compact_pmo_table_matrix_for_native_table(matrix: list[list[str]]) -> list[list[str]]:
    """推送飞书前压紧 GFM 矩阵（表头不变）。"""
    if not matrix or len(matrix) < 2:
        return matrix
    header = matrix[0]
    profile = detect_pmo_native_table_profile(header)
    visual = get_pmo_war_report_visual_profile()
    if profile == "demand":
        ncol = len(header)
        if ncol == len(PMO_DEMAND_TABLE_HEADERS_NATIVE) or "优先级" not in "".join(header):
            row_fn = _fig_native_demand_row_cells
        elif visual == PMO_WAR_REPORT_VISUAL_FIG1:
            row_fn = _fig1_demand_row_cells
        else:
            row_fn = _compact_demand_row_cells
        return [header] + [row_fn(row, header) for row in matrix[1:]]
    if profile == "personnel":
        p_idx, task_idx, alert_idx = _personnel_table_column_indices(header)
        compacted: list[list[str]] = [header]
        for row in matrix[1:]:
            cells = list(row)
            while len(cells) < len(header):
                cells.append("—")
            if task_idx < len(cells):
                cells[task_idx] = normalize_personnel_task_cell_text(
                    cells[task_idx], preserve_multiline=True
                )
            if alert_idx < len(cells):
                a = _strip_md_bold(cells[alert_idx])
                if len(a) > PMO_PERSONNEL_ALERT_CELL_MAX_LEN:
                    cells[alert_idx] = a[: PMO_PERSONNEL_ALERT_CELL_MAX_LEN - 1] + "…"
            if p_idx < len(cells):
                pname = _strip_md_bold(cells[p_idx]) or "—"
                cells[p_idx] = pname
            compacted.append(cells)
        return compacted
    return matrix


def detect_pmo_native_table_profile(header_cells: list[str]) -> str | None:
    joined = "".join(header_cells)
    if "需求名称" in joined and ("完成度" in joined or "完成" in joined):
        return "demand"
    if "人员" in joined and (
        "状态预警" in joined or "负责需求" in joined or "任务" in joined
    ):
        return "personnel"
    return None


def pmo_native_table_column_widths(profile: str, ncol: int) -> list[str]:
    if profile == "demand" and ncol == len(PMO_DEMAND_TABLE_HEADERS_NATIVE):
        base = list(PMO_DEMAND_TABLE_COLUMN_WIDTHS_NATIVE)
    elif profile == "demand":
        base = list(PMO_DEMAND_TABLE_COLUMN_WIDTHS_PCT)
    elif profile == "personnel":
        base = list(PMO_PERSONNEL_TABLE_COLUMN_WIDTHS_PCT)
    else:
        base = []
    if not base:
        return ["auto"] * ncol
    if len(base) >= ncol:
        return list(base[:ncol])
    extra = ncol - len(base)
    tail = max(8, (100 - sum(int(w.rstrip("%")) for w in base)) // max(extra, 1))
    return list(base) + [f"{tail}%"] * extra


def format_epic_participants_plain(label: str) -> str:
    if not label or label == "—":
        return "—"
    names = [n.strip() for n in re.split(r"[;；]", label) if n.strip()]
    return "; ".join(names) if names else "—"


def format_demand_table_gfm_row(
    *,
    priority: Any,
    epic_name: str,
    time_span: str,
    participants: str,
    progress_bar: str,
    workflow_status: str,
) -> str:
    """组装 📊 表一行 GFM（优先级列加粗，其余列无 **）。"""
    _pri, pure_name = split_priority_from_epic_name(epic_name)
    cells = [
        format_priority_cell(priority or _pri),
        format_compact_epic_name_cell(pure_name),
        format_compact_time_span_cell(time_span),
        format_compact_participants_cell(participants),
        format_compact_completion_cell(progress_bar),
        format_workflow_status_cell(workflow_status),
    ]
    return _gfm_row_from_cells(cells)


_DEMAND_SECTION_KEYWORDS = ("需求进度全览", "📊")


def _demand_table_column_indices(header: list[str]) -> tuple[int, int, int]:
    priority_idx = 0
    name_idx = 1
    status_idx = len(header) - 1
    for ci, col in enumerate(header):
        c = col.strip()
        if "优先级" in c:
            priority_idx = ci
        if "需求名称" in c or c == "需求":
            name_idx = ci
        if c == "状态" or "泳道" in c:
            status_idx = ci
    return priority_idx, name_idx, status_idx


def _demand_header_is_six_column(header: list[str]) -> bool:
    joined = "".join(header)
    return "优先级" in joined and "需求名称" in joined


def _upgrade_demand_header_and_rows(
    header: list[str], data_lines: list[str]
) -> tuple[list[str], list[str]]:
    """5 列旧版（无优先级列）→ 6 列：从需求名称拆出 P0 到首列。"""
    if _demand_header_is_six_column(header):
        return header, data_lines
    if not any("需求名称" in c for c in header):
        return header, data_lines
    name_idx = next(
        (i for i, c in enumerate(header) if "需求名称" in c or c.strip() == "需求"),
        0,
    )
    new_header = list(PMO_DEMAND_TABLE_HEADERS)
    upgraded: list[str] = []
    for ln in data_lines:
        cells = _split_gfm_row(ln)
        while len(cells) < len(header):
            cells.append("—")
        embedded, pure = split_priority_from_epic_name(cells[name_idx])
        pr_cell = format_priority_cell(embedded or "—")
        rest = [cells[i] for i in range(len(header)) if i != name_idx]
        new_cells = [pr_cell, pure] + rest
        upgraded.append(
            _polish_demand_data_row(_gfm_row_from_cells(new_cells), header=new_header)
        )
    return new_header, upgraded


def normalize_priority_token(raw: str) -> str:
    """【P0】/P0/— → 标准 P0 或空。"""
    s = _strip_md_bold(raw)
    if not s or s in ("—", "-"):
        return ""
    embedded, _ = split_priority_from_epic_name(s)
    if embedded:
        return embedded.upper()
    m = _PRIORITY_PLAIN_RE.match(s.upper())
    if m:
        return f"P{m.group(1)}"
    m2 = re.search(r"P\s*(\d+)", s.upper())
    if m2:
        return f"P{m2.group(1)}"
    return ""


def _priority_sort_key_from_cell(priority_cell: str, name_cell: str) -> tuple[int, str]:
    pr_raw = normalize_priority_token(priority_cell)
    embedded, pure = split_priority_from_epic_name(name_cell)
    pr = pr_raw or embedded
    m = re.match(r"P(\d+)", pr.upper()) if pr else None
    num = int(m.group(1)) if m else 99
    return (num, pure.lower())


def _polish_demand_data_row(row_line: str, *, header: list[str]) -> str:
    cells = _split_gfm_row(row_line)
    while len(cells) < len(header):
        cells.append("—")
    row_fn = (
        _fig1_demand_row_cells
        if get_pmo_war_report_visual_profile() == PMO_WAR_REPORT_VISUAL_FIG1
        else _compact_demand_row_cells
    )
    return _gfm_row_from_cells(row_fn(cells, header))


def polish_demand_table_in_markdown(mc: str, *, sort_rows: bool = True) -> str:
    """📊 表：优先级独立列、P0→P1→P2 排序、仅首列加粗。"""
    if not mc or "|" not in mc:
        return mc
    start = -1
    for kw in _DEMAND_SECTION_KEYWORDS:
        i = mc.find(kw)
        if i >= 0:
            start = i if start < 0 else min(start, i)
    if start < 0:
        return mc
    lines = mc[start:].splitlines()
    prefix = mc[:start]
    out_body: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" not in line or _is_gfm_separator(line):
            out_body.append(line)
            i += 1
            continue
        header = _split_gfm_row(line)
        if "需求名称" not in "".join(header) and "优先级" not in "".join(header):
            out_body.append(line)
            i += 1
            continue
        i += 1
        if i < len(lines) and _is_gfm_separator(lines[i]):
            i += 1
        data_lines: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip() or "|" not in nxt:
                break
            if nxt.strip().startswith("###") or "👥" in nxt or "人员任务" in nxt:
                break
            if _is_gfm_separator(nxt):
                break
            data_lines.append(nxt)
            i += 1
        if not data_lines:
            out_body.append(line)
            continue
        header, data_lines = _upgrade_demand_header_and_rows(header, data_lines)
        sep = "| " + " | ".join(["---"] * len(header)) + " |"
        block = [_gfm_row_from_cells(header), sep]
        priority_idx, name_idx, _status_idx = _demand_table_column_indices(header)
        data_lines = [_polish_demand_data_row(ln, header=header) for ln in data_lines]
        if sort_rows:

            def _row_pri_key(ln: str) -> tuple[int, str]:
                cells = _split_gfm_row(ln)
                pc = cells[priority_idx] if priority_idx < len(cells) else ""
                nc = cells[name_idx] if name_idx < len(cells) else ""
                return _priority_sort_key_from_cell(pc, nc)

            data_lines.sort(key=_row_pri_key)
        out_body.extend(block)
        out_body.extend(data_lines)
        out_body.extend(lines[i:])
        return prefix + "\n".join(out_body)
    return mc


def collapse_demand_table_to_native_fig_layout(mc: str) -> str:
    """六列 📊 GFM → 图1~5 五列（推送飞书前必过）。"""
    if not mc or "|" not in mc:
        return mc
    start = -1
    for kw in _DEMAND_SECTION_KEYWORDS:
        i = mc.find(kw)
        if i >= 0:
            start = i if start < 0 else min(start, i)
    if start < 0:
        return mc
    lines = mc[start:].splitlines()
    prefix = mc[:start]
    out_body: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" not in line or _is_gfm_separator(line):
            out_body.append(line)
            i += 1
            continue
        header = _split_gfm_row(line)
        if len(header) < 2:
            out_body.append(line)
            i += 1
            continue
        block: list[str] = [line]
        i += 1
        if i < len(lines) and _is_gfm_separator(lines[i]):
            block.append(lines[i])
            i += 1
        data_lines: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                break
            if "|" not in nxt:
                break
            if nxt.strip().startswith("###") or "👥" in nxt:
                break
            if _is_gfm_separator(nxt):
                break
            data_lines.append(nxt)
            i += 1
        joined_h = "".join(header)
        if "需求" in joined_h and len(header) in (
            len(PMO_DEMAND_TABLE_HEADERS),
            len(PMO_DEMAND_TABLE_HEADERS_NATIVE),
        ):
            new_header = list(PMO_DEMAND_TABLE_HEADERS_NATIVE)
            new_rows = [
                _gfm_row_from_cells(_fig_native_demand_row_cells(_split_gfm_row(ln), header))
                for ln in data_lines
            ]
            out_body.append(_gfm_row_from_cells(new_header))
            out_body.append("| " + " | ".join("---" for _ in new_header) + " |")
            out_body.extend(new_rows)
        else:
            out_body.extend(block)
            out_body.extend(data_lines)
        out_body.extend(lines[i:])
        return prefix + "\n".join(out_body)
    return mc


_PMO_WAR_REPORT_DEV_FOOTER_RE = re.compile(
    r"^\s*📋\s*本次数据[^\n]*\n?",
    re.MULTILINE,
)


def strip_pmo_war_report_dev_footer(mc: str) -> str:
    """去掉战报底部「本次数据 / 宿主预取」等开发说明（禁止出现在飞书卡片）。"""
    if not mc:
        return mc
    out = _PMO_WAR_REPORT_DEV_FOOTER_RE.sub("", mc)
    return out.rstrip() + ("\n" if out.strip() else "")


def polish_pmo_war_report_markdown(mc: str) -> str:
    """战报三表：📊 五列 native + 👥 全量多行 + 排序 + 行序。"""
    mc = polish_demand_table_in_markdown(mc)
    mc = collapse_demand_table_to_native_fig_layout(mc)
    mc = polish_personnel_matrix_in_markdown(
        mc,
        sort_rows=True,
        normalize_tasks=True,
    )
    return strip_pmo_war_report_dev_footer(mc)


# 👥 人员任务矩阵行序（战报 GFM / 飞书 native_table 分页须一致；禁止按姓名字母序）
PMO_PERSONNEL_ALERT_SORT_RANK: dict[str, int] = {
    "overdue": 0,      # 🚨 延期（最前）
    "behind": 1,       # 🚨 进度落后
    "idle": 2,         # 🟡 偏闲
    "insufficient": 3,  # ⚠️ 数据不足
    "normal": 4,       # ✅ 正常（最后）
    "unknown": 5,
}


def classify_personnel_alert(alert_text: str) -> str:
    a = str(alert_text or "")
    if "延期" in a:
        return "overdue"
    if "进度落后" in a:
        return "behind"
    if "偏闲" in a:
        return "idle"
    if "数据不足" in a:
        return "insufficient"
    if "正常" in a or "✅" in a:
        return "normal"
    return "unknown"


def personnel_matrix_sort_key(*, person: str, alert_text: str) -> tuple[int, str]:
    bucket = classify_personnel_alert(alert_text)
    return (PMO_PERSONNEL_ALERT_SORT_RANK.get(bucket, 99), (person or "").lower())


PMO_PERSONNEL_MATRIX_ROW_SORT_SPEC = (
    "👥 人员任务矩阵 **数据行排序**（强制，禁止按姓名字母序）："
    "🚨 延期 → 🚨 进度落后 → 🟡 偏闲 → ⚠️ 数据不足 → ✅ 正常（最后）；"
    "同档内按姓名 tie-break。宿主推送前会 `polish_personnel_matrix_in_markdown` 校正。"
    "飞书列宽：`pmo_native_table_column_widths('personnel')`。"
)

PMO_PERSONNEL_TASK_CELL_FORMAT_SPEC = (
    "👥 **负责需求（含优先级）**列：飞书战报 **全量罗列**，每条 `【P0】任务名 · 状态` **独占一行**（单元格内 `<br>`）；"
    "**禁止**「等N项」省略、禁止 ` · ` 挤成单行、禁止 `**`。"
    "代码 SSOT：`format_personnel_matrix_tasks_cell(compact_for_feishu=False)`。"
)

_PERSONNEL_TASK_COL_NAMES = ("负责需求", "任务", "需求明细", "任务明细")


def _strip_md_bold(text: str) -> str:
    return re.sub(r"\*+", "", str(text or "")).strip()


def _dash_cell(v: Any) -> str:
    if v is None or v == "" or v == "null":
        return "—"
    s = str(v).strip()
    return s if s else "—"


def format_personnel_matrix_tasks_cell(
    tasks: list[dict[str, Any]],
    *,
    limit: int | None = None,
    name_max_len: int = PMO_PERSONNEL_TASK_LINE_MAX_LEN,
    status_max_len: int = 18,
    compact_for_feishu: bool = False,
) -> str:
    """
    👥 负责需求列：默认 **全量 + `<br>` 分行**（飞书 native_table + `row_height=low`：
    表内单行省略，hover 展示全部任务，每条一行）。
    仅显式 `compact_for_feishu=True` 时退回「等N项」紧凑单行（**禁止**用于战报推送）。
    """
    cap = limit if limit is not None else (
        2 if compact_for_feishu else PMO_PERSONNEL_TASKS_LIST_MAX
    )
    lines: list[str] = []
    for t in tasks[:cap]:
        pr = _dash_cell(t.get("priority"))
        name = _strip_md_bold(str(t.get("task") or "—"))
        if len(name) > name_max_len:
            name = name[: name_max_len - 1] + "…"
        st = _strip_md_bold(
            str(
                t.get("status")
                or t.get("status_text")
                or t.get("progress")
                or "—"
            )
        )
        if len(st) > status_max_len:
            st = st[: status_max_len - 1] + "…"
        prefix = f"【{pr}】" if pr != "—" else ""
        lines.append(f"{prefix}{name} · {st}" if prefix else f"{name} · {st}")
    if compact_for_feishu:
        return format_personnel_tasks_lines_compact(lines, total_count=len(tasks))
    return "<br>".join(lines) if lines else "—"


def format_personnel_tasks_lines_compact(
    lines: list[str], *, total_count: int | None = None
) -> str:
    """飞书 native_table：单行，仅展示首条任务（其余用「等N项」）。"""
    if not lines:
        return "—"
    body = lines[0]
    n = total_count if total_count is not None else len(lines)
    if n > 1:
        body = f"{body} · 等{n}项"
    if len(body) > PMO_PERSONNEL_TASKS_CELL_MAX_LEN:
        return body[: PMO_PERSONNEL_TASKS_CELL_MAX_LEN - 1] + "…"
    return body


def format_personnel_matrix_tasks_cell_compact(cell: str) -> str:
    """将已有 GFM 任务列压成紧凑单行。"""
    raw = _strip_md_bold(str(cell or "").strip())
    if not raw or raw == "—":
        return "—"
    raw = re.sub(r"<br\s*/?>", " · ", raw, flags=re.I)
    parts = [p.strip() for p in re.split(r"\s*·\s*", raw) if p.strip()]
    if not parts:
        return "—"
    return format_personnel_tasks_lines_compact(parts, total_count=len(parts))


def normalize_personnel_task_cell_text(
    cell: str, *, preserve_multiline: bool = True
) -> str:
    """校正 LLM/旧版：去掉 **；展开为全量 `<br>` 分行（配合 row_height=low + hover）。"""
    raw = _strip_md_bold(str(cell or "").strip())
    if not raw or raw == "—":
        return "—"
    if not preserve_multiline:
        return format_personnel_matrix_tasks_cell_compact(raw)

    lines: list[str] = []
    if re.search(r"<br\s*/?>", raw, flags=re.I):
        parts = re.split(r"<br\s*/?>", raw, flags=re.I)
        lines = [p.strip() for p in parts if p.strip()]
    else:
        chunks = re.split(r"[;；]\s*", raw)
        for ch in chunks:
            ch = ch.strip()
            if not ch or re.search(r"等\s*\d+\s*项", ch):
                continue
            for piece in re.split(r"\s*·\s*", ch):
                piece = piece.strip()
                if piece and not re.match(r"^等\d+项$", piece):
                    lines.append(piece)
    lines = [ln for ln in lines if ln and not re.match(r"^等\d+项$", ln)]
    if not lines:
        return "—"
    return "<br>".join(lines)


def _gfm_row_from_cells(cells: list[str]) -> str:
    return "| " + " | ".join(str(c or "—").strip() or "—" for c in cells) + " |"


def is_terminal_personnel_task(t: dict[str, Any]) -> bool:
    st = str(t.get("status") or t.get("status_text") or "").strip()
    prog = str(t.get("progress") or "").strip()
    if t.get("actual_delivery_date_iso") or t.get("actual_delivery_date"):
        return True
    if "🟢" in st or "提前" in st:
        return True
    if any(x in prog for x in ("完成", "上线", "发布", "验收")):
        return True
    if any(x in st for x in ("完成", "上线")):
        return True
    if "提交测试" in prog or "测试通过" in prog:
        return True
    return False


def build_person_rhythm_alert(
    tasks: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> str:
    """与宏观看板脚本一致的节奏预警文案（用于排序与 👥 第三列）。"""
    if not tasks:
        return "⚠️ 数据不足，无法节奏判定"
    if today is None:
        from l3_node.tools.pmo_dates import pmo_today_date

        today = pmo_today_date()
    total = len(tasks)
    done = sum(1 for t in tasks if is_terminal_personnel_task(t))
    pct = round(100 * done / total) if total else 0
    sprint_start = None
    for t in tasks:
        sd = str(t.get("start_date_iso") or "")[:10]
        if sd:
            try:
                sprint_start = date.fromisoformat(sd)
                break
            except ValueError:
                pass
    if sprint_start is None:
        sprint_start = today
    days_elapsed = max(1, (today - sprint_start).days + 1)
    time_pct = min(100, round(100 * days_elapsed / 7))
    if done == total and total > 0:
        return f"🟡 偏闲（本周计划 {total}/完成 {done}，进度超前）"
    overdue = sum(
        1
        for t in tasks
        if t.get("expected_delivery_date_iso")
        and str(t.get("expected_delivery_date_iso"))[:10] < today.isoformat()
        and not is_terminal_personnel_task(t)
    )
    if overdue:
        return f"🚨 延期 {overdue} 项（本周计划 {total}/完成 {done}）"
    if pct < 30 and time_pct >= 50:
        return f"🚨 进度落后（时间已过约 {time_pct}%，完成 {pct}%）"
    return f"✅ 正常（本周计划 {total}/完成 {done}）"


def personnel_matrix_entries_sorted(
    by_person: dict[str, list[dict[str, Any]]],
    *,
    current_sprint: str,
    today: date | None = None,
) -> list[tuple[str, list[dict[str, Any]], str]]:
    entries: list[tuple[str, list[dict[str, Any]], str]] = []
    for person, ptasks in by_person.items():
        week = [
            t
            for t in ptasks
            if t.get("is_current_week") or t.get("sprint") == current_sprint
        ]
        if not week:
            continue
        alert = build_person_rhythm_alert(week, today=today)
        entries.append((person, week, alert))
    entries.sort(
        key=lambda item: personnel_matrix_sort_key(
            person=item[0], alert_text=item[2]
        )
    )
    return entries


_PERSONNEL_SECTION_KEYWORDS = ("人员任务矩阵", "人员预警矩阵", "👥")
_PERSONNEL_ALERT_COL_NAMES = ("状态预警", "预警", "状态", "节奏")


def _split_gfm_row(line: str) -> list[str]:
    s = line.strip()
    if not s.startswith("|"):
        return []
    inner = s.strip("|")
    return [c.strip() for c in inner.split("|")]


def _is_gfm_separator(line: str) -> bool:
    return bool(re.match(r"^\|\s*[-:]+\s*\|", line.strip()))


def _personnel_table_column_indices(header: list[str]) -> tuple[int, int, int]:
    person_idx = 0
    task_idx = 1
    alert_idx = len(header) - 1
    for ci, col in enumerate(header):
        if any(k in col for k in _PERSONNEL_ALERT_COL_NAMES):
            alert_idx = ci
        if "人员" in col or col.strip() in ("姓名", "执行人"):
            person_idx = ci
        if any(k in col for k in _PERSONNEL_TASK_COL_NAMES):
            task_idx = ci
    return person_idx, task_idx, alert_idx


def _polish_personnel_data_row(
    row_line: str,
    *,
    person_idx: int,
    task_idx: int,
) -> str:
    cells = _split_gfm_row(row_line)
    if task_idx < len(cells):
        cells[task_idx] = normalize_personnel_task_cell_text(cells[task_idx])
    if person_idx < len(cells):
        pname = _strip_md_bold(cells[person_idx]) or "—"
        cells[person_idx] = f"**{pname}**" if pname != "—" else "—"
    return _gfm_row_from_cells(cells)


def reorder_personnel_matrix_in_markdown(mc: str) -> str:
    """按预警严重度重排 👥 区块内首张 GFM 表的数据行（LLM 字母序兜底）。"""
    return polish_personnel_matrix_in_markdown(mc, sort_rows=True, normalize_tasks=True)


def polish_personnel_matrix_in_markdown(
    mc: str,
    *,
    sort_rows: bool = True,
    normalize_tasks: bool = True,
) -> str:
    """重排 👥 行序 + 规范化「负责需求」列为单行紧凑（去 **、<br>）。"""
    if not mc or "|" not in mc:
        return mc
    start = -1
    for kw in _PERSONNEL_SECTION_KEYWORDS:
        i = mc.find(kw)
        if i >= 0:
            start = i if start < 0 else min(start, i)
    if start < 0:
        return mc
    lines = mc[start:].splitlines()
    prefix = mc[:start]
    out_body: list[str] = []
    i = 0
    reordered = False
    while i < len(lines):
        line = lines[i]
        if "|" not in line or _is_gfm_separator(line):
            out_body.append(line)
            i += 1
            continue
        header = _split_gfm_row(line)
        if len(header) < 2:
            out_body.append(line)
            i += 1
            continue
        block: list[str] = [line]
        i += 1
        if i < len(lines) and _is_gfm_separator(lines[i]):
            block.append(lines[i])
            i += 1
        data_lines: list[str] = []
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                break
            if "|" not in nxt:
                break
            if nxt.strip().startswith("###") or nxt.strip().startswith("**📦"):
                break
            if _is_gfm_separator(nxt):
                break
            data_lines.append(nxt)
            i += 1
        if not data_lines:
            out_body.extend(block)
            continue
        person_idx, task_idx, alert_idx = _personnel_table_column_indices(header)

        if normalize_tasks:
            data_lines = [
                _polish_personnel_data_row(
                    ln, person_idx=person_idx, task_idx=task_idx
                )
                for ln in data_lines
            ]

        if sort_rows:

            def _row_sort_key(row_line: str) -> tuple[int, str]:
                cells = _split_gfm_row(row_line)
                person = cells[person_idx] if person_idx < len(cells) else ""
                alert = cells[alert_idx] if alert_idx < len(cells) else ""
                person_plain = _strip_md_bold(person)
                return personnel_matrix_sort_key(
                    person=person_plain, alert_text=alert
                )

            data_lines.sort(key=_row_sort_key)

        out_body.extend(block)
        out_body.extend(data_lines)
        out_body.extend(lines[i:])
        return prefix + "\n".join(out_body)
    return mc
