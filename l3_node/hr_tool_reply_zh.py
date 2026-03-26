"""
招聘 MCP 工具返回值的 HR 可读中文封装。

插件层仍返回 JSON 便于程序与日志；经 mcp_registry 包装后再进入 Agent Observation / 飞书。
"""

from __future__ import annotations

from typing import Any


def format_add_automated_recruitment_task_result_for_hr(d: dict[str, Any]) -> str:
    """
    将 ``add_scheduled_job`` / ``add_automated_recruitment_task`` 的成功或失败 dict
    转为飞书 HR 能直接读懂的短文案（无 JSON、少字段名）。
    """
    if not isinstance(d, dict):
        return str(d)

    if not d.get("ok"):
        err = (d.get("error") or "未知原因").strip()
        return (
            "未能启动招聘无人值守定时任务。\n\n"
            f"原因：{err}\n\n"
            "请确认岗位名、jd.json 是否齐全，或联系技术同事查看 L3 日志。"
        )

    jn = (d.get("job_name") or "当前岗位").strip()
    go_t = d.get("greet_only_total_target")
    try:
        go_n = int(go_t) if go_t is not None else 0
    except (TypeError, ValueError):
        go_n = 0

    if go_n > 0:
        try:
            rim = int(d.get("greet_only_interval_minutes") or 0)
        except (TypeError, ValueError):
            rim = 0
        rim_txt = f"{rim} 分钟" if rim > 0 else "默认间隔"
        lines = [
            f"已为岗位「{jn}」启动 **仅打招呼** 定时任务。",
            f"· 目标：累计成功打招呼 **{go_n}** 次；约每 **{rim_txt}** 走一步。",
            "· 达标后会自动停表，并可通过飞书询问是否开始收简历。",
            "· 需要停：发 **停止收网**；Chrome 请保持已挂 CDP。",
        ]
        _append_memory_snapshot_lines(lines, d.get("job_memory_at_start"))
        return "\n".join(lines)

    eg = d.get("enable_greet_recommend")
    if eg is None and isinstance(d.get("job_memory_at_start"), dict):
        summ = (d["job_memory_at_start"] or {}).get("saved_config_summary")
        if isinstance(summ, dict) and summ.get("enable_greet_recommend") is not None:
            eg = summ.get("enable_greet_recommend")
    # 与 jd / add_scheduled_job 缺省一致：未带回该键时按「开打招呼」展示
    greet_on = bool(eg) if eg is not None else True

    def _ig(key: str, default: int) -> int:
        v = d.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    mch = _ig("max_count_per_harvest_tick", 50)
    gt_disp = _ig("greet_target", 3)
    sw_disp = _ig("greet_harvest_switch_interval_minutes", 10)

    try:
        cap = int(d.get("resume_collect_target") or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0 and isinstance(d.get("job_memory_at_start"), dict):
        summ2 = (d["job_memory_at_start"] or {}).get("saved_config_summary")
        if isinstance(summ2, dict):
            try:
                cap = int(summ2.get("resume_collect_target") or summ2.get("analyze_threshold") or 0)
            except (TypeError, ValueError):
                cap = 0

    if not greet_on:
        cap_txt = f"**{cap}** 份" if cap > 0 else "（见 jd 或进度）"
        lines = [
            f"已为岗位「{jn}」注册 **仅收网** 定时任务（**只从沟通里抓简历**，不打推荐/不主动打招呼）。",
            f"· 本轮计划收到约 **{cap_txt}** 简历后按规则停收（与透析触发份数一致）。",
            f"· **单次收网 tick** 最多处理约 **{mch}** 个沟通会话；推荐↔收网交替间隔此处为 **{sw_disp}** 分钟（仅收网模式下仍按该间隔跑收网 tick）。",
            "· 一般约 **30 秒内** 开始第一轮抓取；请保持 **Chrome 已挂 CDP**，Boss 能打开**沟通**页。",
        ]
        _append_memory_snapshot_lines(lines, d.get("job_memory_at_start"))
        lines.extend(
            [
                "",
                "常用短指令：**进度**、**停止收网**、**分析简历**、**再抓 N 份**、**仅收网**；"
                "若要改为「推荐↔收简历」交替，请让助手重新注册或说明开打招呼。",
                "改交替参数可一条发：`打招呼改成10人 收网改成50人 推荐间隔2分钟`（`收网改成N人`=每轮左侧会话上限，累计份数用 **再抓**）。",
            ]
        )
        return "\n".join(lines)

    cap_txt2 = f"**{cap}** 份" if cap > 0 else "（见 jd）"
    lines = [
        f"已为岗位「{jn}」注册 **无人值守** 定时任务。",
        "· 模式：**推荐/打招呼** 与 **沟通里收简历** 按间隔 **交替** 进行（不是只抓简历）。",
        f"· **本岗已生效数字**：累计收网约 **{cap_txt2}** 后停自动；**每轮打招呼**最多 **{gt_disp}** 人；"
        f"**单次收网 tick** 最多处理约 **{mch}** 个沟通会话；推荐↔收简历约每 **{sw_disp}** 分钟切换一步（打满或暂无人可聊可提前切换）。",
        "· 一般约 **30 秒内** 开始第一轮；请保持 **Chrome 已挂 CDP**，Boss 能打开推荐/沟通页。",
    ]
    _append_memory_snapshot_lines(lines, d.get("job_memory_at_start"))
    lines.extend(
        [
            "",
            "常用短指令：**进度**、**停止收网**、**分析简历**、**再抓 N 份**、**同意调度**、**仅收网**（切到只收沟通简历）。",
            "改每轮上限与轮换：`打招呼改成10人 收网改成50人 推荐间隔2分钟`（一条即可；`收网改成N人` 是每轮会话数，不是累计简历份数）。",
        ]
    )
    return "\n".join(lines)


def _append_memory_snapshot_lines(lines: list[str], mem: Any) -> None:
    """基于登记任务**前**的快照写两三句人话，避免 HR 把「scheduler_jobs_active:false」当成没启动。"""
    if not isinstance(mem, dict):
        return
    pending = int(mem.get("pending_pdf_count") or 0)
    unproc = int(mem.get("unprocessed_for_analysis") or 0)
    summ = mem.get("saved_config_summary")
    cap, thr = 0, 0
    eg = True
    if isinstance(summ, dict):
        try:
            cap = int(summ.get("resume_collect_target") or 0)
        except (TypeError, ValueError):
            cap = 0
        try:
            thr = int(summ.get("analyze_threshold") or 0)
        except (TypeError, ValueError):
            thr = 0
        v = summ.get("enable_greet_recommend")
        if v is not None:
            eg = bool(v)

    lines.append(
        "· 说明：下面数字是 **刚登记任务前** 的磁盘快照，**不是**「系统没启动」。"
        "定时任务已在后台挂上。"
    )
    lines.append(f"· 当时 pending 里约 **{pending}** 份简历；约 **{unproc}** 份还在排队等 AI 评价（估算）。")
    if cap > 0:
        greet_w = "开" if eg else "关"
        lines.append(
            f"· jd 里本轮约收 **{cap}** 份；攒够 **{thr or cap}** 份未评价简历会集中跑一轮分析；自动打招呼：**{greet_w}**。"
        )
