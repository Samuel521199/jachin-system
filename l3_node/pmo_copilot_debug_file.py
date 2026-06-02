"""
PMO-Copilot 人类可读调试日志（按轮次摘要 · v7）。

启用：环境变量 ``JACHIN_PMO_COPILOT_DEBUG_LOG`` = 绝对路径 ``.txt``（``run_pmo_copilot_skill.py`` 自动设置）。

每完成一轮工具调用（Action + Observation）追加一段摘要：目的 / 想法 / 工具 / 操作 / 结果 / 报错。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_pending_action: dict[str, tuple[str, str, str]] = {}
_session: dict[str, Any] = {}


def _default_pmo_max_iterations() -> int:
    from l3_node.agent_core import MAX_PMO_REACT_ITERATIONS

    return max(1, int(MAX_PMO_REACT_ITERATIONS))


def _session_max_iterations_cap() -> int:
    v = _session.get("max_iterations")
    if v is not None and int(v) > 0:
        return max(1, int(v))
    return _default_pmo_max_iterations()


def debug_log_path() -> str:
    return (os.environ.get("JACHIN_PMO_COPILOT_DEBUG_LOG") or "").strip()


def sync_pmo_debug_max_iterations(max_iterations: int) -> None:
    cap = max(1, int(max_iterations))
    old = int(_session.get("max_iterations") or 0)
    _session["max_iterations"] = cap
    fp = debug_log_path()
    if not fp or not Path(fp).is_file():
        return
    if old == cap:
        return
    try:
        text = Path(fp).read_text(encoding="utf-8")
        new_line = f"ReAct 上限: {cap} 轮"
        if re.search(r"^ReAct 上限:\s*\d+\s*轮\s*$", text, flags=re.MULTILINE):
            text = re.sub(
                r"^ReAct 上限:\s*\d+\s*轮\s*$",
                new_line,
                text,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            text = text.replace(f"ReAct 上限: {old} 轮", new_line, 1)
        Path(fp).write_text(text, encoding="utf-8")
    except OSError:
        pass


def init_pmo_debug_session(
    *,
    log_path: str | Path,
    user_message: str,
    correlation_id: str = "",
    max_iterations: int | None = None,
    mode_hint: str = "",
) -> None:
    """初始化日志文件头（覆盖旧内容）。"""
    cap = max(1, int(max_iterations)) if max_iterations is not None else _default_pmo_max_iterations()
    fp = str(Path(log_path).resolve())
    os.environ["JACHIN_PMO_COPILOT_DEBUG_LOG"] = fp
    _session.clear()
    _pending_action.clear()
    _session.update(
        {
            "max_iterations": cap,
            "correlation_id": correlation_id,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode_hint": (mode_hint or "").strip(),
        }
    )
    header = "\n".join(
        [
            "PMO-Copilot 运行日志（人类可读 · 按轮次摘要）",
            f"开始时间: {_session['started_at']}",
            f"任务 ID: {correlation_id or '—'}",
            f"ReAct 上限: {cap} 轮",
            f"日志文件: {fp}",
            "",
            "【架构速览 · v7】",
            "  INIT: atom_bi_project_context → pmo_mirror_import",
            "  分析: core:db_query（Probe → Locate → Drill）→ Thought → atom_lark_notifier",
            "",
            "【本次任务】",
            (user_message or "").strip() or "（无）",
            "",
            "—— 下方每轮：在做什么 / Agent 想法 / 工具与操作 / 发生了什么 / 有无问题 ——",
            "",
        ]
    )
    try:
        Path(fp).parent.mkdir(parents=True, exist_ok=True)
        Path(fp).write_text(header, encoding="utf-8")
    except OSError:
        pass


def append_pmo_debug_status(message: str) -> None:
    """追加网关/环境嗅探等状态行（非 ReAct 轮次）。"""
    if not debug_log_path():
        return
    raw = (message or "").strip()
    if not raw:
        return
    text = raw
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("status"):
                text = str(obj.get("status") or raw)
        except json.JSONDecodeError:
            pass
    _append_lines([f"⏳ {text}", ""])


def finalize_pmo_debug_log(final_answer: str = "", *, aborted: bool = False) -> None:
    fp = debug_log_path()
    if not fp:
        return
    lines = ["", "=" * 72, "【任务结束】"]
    if aborted:
        lines.append("状态: 中断或未正常完成")
    ans = (final_answer or "").strip()
    if ans:
        preview = ans if len(ans) <= 800 else ans[:800] + f"\n…（共 {len(ans)} 字，完整内容见控制台 Final Answer）"
        lines.extend(["", "最终回复摘要:", preview])
    else:
        lines.append("状态: 无 Final Answer")
    lines.append(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _append_lines(lines)


def append_pmo_debug_action(
    *, tool: str, inp: str, iteration: int, run_id: str, thought: str = ""
) -> None:
    if not debug_log_path():
        return
    key = f"{run_id}:{iteration}"
    _pending_action[key] = (str(tool or ""), str(inp or ""), str(thought or "").strip())


def append_pmo_debug_observation(*, tool: str, observation_full: str, iteration: int, run_id: str) -> None:
    if not debug_log_path():
        return
    key = f"{run_id}:{iteration}"
    pending = _pending_action.pop(key, None)
    if pending:
        act_tool, act_inp, act_thought = pending
    else:
        act_tool, act_inp, act_thought = str(tool or ""), "", ""
    block = _format_round_block(
        iteration=iteration,
        tool=act_tool or tool,
        action_input=act_inp,
        observation=str(observation_full or ""),
        thought=act_thought,
    )
    _append_lines(block)


def _append_lines(lines: list[str]) -> None:
    fp = debug_log_path()
    if not fp:
        return
    try:
        with open(fp, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _format_round_block(
    *, iteration: int, tool: str, action_input: str, observation: str, thought: str = ""
) -> list[str]:
    n = iteration + 1
    cap = _session_max_iterations_cap()
    t = (tool or "").strip()
    phase = _phase_label(t, action_input, observation)
    purpose, _idea_raw = _split_thought(thought, tool=t, action_input=action_input)
    human_purpose = _humanize_purpose(purpose, tool=t, action_input=action_input)
    human_idea = _humanize_agent_idea(thought, tool=t, action_input=action_input)

    lines = [
        "=" * 72,
        f"【第 {n} / {cap} 轮】{phase}",
        "-" * 72,
        "📌 这一步在做什么",
        f"   {human_purpose}",
        "",
        "💭 Agent 想法",
        f"   {human_idea}",
        "",
        f"🔧 调用了: {t or '（未知工具）'}",
        "📋 具体操作",
    ]
    op_lines = _format_operation(t, action_input)
    lines.extend(f"   {ln}" if ln else "" for ln in op_lines)

    lines.append("")
    lines.append("📊 发生了什么")
    effect_lines, error_lines = _summarize_effect(t, action_input, observation)
    lines.extend(f"   {ln}" if ln else "" for ln in effect_lines)

    lines.append("")
    if error_lines:
        lines.append("❌ 问题说明（需处理）:")
        lines.extend(f"   · {e}" for e in error_lines)
    else:
        lines.append("✅ 本步无系统错误")
    lines.append("=" * 72)
    return lines


def _looks_like_gfm_table_line(text: str) -> bool:
    s = (text or "").strip()
    return s.startswith("|") and s.count("|") >= 2


def _looks_like_gfm_or_table_blob(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        return False
    if len(lines) == 1 and lines[0].startswith("|") and lines[0].count("|") >= 4:
        return True
    pipe_lines = sum(1 for ln in lines if _looks_like_gfm_table_line(ln))
    if pipe_lines >= 1 and pipe_lines >= max(1, len(lines) // 2):
        return True
    if "待填充" in t and pipe_lines >= 1:
        return True
    return False


def _extract_prose_from_thought(thought: str) -> str:
    """从 Thought 中提取可读 prose，跳过 GFM 表格行与草稿占位。"""
    kept: list[str] = []
    for ln in (thought or "").splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if _looks_like_gfm_table_line(stripped):
            continue
        if re.match(r"^\|[-:|\s]+\|$", stripped):
            continue
        if re.match(r"^[📊👥📦].*[（(]Step\d", stripped):
            continue
        if stripped in ("（待填充）", "待填充"):
            continue
        kept.append(stripped)
    return "\n".join(kept).strip()


def _summarize_gfm_draft_rows(thought: str) -> str:
    """把 Thought 里的 GFM 表草稿改写成人类可读摘要。"""
    rows: list[str] = []
    for ln in (thought or "").splitlines():
        stripped = ln.strip()
        if not _looks_like_gfm_table_line(stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        vid = cells[0]
        if re.match(r"vew[A-Za-z0-9]{6,}", vid):
            cnt = cells[1] if len(cells) > 1 else ""
            col_cell = cells[2] if len(cells) > 2 else ""
            col_n = len(re.findall(r'"[^"]+"', col_cell)) if col_cell.startswith("[") else 0
            hint = f"{vid}"
            if cnt:
                hint += f"（{cnt} 条）"
            if col_n:
                hint += f"，{col_n} 个字段"
            elif col_cell and len(col_cell) > 20:
                hint += "，含列名字段清单"
            rows.append(hint)
        elif any(k in stripped for k in ("需求", "人员", "Epic", "Version", "Sprint", "状态")):
            preview = _truncate(stripped, 72)
            rows.append(f"表行 {preview}")
    if not rows:
        pipe_n = sum(1 for ln in thought.splitlines() if _looks_like_gfm_table_line(ln.strip()))
        if pipe_n:
            return f"Thought 含 {pipe_n} 行 GFM 表草稿（列名/数据占位，已省略原文）"
        return ""
    summary = "表草稿：" + "；".join(rows[:6])
    if len(rows) > 6:
        summary += f" … 另有 {len(rows) - 6} 行"
    if "待填充" in thought:
        summary += "；⚠️ 仍有「待填充」占位"
    return summary


def _humanize_agent_idea(thought: str, *, tool: str = "", action_input: str = "") -> str:
    """Agent 想法：保留 prose，GFM 表草稿改为摘要，避免整段 JSON/管道符刷屏。"""
    t = (thought or "").strip()
    if t in ("（API function calling）", "API function calling"):
        t = ""
    if not t:
        inferred = infer_tool_purpose_from_input(tool, action_input)
        return inferred or "（模型未输出文本 Thought，已由宿主根据工具输入推断目的）"
    prose = _extract_prose_from_thought(t)
    draft_summary = _summarize_gfm_draft_rows(t) if _looks_like_gfm_or_table_blob(t) else ""
    if prose and draft_summary:
        return f"{prose}\n   {draft_summary}"
    if prose:
        if len(prose) <= 500:
            return prose
        return prose[:500].rstrip() + f"…（共 {len(prose)} 字）"
    if draft_summary:
        return draft_summary
    if _looks_like_gfm_or_table_blob(t):
        return _summarize_gfm_draft_rows(t) or "（Thought 主要为 GFM 表草稿，已省略管道符原文）"
    if len(t) <= 500:
        return t
    return t[:500].rstrip() + f"…（共 {len(t)} 字）"


def _humanize_purpose(purpose: str, *, tool: str, action_input: str) -> str:
    """把宿主推断的目的改写成非技术人员也能读懂的一句话。"""
    sql = _extract_sql(action_input)
    sl = (sql or "").lower()
    tb = (tool or "").replace("mcp:", "").lower()
    raw_purpose = (purpose or "").strip()

    # GFM 表行/草稿永远不能作为「这一步在做什么」
    if _looks_like_gfm_or_table_blob(raw_purpose) or raw_purpose.startswith("|"):
        raw_purpose = ""

    if "db_query" in tb:
        if "pmo_views_meta" in sl:
            return "查看项目里有哪些飞书视图、每个视图有多少条数据（Step1 数据地图）"
        if "limit 1" in sl and "fields" in sl:
            if "vewcz1ffji" in sl:
                return "抽 1 条人员看板记录，确认字段名长什么样（Step2 样本）"
            if "vewpi8lyyw" in sl:
                return "抽 1 条开发进度记录，确认字段名长什么样（Step2 样本）"
            return "抽 1 条样本，确认 JSON 字段名（Step2）"
        if "limit 2" in sl or "limit 5" in sl:
            if "vewcz1ffji" in sl and "vewpi8lyyw" in sl:
                return "查看开发主表与人员看板的样本记录，确认 JSON 字段结构（Step2 样本）"
        if "json_each" in sl and "vewcz1ffji" in sl:
            select_part = sl.split("from", 1)[0] if "from" in sl else sl
            if not any(k in select_part for k in ("requirement", "status", "sprint", "progress", "due")):
                return (
                    "查人员看板里有哪些执行人（⚠️ 当前 SQL 只查了人名，"
                    "还应带上任务名、状态、Sprint、截止日期才能做负荷分析）"
                )
            return "查人员看板：每人负责哪些任务、状态如何、Sprint 和截止日期（Step3 人员矩阵）"
        if "vewcz1ffji" in sl and "父记录" in sql and "[0].text" in sql and " is null" in sl:
            return (
                "❌ 错误操作：在人员看板上用「Epic 顶层」条件查需求——"
                "人员表任务都有父记录，这个条件会查不到任何数据；"
                "Epic 应去 vewpI8lyYw 查（Step4）"
            )
        if "vewpi8lyyw" in sl and "父记录" in sql and "[0].text" in sql:
            return "查开发主表里有哪些顶层 Epic 大需求（Step4）"
        if "group by" in sl and "sprint" in sl:
            return "统计各 Sprint 里有多少条任务（Step5）"
        if "group by" in sl and ("状态" in sql or "status" in sl):
            return "统计各状态（按时/延期/完成等）有多少条（Step5）"
        if "version goal" in sl or ("version" in sl and "vew8txmcsh" in sl):
            return "统计产品视图里 Version Goal 填了多少（Step7）"
        inferred = _infer_purpose_from_sql(sql)
        if inferred:
            return inferred
    if "lark_notifier" in tb:
        obj = _try_parse_json(action_input)
        md_len = 0
        if isinstance(obj, dict):
            md_len = len(str(obj.get("markdown_content") or ""))
        return f"把 PMO 战报发到飞书群（markdown 约 {md_len} 字）"
    if raw_purpose and raw_purpose != "（未捕获 Thought，见下方 SQL/操作）":
        if len(raw_purpose) <= 120 and not _looks_like_gfm_or_table_blob(raw_purpose):
            return raw_purpose
    inferred = infer_tool_purpose_from_input(tool, action_input)
    if inferred:
        return inferred
    return raw_purpose or "执行工具调用"


def infer_tool_purpose_from_input(tool: str, inp: str) -> str:
    """从工具 id + Action Input 推断人类可读目的（function calling 无 Thought 时兜底）。"""
    tb = (tool or "").replace("mcp:", "").strip().lower()
    if "db_query" in tb:
        return _infer_purpose_from_sql(_extract_sql(inp))
    if "mirror_import" in tb:
        return "镜像入库：解析 pmo_lark_pull/*.md → pmo_raw_records"
    if "bi_project_context" in tb:
        return "拉取 §1.1 飞书 Wiki 多维表落盘"
    if "lark_notifier" in tb:
        obj = _try_parse_json(inp)
        cid = str((obj or {}).get("chat_id") or "主群").strip() if isinstance(obj, dict) else "主群"
        return f"推送 §1.4 战报到群 {cid}"
    if "fs_read" in tb or tb == "read_file":
        return "读取本地 Markdown 样本"
    return ""


def _infer_purpose_from_sql(sql: str) -> str:
    sl = (sql or "").lower()
    if not sl.strip():
        return "执行 SQLite 只读查询"
    if "pmo_views_meta" in sl:
        if "record_count" in sl:
            return "Step1·地图：读取各视图 record_count 与 columns_json"
        return "Step1·地图：读取视图 columns_json（建议含 record_count）"
    if "pragma table_info" in sl:
        return "探表结构：确认 pmo_raw_records 列名（source_view 非 view_id）"
    if "group by" in sl or "count(*)" in sl:
        if "vewcz1ffji" in sl and ("en_name" in sl or "person" in sl or "participant" in sl):
            return "Step3·人员矩阵：vewCz1FFJi 按 en_name 统计任务数（SSOT）"
        if "sprint" in sl:
            return "Step5·Sprint 分布：按 Sprint 聚合"
        if "状态" in sql or "status" in sl:
            return "Step5·状态分布：按状态字段聚合"
        if "version goal" in sl or "version" in sl:
            return "Step7·版本 Goal 覆盖率统计"
        if "vewpi8lyyw" in sl and ("父记录" in sql or "[0].text" in sql):
            return "Step4·Epic 层级：顶层需求（父记录[0].text IS NULL）"
        if "vewcz1ffji" in sl or "负责人" in sql or "person in charge" in sl:
            return "人员任务聚合（须用 Person in charge/Participant + en_name）"
        return "聚合统计探针"
    if "limit 1" in sl and "fields" in sl:
        if "vewcz1ffji" in sl:
            return "Step2·样本：确认 vewCz1FFJi 的 JSON 字段名"
        if "vewpi8lyyw" in sl:
            return "Step2·样本：确认 vewpI8lyYw 的 JSON 字段名"
        return "Step2·样本：读取单行 fields 确认列名"
    if "vewpi8lyyw" in sl and ("requirement" in sl or "priority" in sl):
        return "Locate·开发表需求明细（vewpI8lyYw）"
    if "vew8txmcsh" in sl or "vewl9mofgd" in sl:
        return "Locate·产品视图交叉查询"
    if "fields like" in sl or "like '%" in sl:
        return "Step6·跨视图：按需求名/人名检索矛盾"
    if "source_view" in sl:
        return "按 source_view 过滤明细查询"
    return "执行 SQLite 只读查询"


def _split_thought(thought: str, *, tool: str = "", action_input: str = "") -> tuple[str, str]:
    t = (thought or "").strip()
    if t in ("（API function calling）", "API function calling"):
        t = ""
    if not t:
        inferred = infer_tool_purpose_from_input(tool, action_input)
        if inferred:
            return inferred, inferred
        return "（未捕获 Thought，见下方 SQL/操作）", "（模型未输出文本 Thought，已由宿主根据工具输入推断目的）"
    if _looks_like_gfm_or_table_blob(t):
        inferred = infer_tool_purpose_from_input(tool, action_input)
        return inferred or "（Thought 为表草稿，目的见 SQL）", t
    prose = _extract_prose_from_thought(t)
    body = prose if prose else t
    m = re.match(r"^(.+?[。！？!?])\s*(.+)$", body, re.DOTALL)
    if m and len(m.group(1)) >= 8 and not _looks_like_gfm_table_line(m.group(1)):
        return m.group(1).strip(), t
    if len(body) <= 80:
        return body, t
    first_line = body.split("\n", 1)[0].strip()
    if len(first_line) <= 100:
        return first_line, t
    return body[:80].rstrip() + "…", t


def _phase_label(tool: str, inp: str, observation: str) -> str:
    tb = tool.replace("mcp:", "").lower()
    if "mirror_import" in tb:
        return "INIT · 入库"
    if "bi_project_context" in tb:
        return "INIT · 拉表"
    if "lark_notifier" in tb:
        return "推送 · 战报"
    if "db_query" in tb:
        sql = _extract_sql(inp).lower()
        if "pmo_views_meta" in sql:
            return "Probe · 数据地图"
        if "group by" in sql or "count(*)" in sql or "count(*) as" in sql:
            if "sprint" in sql:
                return "Probe · 工作周期"
            return "Probe · 聚合统计"
        if "distinct" in sql:
            return "分析 · 查库"
        if re.search(r"select\s+id\b", sql) and "json_extract" in sql:
            return "Drill · 详情钻取"
        if "where" in sql and ("priority" in sql or "parent" in sql or "父" in sql):
            return "Locate · 精准定位"
        return "分析 · 查库"
    if "fs_read" in tb or tb == "read_file":
        return "读盘 · Markdown"
    if "web_scraper" in tb:
        return "辅轨 · 页面抓取"
    return "执行工具"


_SQL_DISPLAY_MAX = 800


def _format_operation(tool: str, inp: str) -> list[str]:
    tb = tool.replace("mcp:", "").lower()
    obj = _try_parse_json(inp)
    if "db_query" in tb:
        sql = _extract_sql(inp)
        if sql:
            return [_truncate(sql, _SQL_DISPLAY_MAX)]
        return ["（未解析到 SQL）"]
    if "mirror_import" in tb:
        mp = ""
        if isinstance(obj, dict):
            mp = str(obj.get("manifest_path") or obj.get("path") or "").strip()
        hint = f"manifest={Path(mp).name}" if mp else "默认 manifest"
        return [f"扫描 pmo_lark_pull/*.md → upsert pmo_raw_records + pmo_views_meta（{hint}）"]
    if "bi_project_context" in tb:
        urls = _extract_wiki_urls(obj, inp)
        if urls:
            return [f"共 {len(urls)} 个 Wiki 视图 URL（见本轮「已抓取」列表）"]
        od = ""
        if isinstance(obj, dict):
            od = str(obj.get("output_dir_relative") or obj.get("output_dir") or "").strip()
        return [od or "（使用 MCP 默认配置拉表）"]
    if "lark_notifier" in tb and isinstance(obj, dict):
        cid = str(obj.get("chat_id") or "默认主群").strip()
        title = str(obj.get("title") or "").strip()
        md = str(obj.get("markdown_content") or obj.get("markdown") or "")
        parts = [f"群 {cid}"]
        if title:
            parts.append(f"标题《{_truncate(title, 40)}》")
        if md:
            parts.append(f"战报 {len(md)} 字符")
        return [" · ".join(parts)]
    if "fs_read" in tb or tb == "read_file":
        p = ""
        if isinstance(obj, dict):
            p = str(obj.get("file_path") or obj.get("path") or "").strip()
        if not p and inp.strip():
            p = inp.strip()[:300]
        return [Path(p).name if p else "（未知文件）"]
    target = _action_target_summary(tool, inp)
    return [target] if target else ["（见 Action Input）"]


def _summarize_effect(tool: str, inp: str, observation: str) -> tuple[list[str], list[str]]:
    tb = tool.replace("mcp:", "").lower()
    obs = observation or ""
    errors: list[str] = []
    lines: list[str] = []

    if "db_query" in tb:
        lines.extend(_summarize_db_query_observation(inp, obs, errors))

    elif "mirror_import" in tb:
        data = _try_parse_json(obs)
        if isinstance(data, dict) and data.get("status") == "ok":
            total = int(data.get("total_records") or 0)
            views = data.get("views") if isinstance(data.get("views"), list) else []
            lines.append(f"结果: ✅ 镜像入库成功 — 共 {total:,} 条记录，{len(views)} 个视图")
            for v in views[:20]:
                if not isinstance(v, dict):
                    continue
                vid = str(v.get("view_id") or "")
                cnt = int(v.get("record_count") or 0)
                title = _title_from_view_dict(v) or vid
                lines.append(f"  · 「{title}」 ({vid}) → {cnt:,} 条")
        elif isinstance(data, dict) and data.get("status") == "error":
            errors.append(str(data.get("message") or data.get("error") or "镜像入库失败"))
            lines.append("结果: ❌ 镜像入库失败")
        else:
            lines.append(f"结果: {obs[:200]}")

    elif "bi_project_context" in tb:
        data = _try_parse_json(obs)
        if isinstance(data, dict):
            st = str(data.get("status") or "").lower()
            out_dir = str(data.get("output_dir") or data.get("msg") or "").strip()
            files = [f for f in (data.get("files") or []) if isinstance(f, str)]
            nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else []
            err_list = data.get("errors") if isinstance(data.get("errors"), list) else []

            if st == "error" or data.get("error"):
                errors.append(str(data.get("error") or data.get("msg") or "拉表失败"))
                lines.append("结果: ❌ 拉表失败")
            elif st == "success" or files:
                md_files = [f for f in files if f.lower().endswith(".md")]
                lines.append(f"结果: ✅ 成功 — 写入 {len(md_files) or len(files)} 个文件")
            else:
                lines.append("结果: ⚠️ 状态不明，请检查 Observation")

            if out_dir:
                lines.append(f"  落盘目录: {out_dir}")
            if files:
                lines.append("")
                lines.append("  已抓取的表（本地文件名 · 视图）:")
                idx = 0
                for fn in files:
                    if fn.endswith("MANIFEST.json") or "manifest" in fn.lower():
                        continue
                    idx += 1
                    vid = _view_id_from_filename(fn)
                    title = _title_from_nodes(nodes, vid)
                    label = f"{title} · {vid}" if title and vid else (vid or fn)
                    lines.append(f"  {idx}. {label}")
                    lines.append(f"     → {Path(fn).name}")
            for e in err_list:
                if e:
                    errors.append(str(e))
        else:
            lines.append(f"结果: ⚠️ 无法解析 JSON（Observation 长度 {len(obs)} 字）")

    elif "fs_read" in tb or tb == "read_file":
        low = obs.lower()
        if "file not found" in low or "不存在" in obs or "未找到" in obs:
            errors.append(_first_line_containing(obs, "not found") or obs[:200])
            lines.append("结果: ❌ 读文件失败")
        elif len(obs) < 80 and ("error" in low or "denied" in low):
            errors.append(obs.strip()[:300])
            lines.append("结果: ❌ 读文件失败")
        else:
            row_hint = _approx_table_rows(obs)
            lines.append(
                f"结果: ✅ 成功 — 读到约 {len(obs):,} 字符"
                + (f"（{row_hint}）" if row_hint else "")
            )
            return lines, errors

    elif "lark_notifier" in tb:
        data = _try_parse_json(obs)
        inp_obj = _try_parse_json(inp)
        md_len = 0
        if isinstance(inp_obj, dict):
            md_len = len(str(inp_obj.get("markdown_content") or ""))
        if isinstance(data, dict):
            err_code = str(data.get("error") or "")
            st = str(data.get("status") or "").lower()
            if st == "success":
                lines.append("结果: ✅ 飞书战报已成功发送到群里")
            elif err_code == "pmo_premature_notifier_blocked":
                block_lines, block_errors = _explain_notifier_block(data, markdown_len=md_len)
                lines.extend(block_lines)
                errors.extend(block_errors)
            elif st == "error" or err_code:
                block_lines, block_errors = _explain_notifier_block(data, markdown_len=md_len)
                if block_lines:
                    lines.extend(block_lines)
                    errors.extend(block_errors)
                else:
                    errors.append(str(data.get("msg") or err_code or "推送失败"))
                    lines.append("结果: ❌ 飞书推送失败（未发出卡片）")
            else:
                lines.append(f"结果: {obs[:120]}")
        elif "pmo_premature_notifier_blocked" in obs:
            data = _try_parse_json(obs)
            if isinstance(data, dict):
                block_lines, block_errors = _explain_notifier_block(data, markdown_len=md_len)
                lines.extend(block_lines)
                errors.extend(block_errors)
            else:
                errors.append("战报格式不完整或分析未完成，宿主拦截未发送飞书")
                lines.append("结果: 🚫 推送被拦截，消息未发到飞书")
        elif "pmo_false_sync_claim_blocked" in obs:
            errors.append("战报里错误声称「核心表未同步」，宿主拦截")
            lines.append("结果: 🚫 推送被拦截（内容不实）")
        elif '"status"' in obs and "success" in obs.lower():
            lines.append("结果: ✅ 可能已成功（请核对 status 字段）")
        else:
            lines.append(f"结果: {obs[:200]}")

    else:
        if len(obs) > 400:
            lines.append(f"结果: 工具返回约 {len(obs):,} 字符")
        elif obs.strip():
            lines.append(f"结果: {obs.strip()[:300]}")
        else:
            lines.append("结果: （空）")

    for pat in (
        "pmo_false_sync_claim_blocked",
        "pmo_branch_a_init_switch_blocked",
        "pmo_markdown_fix_only_db_blocked",
        "Access Denied",
        "rate limit",
        "timeout",
        "Traceback",
    ):
        if pat.lower() in obs.lower() and not any(pat in e for e in errors):
            snippet = _first_line_containing(obs, pat) or pat
            if snippet and snippet not in errors:
                errors.append(snippet[:240])
    # pmo_premature_notifier_blocked 已在 lark_notifier 分支用人话展开，避免重复
    if (
        "pmo_premature_notifier_blocked" in obs.lower()
        and "lark_notifier" not in tb
        and not any("战报" in e or "pmo_premature" in e for e in errors)
    ):
        data = _try_parse_json(obs)
        if isinstance(data, dict):
            block_lines, block_errors = _explain_notifier_block(data, markdown_len=0)
            lines.extend(block_lines)
            errors.extend(block_errors)

    return lines, errors


def _summarize_db_query_observation(inp: str, obs: str, errors: list[str]) -> list[str]:
    sql = _extract_sql(inp)
    data = _try_parse_json(obs)
    lines: list[str] = []

    if not isinstance(data, dict):
        if "error" in obs.lower()[:300]:
            errors.append(obs.strip()[:240])
            lines.append("结果: ❌ 查询失败")
        else:
            lines.append(f"结果: ⚠️ 无法解析（{len(obs)} 字符）")
        return lines

    if data.get("status") != "ok":
        errors.append(str(data.get("message") or data.get("error") or "查询失败"))
        for h in data.get("hints") or []:
            errors.append(f"💡 {h}")
        if data.get("schema_hint"):
            errors.append(f"📋 {data['schema_hint']}")
        lines.append("结果: ❌ 查询失败")
        return lines

    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    row_count = int(data.get("row_count") or len(rows))
    truncated = bool(data.get("truncated"))
    obs_len = len(obs)

    if "pmo_views_meta" in sql.lower():
        lines.append(f"结果: ✅ 数据地图 — {row_count} 个视图（{obs_len:,} 字符）")
        for r in rows[:20]:
            if not isinstance(r, dict):
                continue
            vid = str(r.get("view_id") or "")
            title = str(r.get("view_name") or "").strip() or vid
            cnt = r.get("record_count")
            if cnt is None and vid:
                cnt_s = "（未 SELECT record_count）"
            else:
                try:
                    cnt_s = f"{int(cnt):,} 条记录" if cnt is not None else "— 条"
                except (TypeError, ValueError):
                    cnt_s = str(cnt)
            cols = r.get("columns_json")
            if cnt is None and cols:
                try:
                    col_n = len(json.loads(cols)) if isinstance(cols, str) else len(cols)
                    cnt_s = f"{col_n} 列（未 SELECT record_count）"
                except Exception:
                    pass
            lines.append(f"  · 「{title}」 ({vid}) → {cnt_s}")
        hints = data.get("hints")
        if isinstance(hints, list):
            for h in hints[:3]:
                lines.append(f"  💡 {_truncate(str(h), 200)}")
        return lines

    if _sql_looks_aggregate(sql):
        lines.append(f"结果: ✅ 聚合统计 — {row_count} 组数据（{obs_len:,} 字符）")
        shown = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            line = _format_aggregate_row(r)
            if line:
                lines.append(f"  · {line}")
                shown += 1
            if shown >= 12:
                break
        if row_count > shown:
            lines.append(f"  … 另有 {row_count - shown} 行未展示")
        return lines

    lines.append(f"结果: ✅ 明细查询 — {row_count} 行（{obs_len:,} 字符）")
    shown = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        line = _format_detail_row(r)
        if line:
            lines.append(f"  · {line}")
            shown += 1
        if shown >= 8:
            break
    if row_count > shown:
        lines.append(f"  … 另有 {row_count - shown} 行未展示")
    if truncated:
        lines.append("  （Observation 已截断，完整结果见 DB）")
    hints = data.get("hints")
    if isinstance(hints, list) and row_count == 0:
        for h in hints[:3]:
            lines.append(f"  💡 {_truncate(str(h), 220)}")
        human_zero = _human_explain_zero_rows(sql)
        if human_zero:
            lines.append(f"  📌 人话解释: {human_zero}")
    elif row_count == 0:
        human_zero = _human_explain_zero_rows(sql)
        if human_zero:
            lines.append(f"  📌 人话解释: {human_zero}")
    return lines


def _human_explain_zero_rows(sql: str) -> str:
    sl = (sql or "").lower()
    if "vewcz1ffji" in sl and "父记录" in sql and "[0].text" in sql and " is null" in sl:
        return (
            "查到了 0 条——因为在「人员看板」上用了 Epic 筛选条件。"
            "人员表里的任务几乎都有上级（父记录），所以筛「父记录为空」必然为空。"
            "若要查 Epic，请改查 vewpI8lyYw（Step4）；若查人员任务，去掉父记录条件。"
        )
    if "父记录" in sql and " is null" in sl and "[0].text" not in sql:
        return "查到了 0 条——「父记录 IS NULL」写法不对，Epic 应改用 父记录[0].text IS NULL。"
    if "view_id" in sl and "pmo_raw_records" in sl:
        return "查到了 0 条——pmo_raw_records 表没有 view_id 列，应改用 source_view='vew…'。"
    return ""


def _explain_notifier_block(data: dict[str, Any], *, markdown_len: int = 0) -> tuple[list[str], list[str]]:
    """把宿主拦截 JSON 翻译成运维/业务能读懂的说明。"""
    lines: list[str] = []
    errors: list[str] = []
    reason = str(data.get("reason") or "")
    err_code = str(data.get("error") or "pmo_premature_notifier_blocked")
    missing_sections = data.get("missing_sections") or []
    missing_probes = data.get("missing_probes") or []
    msg = str(data.get("msg") or "")

    lines.append("结果: 🚫 飞书未发送——战报被系统拦截")

    if reason == "markdown_incomplete" or "markdown" in reason:
        lines.append(
            f"  原因: 战报正文不完整（当前 markdown 约 {markdown_len} 字，通常需要含三张表格的完整内容）"
        )
        if missing_sections:
            sec_txt = "、".join(str(s) for s in missing_sections[:5])
            lines.append(f"  缺少: {sec_txt}")
        errors.append("战报缺少 §1.4 三张 GFM 表格（📊需求进度 / 👥人员矩阵 / 📦版本映射），飞书未发送")
        errors.append(
            "解决办法: 把 Thought 里写好的三表 markdown 全文粘贴到 atom_lark_notifier 的 markdown_content 字段，"
            "不要只写摘要；若分析已完成则禁止重跑查库"
        )
    elif reason == "analysis_incomplete" or missing_probes:
        qn = data.get("db_query_count")
        min_q = data.get("min_db_queries")
        lines.append(f"  原因: 七步分析探针尚未完成（已查 {qn}/{min_q} 次）")
        if missing_probes:
            lines.append(f"  还缺: {('、'.join(str(x) for x in missing_probes[:6]))}")
        errors.append("分析步骤未完成，不允许推送；请继续 core:db_query 完成 Sprint/状态/人员/Epic/版本 探针")
    else:
        if msg:
            short = msg.replace("【宿主拦截】", "").strip()[:300]
            lines.append(f"  原因: {short}")
        errors.append(msg[:400] or err_code)

    if "禁止重跑" in msg or "无需重新执行" in msg or "markdown_fix" in err_code:
        errors.append("提示: 分析数据已够用，下一轮只改 markdown 格式，不要重新查库")

    return lines, errors


def _sql_looks_aggregate(sql: str) -> bool:
    sl = sql.lower()
    return "group by" in sl or "count(*)" in sl or "sum(" in sl or "avg(" in sl


def _format_aggregate_row(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for k, v in row.items():
        if v is None or str(v).strip() == "":
            continue
        parts.append(f"{k}={_truncate(str(v), 40)}")
    return " · ".join(parts[:4]) if parts else ""


def _format_detail_row(row: dict[str, Any]) -> str:
    req = row.get("req_name") or row.get("requirement") or row.get("Requirement") or row.get("req") or row.get("task")
    person = row.get("person") or row.get("person_name") or row.get("owner") or row.get("en_name")
    status = row.get("status") or row.get("status_text") or row.get("状态")
    priority = row.get("priority")
    sprint = row.get("sprint")
    due = row.get("due") or row.get("start_date")
    rid = row.get("id")
    parts: list[str] = []
    if person and req:
        parts.append(f"{person} → {_truncate(str(req), 40)}")
    elif req:
        parts.append(_truncate(str(req), 50))
    elif person:
        parts.append(f"人员={person}")
    if priority:
        parts.append(f"priority={priority}")
    if status:
        parts.append(f"status={_truncate(str(status), 20)}")
    if sprint:
        parts.append(f"sprint={_truncate(str(sprint), 24)}")
    if due:
        parts.append(f"due={_truncate(str(due), 16)}")
    if rid and not parts:
        parts.append(str(rid))
    elif rid and len(str(rid)) < 40 and len(parts) < 3:
        parts.append(f"id={rid}")
    if not parts:
        kv = []
        for k, v in list(row.items())[:3]:
            if v is not None and str(v).strip():
                kv.append(f"{k}={_truncate(str(v), 30)}")
        return " · ".join(kv)
    return " · ".join(parts)


def _extract_sql(inp: str) -> str:
    obj = _try_parse_json(inp)
    if isinstance(obj, dict):
        return str(obj.get("sql") or obj.get("query") or "").strip()
    return (inp or "").strip()


def _title_from_view_dict(v: dict[str, Any]) -> str:
    name = str(v.get("view_name") or "").strip()
    if name:
        return name
    fn = str(v.get("file_name") or "")
    m = re.search(r"\d+_(.+?)_[A-Za-z0-9]+_vew", fn)
    if m:
        return m.group(1).replace("_", " ").strip()
    return ""


def _action_target_summary(tool: str, inp: str) -> str:
    return ""


def _try_parse_json(s: str) -> dict[str, Any] | None:
    text = (s or "").strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_wiki_urls(obj: dict[str, Any] | None, raw: str) -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        wu = obj.get("wiki_urls")
        if isinstance(wu, list):
            out.extend(str(u).strip() for u in wu if str(u).strip())
        elif isinstance(wu, str):
            try:
                parsed = json.loads(wu)
                if isinstance(parsed, list):
                    out.extend(str(u).strip() for u in parsed if str(u).strip())
            except json.JSONDecodeError:
                pass
        cfg = obj.get("config")
        if isinstance(cfg, dict) and isinstance(cfg.get("wiki_urls"), list):
            out.extend(str(u).strip() for u in cfg["wiki_urls"] if str(u).strip())
    if not out and raw:
        out = re.findall(r"https://[^\s\"\\]+", raw)
    seen: set[str] = set()
    dedup: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def _view_id_from_filename(fn: str) -> str:
    m = re.search(r"(vew[a-zA-Z0-9]{6,12})", fn)
    return m.group(1) if m else ""


def _title_from_nodes(nodes: list[Any], view_id: str) -> str:
    if not view_id:
        return ""
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if str(n.get("view_id_hint") or "") == view_id:
            t = str(n.get("title") or "").strip()
            if t:
                return t
    return ""


def _approx_table_rows(obs: str) -> str:
    if "| ---" not in obs and "|:" not in obs:
        return ""
    lines = obs.splitlines()
    in_table = False
    rows = 0
    for ln in lines:
        if "|" in ln and ("---" in ln or ":---" in ln):
            in_table = True
            continue
        if in_table and ln.strip().startswith("|") and "---" not in ln:
            rows += 1
    if rows:
        return f"表内约 {rows} 行"
    return ""


def _first_line_containing(text: str, needle: str) -> str:
    nl = needle.lower()
    for ln in (text or "").splitlines():
        if nl in ln.lower():
            return ln.strip()[:240]
    return ""


def _truncate(s: str, n: int) -> str:
    t = (s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


# 兼容旧 import
def parse_wiki_urls_from_bi_action_input(inp: str) -> list[str]:
    return _extract_wiki_urls(_try_parse_json(inp), inp)


def summarize_lark_notifier_input(inp: str) -> str:
    obj = _try_parse_json(inp)
    if not isinstance(obj, dict):
        return (inp or "")[:500]
    md = str(obj.get("markdown_content") or obj.get("markdown") or "")
    return (
        f"title={obj.get('title')!s}, chat_id={obj.get('chat_id')!s}, "
        f"markdown 长度={len(md)} 字符"
    )
