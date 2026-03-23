"""
HR 招聘 DAG 节点 — 无人值守收网（状态机版）

替代 APScheduler 定时任务：在单节点内用主循环 + 信号中断 + 冷热岗博弈调度。
实际 Boss RPA 调用留作 mock / 后续接线至 com.jachin.hr.recruitment.tools。
"""
from __future__ import annotations

import logging
from typing import Any

from core.workflow_engine import DAGWorkflow, SIGNAL_STOP_HARVEST, WorkflowContext, WorkflowNode

logger = logging.getLogger(__name__)


def _hr_now_ts() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_hr_progress_line(line: str) -> None:
    try:
        from l3_node.task_planning import append_hr_recruitment_progress

        append_hr_recruitment_progress(line)
    except Exception as e:
        logger.debug("[HR-DAG] append_hr_progress 跳过: %s", e)


def _maybe_restore_hr_counts_from_progress(context: dict[str, Any], workflow_id: str) -> None:
    """当 context 中计数仍为 0 时，从 progress.md 当前 workflow 段落恢复，避免跨会话重复从 0 计数。"""
    if context.get("skip_hr_progress_restore"):
        return
    wid = (workflow_id or "").strip()
    if not wid:
        return
    g0 = int(context.get("greeted_count", 0) or 0)
    r0 = int(context.get("resume_count", 0) or 0)
    if g0 > 0 or r0 > 0:
        return
    try:
        from l3_node.task_planning import (
            extract_hr_progress_for_workflow,
            parse_hr_recruitment_progress_last_counts,
            read_hr_recruitment_progress,
        )

        full = read_hr_recruitment_progress()
        section = extract_hr_progress_for_workflow(full, wid)
        pg, pr = parse_hr_recruitment_progress_last_counts(section)
        if pg is not None:
            context["greeted_count"] = pg
        if pr is not None:
            context["resume_count"] = pr
        if pg is not None or pr is not None:
            logger.info(
                "[HarvestLoop] 已从 progress.md 恢复计数 workflow=%s greeted=%s resumes=%s",
                wid,
                pg,
                pr,
            )
    except Exception as e:
        logger.debug("[HarvestLoop] progress 恢复跳过: %s", e)


def _note_hr_interrupt(context: dict[str, Any], message: str) -> None:
    ts = _hr_now_ts()
    job = (context.get("job_folder") or context.get("job_name") or "").strip() or "（岗位未填）"
    wid = str(context.get("_dag_workflow_id") or "").strip()
    _append_hr_progress_line(
        f"> ⚠️ **[{ts}]** {message} （岗位: `{job}`，workflow: `{wid}`）"
    )

# Lark / 调度默认绑定的 HR 招聘 DAG id（无指针时使用）
HR_RECRUITMENT_DEFAULT_WORKFLOW_ID = "hr_recruitment_main"

# 默认目标（可被 context 覆盖）
DEFAULT_TARGET_RESUMES = 40
DEFAULT_TARGET_GREETS = 80
VALID_HEAT = frozenset({"hot", "cold"})


def _sync_signals_from_bridge(context: dict[str, Any], workflow_id: str) -> None:
    """将 Lark inject_signal 写入桥中的信号合并进当前 context。"""
    wid = (workflow_id or "").strip()
    if not wid:
        return
    try:
        from l3_node.workflow_signal_bridge import drain_merge_into_context

        drain_merge_into_context(context, wid)
    except Exception as e:
        logger.debug("[HarvestLoop] 信号桥同步跳过: %s", e)


def _pop_signal(context: dict[str, Any], workflow_id: str | None = None) -> str | None:
    if workflow_id:
        _sync_signals_from_bridge(context, workflow_id)
    if hasattr(context, "pop_signal") and callable(getattr(context, "pop_signal")):
        return context.pop_signal()  # type: ignore[no-any-return]
    q = context.get("_workflow_signals")
    if isinstance(q, list) and q:
        return str(q.pop(0))
    return None


def _mock_atom_greet_recommend_boss(context: dict[str, Any], *, round_tag: str = "") -> int:
    """
    Mock: atom_greet_recommend_boss（推荐牛人打招呼）。
    真实实现: tools.atom_greet_recommend_boss.atom_greet_recommend_boss(...)
    """
    inc = int(context.get("_mock_greet_increment", 1))
    logger.info("[HarvestLoop] MOCK atom_greet_recommend_boss %s increment=%s", round_tag, inc)
    # TODO: from tools.atom_greet_recommend_boss import atom_greet_recommend_boss
    # result = atom_greet_recommend_boss(cdp_url=context.get("cdp_url", "http://127.0.0.1:9222"), jd_config_path=context.get("jd_config_path", ""))
    # return int(result.get("greeted_count", 0))
    return max(0, inc)


def _mock_atom_inbox_harvester_full_flow(context: dict[str, Any], *, round_tag: str = "") -> int:
    """
    Mock: atom_inbox_harvester_full_flow（收网抓简历）。
    真实实现: 由 recruitment_scheduler 动态加载 tools.atom_inbox_harvester
    """
    key = "_mock_harvest_remaining"
    remaining = int(context.get(key, 5))
    batch = int(context.get("_mock_harvest_batch", 2))
    got = min(batch, max(0, remaining))
    context[key] = remaining - got
    logger.info(
        "[HarvestLoop] MOCK atom_inbox_harvester_full_flow %s downloaded=%s remaining=%s",
        round_tag,
        got,
        context[key],
    )
    # TODO: atom_inbox_harvester_full_flow(cdp_url=..., job_text=..., save_dir=..., ...)
    return got


def _run_greet_via_mcp(ctx: dict[str, Any], *, round_tag: str = "") -> int:
    """调用 atom_greet_recommend_boss，将 DAG context 作为 os_context 贯穿；失败则 mock。"""
    if ctx.get("harvest_loop_use_mock"):
        return _mock_atom_greet_recommend_boss(ctx, round_tag=round_tag)
    try:
        import sys

        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        root = _get_hr_recruitment_plugin_root()
        if not root:
            return _mock_atom_greet_recommend_boss(ctx, round_tag=round_tag)
        s = str(root.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)
        from tools.atom_greet_recommend_boss import atom_greet_recommend_boss

        mg = int(ctx.get("max_greet_per_inner_call", 3) or 3)
        r = atom_greet_recommend_boss(
            cdp_url=str(ctx.get("cdp_url") or "http://127.0.0.1:9222"),
            jd_config_path=str(ctx.get("jd_config_path") or ""),
            max_greet_per_run=mg,
            workflow_hitl_context=ctx,
            os_context=ctx,
        )
        if r.get("stopped_by_os"):
            ctx["_os_playwright_stop"] = True
        return int(r.get("greeted_count", 0))
    except Exception as e:
        logger.warning("[HarvestLoop] greet 真实 MCP 不可用，回退 mock: %s", e)
        return _mock_atom_greet_recommend_boss(ctx, round_tag=round_tag)


def _run_inbox_via_mcp(ctx: dict[str, Any], *, round_tag: str = "") -> int:
    """调用 atom_inbox_harvester_full_flow，os_context=ctx 实现秒级 STOP。"""
    if ctx.get("harvest_loop_use_mock"):
        return _mock_atom_inbox_harvester_full_flow(ctx, round_tag=round_tag)
    try:
        import sys
        from pathlib import Path

        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        root = _get_hr_recruitment_plugin_root()
        if not root:
            return _mock_atom_inbox_harvester_full_flow(ctx, round_tag=round_tag)
        s = str(root.resolve())
        if s not in sys.path:
            sys.path.insert(0, s)
        from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow
        from tools.atom_post_job_boss import get_jd_select, load_jd_config

        jd_path = str(ctx.get("jd_config_path") or "")
        job_folder = str(ctx.get("job_folder") or ctx.get("job_name") or "")
        jd = load_jd_config(jd_path, job_folder)
        job_text = get_jd_select(jd) or job_folder or "职位"
        save_dir = str(ctx.get("inbox_save_dir") or "").strip()
        if not save_dir and job_folder:
            save_dir = str(Path.home() / ".jachin" / "workspace" / "hr_recruitment" / job_folder / "pending")
        max_items = int(ctx.get("inbox_max_items", 50) or 50)
        sw = int(ctx.get("stop_when_downloaded", 0) or 0)
        r = atom_inbox_harvester_full_flow(
            cdp_url=str(ctx.get("cdp_url") or "http://127.0.0.1:9222"),
            job_text=job_text,
            download_to_pending=True,
            max_items=max_items,
            save_dir=save_dir or None,
            job_folder=job_folder,
            filter_tab=str(ctx.get("inbox_filter_tab") or ""),
            request_if_no_resume=bool(ctx.get("request_if_no_resume", True)),
            stop_when_downloaded=sw,
            use_all_positions=bool(ctx.get("use_all_positions", True)),
            workflow_hitl_context=ctx,
            os_context=ctx,
        )
        if r.get("stopped_by_os"):
            ctx["_os_playwright_stop"] = True
        return int(r.get("downloaded", 0))
    except Exception as e:
        logger.warning("[HarvestLoop] inbox 真实 MCP 不可用，回退 mock: %s", e)
        return _mock_atom_inbox_harvester_full_flow(ctx, round_tag=round_tag)


class HrRecruitmentPlanInitNode(WorkflowNode):
    """
    DAG 起点：覆写 ``~/.jachin/workspace/hr_recruitment/task_plan.md``，
    并在 ``progress.md`` 写入会话头，供 HarvestLoop 与跨会话解析。
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        if context.get("skip_hr_plan_init_node"):
            return {}
        try:
            from l3_node.task_planning import append_hr_session_header, write_hr_recruitment_task_plan
        except Exception as e:
            logger.warning("[HrPlanInit] task_planning 不可用: %s", e)
            return {}

        job = str(context.get("job_folder") or context.get("job_name") or "").strip() or "（未命名岗位）"
        wid = str(context.get("_dag_workflow_id") or HR_RECRUITMENT_DEFAULT_WORKFLOW_ID).strip()
        context["_dag_workflow_id"] = wid
        tg = int(context.get("target_greets", DEFAULT_TARGET_GREETS))
        tr = int(context.get("target_resumes", DEFAULT_TARGET_RESUMES))
        ts = _hr_now_ts()
        body = f"""# HR 招聘宏图（Jachin OS 自动维护）

## 宏观目标

- **岗位**: `{job}`
- **目标**: 为 `{job}` 招聘。**需沟通 {tg} 人，抓取 {tr} 份简历。**

## 元数据

- **workflow_id**: `{wid}`
- **更新时间**: {ts}

---

*战况见同目录 `progress.md`；跨会话续跑请结合 workflow 持久化状态阅读进度。*
"""
        write_hr_recruitment_task_plan(body)
        append_hr_session_header(wid, job, ts)
        _append_hr_progress_line(
            f"- 🚀 **[{ts}]** 宏图已立：岗位 `{job}`，目标沟通 **{tg}** 人 / 简历 **{tr}** 份。"
        )
        logger.info("[HrPlanInit] 已写入 task_plan + progress 会话头 workflow=%s", wid)
        return {"_hr_task_plan_initialized": True, "_hr_plan_ts": ts}


class HarvestLoopNode(WorkflowNode):
    """
    无人值守收网循环节点：动态权重（冷热岗）+ STOP_HARVEST 信号优雅退出。

    期望 ``context`` 为 :class:`core.workflow_engine.WorkflowContext`（DAGWorkflow.run 已包装）。

    Context 常用键:
        target_resumes: int, 默认 40
        target_greets: int, 默认 80
        job_heat: 'hot' | 'cold'，冷岗主动出击，热岗先收网后补打招呼
        jd_config_path, cdp_url, job_folder: 真实 MCP 接线
        harvest_loop_use_mock: True 时仅用内置 mock（无 Chrome 环境）
        harvest_loop_max_iterations: >0 时外层 while 最多跑这么多轮（无人值守调度 tick 用）
        cold_greet_inner_rounds: 冷岗每外层轮内打招呼 MCP 调用次数，默认 3；调度器单 tick 建议 1
        skip_hr_progress_restore: True 时不从 progress.md 恢复计数（短 tick / 调度器）
        skip_hr_plan_init_node: 由 HrRecruitmentPlanInitNode 使用；本节点忽略
        _mock_*: mock 模式下增量参数
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(context, WorkflowContext):
            logger.debug(
                "[HarvestLoop] context 非 WorkflowContext，仍尝试用 _workflow_signals 兼容"
            )

        target_resumes = int(context.get("target_resumes", DEFAULT_TARGET_RESUMES))
        target_greets = int(context.get("target_greets", DEFAULT_TARGET_GREETS))
        job_heat = str(context.get("job_heat", "cold") or "cold").lower()
        if job_heat not in VALID_HEAT:
            logger.warning("[HarvestLoop] 未知 job_heat=%s，按 cold 处理", job_heat)
            job_heat = "cold"

        wid = str(context.get("_dag_workflow_id") or HR_RECRUITMENT_DEFAULT_WORKFLOW_ID).strip()
        context["_dag_workflow_id"] = wid
        _maybe_restore_hr_counts_from_progress(context, wid)
        greeted_count = int(context.get("greeted_count", 0) or 0)
        resume_count = int(context.get("resume_count", 0) or 0)

        try:
            from l3_node.local_memory import set_hr_recruitment_workflow_pointer

            job_folder = str(context.get("job_folder") or context.get("job_name") or "").strip()
            jd_cfg = str(context.get("jd_config_path") or "").strip()
            pending_guess = str(context.get("resume_pending_dir") or "").strip()
            if not pending_guess and job_folder:
                from pathlib import Path

                pending_guess = str(
                    Path.home() / ".jachin" / "workspace" / "hr_recruitment" / job_folder / "pending"
                )
            set_hr_recruitment_workflow_pointer(
                wid,
                job_name=job_folder,
                jd_config_path=jd_cfg,
                resume_pending_dir=pending_guess,
            )
        except Exception as e:
            logger.debug("[HarvestLoop] 写入 HR 指针跳过: %s", e)

        logger.info(
            "[HarvestLoop] 开始 workflow_id=%s job_heat=%s target_greets=%s target_resumes=%s",
            wid,
            job_heat,
            target_greets,
            target_resumes,
        )

        def _apply_stop() -> None:
            context["current_progress"] = (
                f"greets {greeted_count}/{target_greets}, resumes {resume_count}/{target_resumes}"
            )

        iteration = 0
        while greeted_count < target_greets or resume_count < target_resumes:
            iteration += 1
            stopped = False
            # --- 信号中断（每轮开头强制检查；_pop_signal 内会同步 Lark 注入的信号）---
            if _pop_signal(context, wid) == SIGNAL_STOP_HARVEST:
                _apply_stop()
                _note_hr_interrupt(
                    context,
                    "收到最高统帅 **STOP_HARVEST** 停止指令，收网主循环退出。",
                )
                logger.warning(
                    "[HarvestLoop] 收到 %s，优雅停止。进度 resumes=%s greets=%s (%s)",
                    SIGNAL_STOP_HARVEST,
                    resume_count,
                    greeted_count,
                    context.get("current_progress", ""),
                )
                break

            if job_heat == "cold":
                # 冷板凳岗：主动出击 — 默认每轮 3 次打招呼 MCP，再 1 次收网；APScheduler tick 可设 cold_greet_inner_rounds=1
                cold_inner = max(1, int(context.get("cold_greet_inner_rounds", 3) or 3))
                for g in range(cold_inner):
                    if greeted_count >= target_greets and resume_count >= target_resumes:
                        break
                    if _pop_signal(context, wid) == SIGNAL_STOP_HARVEST:
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "收到 **STOP_HARVEST**（冷岗 / 打招呼循环内），任务挂起。",
                        )
                        logger.warning("[HarvestLoop] STOP_HARVEST（cold / greet 内）")
                        stopped = True
                        break
                    if greeted_count >= target_greets:
                        break
                    before_g = greeted_count
                    greeted_count += _run_greet_via_mcp(
                        context, round_tag=f"cold i{iteration} g{g + 1}/{cold_inner}"
                    )
                    greeted_count = min(greeted_count, target_greets)
                    inc_g = greeted_count - before_g
                    if inc_g > 0:
                        ts = _hr_now_ts()
                        _append_hr_progress_line(
                            f"- [x] [{ts}] 成功打招呼 {inc_g} 人。当前总进度：（{greeted_count} / {target_greets}）"
                        )
                    if context.get("_os_playwright_stop"):
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "**OS 级停止**（冷岗打招呼）：Playwright 片段中止，可能为 STOP 或用户中断。",
                        )
                        context.pop("_os_playwright_stop", None)
                        stopped = True
                        break

                if stopped:
                    break

                if _pop_signal(context, wid) == SIGNAL_STOP_HARVEST:
                    _apply_stop()
                    _note_hr_interrupt(
                        context,
                        "收到 **STOP_HARVEST**（冷岗 / 收网前），任务挂起。",
                    )
                    logger.warning("[HarvestLoop] STOP_HARVEST（cold / harvest 前）")
                    break

                if resume_count < target_resumes:
                    got = _run_inbox_via_mcp(
                        context, round_tag=f"cold i{iteration} harvest"
                    )
                    resume_count += got
                    resume_count = min(resume_count, target_resumes)
                    if got > 0:
                        ts = _hr_now_ts()
                        _append_hr_progress_line(
                            f"- [x] [{ts}] 成功抓取 {got} 份简历。当前总进度：（{resume_count} / {target_resumes}）"
                        )
                    if context.get("_os_playwright_stop"):
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "**OS 级停止**（冷岗收网）：Playwright 片段中止。",
                        )
                        context.pop("_os_playwright_stop", None)
                        stopped = True

                if stopped:
                    break

            else:
                # 爆款岗：坐等收网 — 先 harvest 直到无新简历，再 1 次打招呼
                while resume_count < target_resumes:
                    if _pop_signal(context, wid) == SIGNAL_STOP_HARVEST:
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "收到 **STOP_HARVEST**（热岗 / 收网循环内），任务挂起。",
                        )
                        logger.warning("[HarvestLoop] STOP_HARVEST（hot / harvest 内）")
                        stopped = True
                        break
                    n = _run_inbox_via_mcp(
                        context, round_tag=f"hot i{iteration} harvest"
                    )
                    if n <= 0:
                        break
                    resume_count += n
                    resume_count = min(resume_count, target_resumes)
                    if n > 0:
                        ts = _hr_now_ts()
                        _append_hr_progress_line(
                            f"- [x] [{ts}] 成功抓取 {n} 份简历。当前总进度：（{resume_count} / {target_resumes}）"
                        )
                    if context.get("_os_playwright_stop"):
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "**OS 级停止**（热岗收网）：Playwright 片段中止。",
                        )
                        context.pop("_os_playwright_stop", None)
                        stopped = True
                        break

                if stopped:
                    break

                if context.get("_os_playwright_stop"):
                    _apply_stop()
                    _note_hr_interrupt(
                        context,
                        "**OS 级停止**（热岗收网后检查）：Playwright 片段中止。",
                    )
                    context.pop("_os_playwright_stop", None)
                    break

                if _pop_signal(context, wid) == SIGNAL_STOP_HARVEST:
                    _apply_stop()
                    _note_hr_interrupt(
                        context,
                        "收到 **STOP_HARVEST**（热岗 / 打招呼前），任务挂起。",
                    )
                    break

                if greeted_count < target_greets:
                    if _pop_signal(context, wid) == SIGNAL_STOP_HARVEST:
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "收到 **STOP_HARVEST**（热岗 / 打招呼前内层），任务挂起。",
                        )
                        break
                    before_gh = greeted_count
                    greeted_count += _run_greet_via_mcp(
                        context, round_tag=f"hot i{iteration} greet-after-dry"
                    )
                    greeted_count = min(greeted_count, target_greets)
                    inc_gh = greeted_count - before_gh
                    if inc_gh > 0:
                        ts = _hr_now_ts()
                        _append_hr_progress_line(
                            f"- [x] [{ts}] 成功打招呼 {inc_gh} 人。当前总进度：（{greeted_count} / {target_greets}）"
                        )
                    if context.get("_os_playwright_stop"):
                        _apply_stop()
                        _note_hr_interrupt(
                            context,
                            "**OS 级停止**（热岗打招呼）：Playwright 片段中止。",
                        )
                        context.pop("_os_playwright_stop", None)
                        break

            # 防止 mock 永不收敛
            if iteration > max(target_greets, target_resumes, 200):
                logger.error("[HarvestLoop] 超过安全迭代上限，强制结束")
                _note_hr_interrupt(
                    context,
                    "超过安全迭代上限，HarvestLoop **强制结束**（请检查 mock 或目标参数）。",
                )
                break

            # 调度器「单 tick」模式：每触发一次 APScheduler 只跑有限轮外层循环（对齐原每 tick 单次 atom 行为）
            hm = int(context.get("harvest_loop_max_iterations") or 0)
            if hm > 0 and iteration >= hm:
                logger.info("[HarvestLoop] 已达 harvest_loop_max_iterations=%s，本 tick 结束", hm)
                _append_hr_progress_line(
                    f"- ⏸️ **[{_hr_now_ts()}]** 本调度 tick 已达 `harvest_loop_max_iterations={hm}`，外层循环暂停；"
                    f" 当前进度：打招呼（{greeted_count} / {target_greets}），简历（{resume_count} / {target_resumes}）。"
                )
                break

        result = {
            "status": "harvest_completed",
            "resumes": resume_count,
            "greets": greeted_count,
            "job_heat": job_heat,
            "target_resumes": target_resumes,
            "target_greets": target_greets,
        }
        context["greeted_count"] = greeted_count
        context["resume_count"] = resume_count
        logger.info("[HarvestLoop] 结束 %s", result)
        return result


class AnalyzeResumeNode(WorkflowNode):
    """
    透析镜：对 context 中指定目录的已入库简历调用 mcp:hr_analyze_resume（Wasm com.jachin.hr.analyzer4）。
    前置：HarvestLoop 已完成或已手动停止收网。
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        target_dir = (context.get("resume_pending_dir") or context.get("target_dir") or "").strip()
        jd_template = (context.get("jd_template") or context.get("jd_full") or "").strip()
        jd_path = str(context.get("jd_config_path") or "").strip()

        if not jd_template and jd_path:
            try:
                import json
                from pathlib import Path

                p = Path(jd_path)
                if p.exists():
                    jd = json.loads(p.read_text(encoding="utf-8"))
                    jd_template = (jd.get("jd_full") or "").strip()
            except Exception as e:
                logger.debug("[AnalyzeResumeNode] 读取 jd.json 失败: %s", e)

        if not target_dir:
            from pathlib import Path

            job = str(context.get("job_folder") or context.get("job_name") or "").strip()
            if job:
                target_dir = str(Path.home() / ".jachin" / "workspace" / "hr_recruitment" / job / "pending")

        if not target_dir:
            logger.warning("[AnalyzeResumeNode] 无 resume 目录，跳过透析镜")
            return {"analyze_ok": False, "analyze_skipped": True, "analyze_error": "无 target_dir"}

        if not jd_template:
            jd_template = "请根据岗位要求评估候选人简历匹配度。"

        try:
            from l3_node.hr_loader import _get_hr_recruitment_plugin_root

            root = _get_hr_recruitment_plugin_root()
            if not root:
                return {"analyze_ok": False, "analyze_error": "HR MCP 包未找到"}
            import sys

            s = str(root.resolve())
            if s not in sys.path:
                sys.path.insert(0, s)
            from tools.hr_analyze_resume import hr_analyze_resume

            raw = hr_analyze_resume(
                target_dir=target_dir,
                jd_template=jd_template,
                target_role=str(context.get("target_role") or "backend_engineer"),
                focus_keywords=str(context.get("focus_keywords") or ""),
                strictness=str(context.get("strictness") or "standard"),
                output_dir=str(context.get("analyze_output_dir") or ""),
            )
            snippet = (raw or "")[:800]
            logger.info("[AnalyzeResumeNode] 透析镜完成 len=%d", len(raw or ""))
            return {"analyze_ok": True, "analyze_result_preview": snippet}
        except Exception as e:
            logger.exception("[AnalyzeResumeNode] 透析镜失败")
            return {"analyze_ok": False, "analyze_error": str(e)}


def build_hr_recruitment_dag(
    workflow_id: str | None = None,
    *,
    include_analyze: bool = True,
) -> DAGWorkflow:
    """
    构建 HR 招聘 DAG。

    默认：``hr_plan_init`` → ``harvest_loop`` → （可选）``analyze_resumes``。
    include_analyze=False 时仅含计划初始化 + HarvestLoop（APScheduler tick 可设 skip_hr_plan_init_node）。
    """
    wid = (workflow_id or HR_RECRUITMENT_DEFAULT_WORKFLOW_ID).strip()
    wf = DAGWorkflow(wid)
    wf.add_node(HrRecruitmentPlanInitNode("hr_plan_init"))
    wf.add_node(HarvestLoopNode("harvest_loop"))
    wf.add_edge("hr_plan_init", "harvest_loop")
    if include_analyze:
        wf.add_node(AnalyzeResumeNode("analyze_resumes"))
        wf.add_edge("harvest_loop", "analyze_resumes")
    return wf
