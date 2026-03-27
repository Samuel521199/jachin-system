"""
职位发布成功后：把无人值守参数写入 jd.json、标记「待 HR 确认调度」、向飞书发说明。
HR 在飞书回复「同意调度」后（见 lark_workflow_command_interceptor）再真正注册 APScheduler。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _format_scheduler_confirm_lark_text(
    job_name: str,
    *,
    greet_harvest_switch_interval_minutes: int,
    greet_target: int,
    max_count_per_harvest_tick: int,
    analyze_threshold: int,
    resume_collect_target: int,
    enable_greet_recommend: bool,
) -> str:
    """飞书给人看的短说明；完整参数由调用方写入日志。"""
    jn = (job_name or "").strip() or "本岗位"
    sw = int(greet_harvest_switch_interval_minutes)
    gt = int(greet_target)
    rct = int(resume_collect_target)
    at = int(analyze_threshold)
    lines: list[str] = [
        f"【{jn}】职位已在 Boss 发布，**自动招聘还没开始**。\n",
    ]
    if enable_greet_recommend:
        lines.append(
            f"点「开始」后，Boss **同一页面**下**同一时间只跑一种动作**：**先牛人沟通（推荐/打招呼）→ 再沟通里收简历** 交替循环；"
            f"默认约每 **{sw}** 分钟进入下一步（**打满本轮 {gt} 个招呼**或**沟通里暂无人可聊**时会**提前**切换，不必等满时间）。"
        )
    else:
        lines.append(
            f"点「开始」后，**不会**自动去推荐里打招呼；约每 **{sw}** 分钟在沟通里收简历。"
        )
    lines.append(f"\n本轮计划先收到约 **{rct}** 份简历后停止自动收网/打招呼。")
    lines.append(
        f"\n当**未出 AI 评价的简历**攒够约 **{at}** 份时，会触发透析分析（按**份数**触发，不是「每隔多少分钟必须分析」）。"
    )
    mch = int(max_count_per_harvest_tick)
    lines.append(
        f"\n**单次收网（每一 tick）**在沟通列表里最多处理约 **{mch}** 个会话（有简历下附件，无则点求简历）；"
        f"与上面「累计 **{rct}** 份后停自动」是两层上限。"
    )
    lines.append(
        "\n\n想改规则可以说：**收网改成80人**、**打招呼改成5人**、**推荐间隔20分钟**（即推荐↔收简历轮换间隔）。"
        "\n都合适请回复：**同意调度**"
        "\n（技术日志里可核对完整数值。）"
    )
    logger.debug("[Lark HR] 调度确认单 max_count_per_tick=%s greet_target=%s", mch, gt)
    return "".join(lines)


def _doc_int(doc: dict, key: str, default: int) -> int:
    v = doc.get(key)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _doc_bool(doc: dict, key: str, default: bool) -> bool:
    v = doc.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("0", "false", "no", "否", "关", "off"):
        return False
    if s in ("1", "true", "yes", "是", "开", "on"):
        return True
    return default


def _merge_scheduling_into_jd(jd_path: Path, fields: dict) -> None:
    jd_path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict = {}
    if jd_path.exists():
        try:
            raw = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                doc = raw
        except Exception as e:
            logger.warning("读取 jd.json 失败，将覆盖写入调度字段: %s", e)
    for k, v in fields.items():
        if v is not None:
            doc[k] = v
    doc.pop("harvest_delay_seconds", None)
    doc.pop("greet_only_total_target", None)
    doc.pop("greet_only_interval_minutes", None)
    jd_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def hr_scheduler_send_confirm_prompt(
    job_name: str,
    jd_config_path: str = "",
    greet_harvest_switch_interval_minutes: int | None = None,
    greet_target: int | None = None,
    max_count_per_harvest_tick: int | None = None,
    analyze_threshold: int | None = None,
    resume_collect_target: int | None = None,
    enable_greet_recommend: bool | None = None,
) -> str:
    """
    合并调度参数到 jd.json，workflow 指针标记 scheduler_pending_confirm，并发飞书说明。
    各数值/bool 为 None 时 **保留 jd.json 已有值**（避免发帖后再次发确认单把「打招呼改成5人」等覆盖回默认 3/4）。
    返回 JSON 字符串。

    已移除 HR 侧 ``auto_analyze`` 参数：透析仍由规则引擎按 ``analyze_threshold``（份数）触发。
    """
    jn = (job_name or "").strip()
    if not jn:
        return json.dumps({"ok": False, "error": "job_name 不能为空"}, ensure_ascii=False)

    from .hr_data_paths import get_job_jd_path, sanitize_job_folder

    jd_path = Path((jd_config_path or "").strip()) if (jd_config_path or "").strip() else get_job_jd_path(jn)
    if not jd_path.exists():
        return json.dumps(
            {"ok": False, "error": f"jd.json 不存在: {jd_path}，请先完成职位发布与 jd 持久化"},
            ensure_ascii=False,
        )

    doc: dict = {}
    try:
        raw = json.loads(jd_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            doc = raw
    except Exception as e:
        logger.warning("读取 jd.json 用于合并调度默认值失败: %s", e)

    if greet_harvest_switch_interval_minutes is not None:
        sw = int(greet_harvest_switch_interval_minutes)
    elif doc.get("greet_harvest_switch_interval_minutes") is not None:
        sw = _doc_int(doc, "greet_harvest_switch_interval_minutes", 10)
    else:
        sw = _doc_int(doc, "recommend_interval_minutes", 10)
    sw = max(1, min(120, sw))
    gt = int(greet_target) if greet_target is not None else _doc_int(doc, "greet_target", 3)
    mch = (
        int(max_count_per_harvest_tick)
        if max_count_per_harvest_tick is not None
        else _doc_int(doc, "max_count", 50)
    )
    if resume_collect_target is not None:
        rct = int(resume_collect_target)
    elif analyze_threshold is not None:
        rct = int(analyze_threshold)
    elif doc.get("resume_collect_target") is not None:
        rct = _doc_int(doc, "resume_collect_target", _doc_int(doc, "analyze_threshold", 4))
    else:
        rct = _doc_int(doc, "analyze_threshold", 4)
    rct = max(1, min(9999, int(rct)))
    at = rct
    eg = (
        bool(enable_greet_recommend)
        if enable_greet_recommend is not None
        else _doc_bool(doc, "enable_greet_recommend", True)
    )
    fields = {
        "greet_harvest_switch_interval_minutes": sw,
        "recommend_interval_minutes": sw,
        "greet_target": gt,
        "max_count": mch,
        "analyze_threshold": at,
        "resume_collect_target": rct,
        "enable_greet_recommend": eg,
        "parallel_greet_and_harvest": False,
    }
    _merge_scheduling_into_jd(jd_path, fields)

    jf = sanitize_job_folder(jd_path.parent.name)
    pending_dir = str(jd_path.parent / "pending")
    wid = f"hr_recruitment_job_{jf}"

    try:
        from l3_node.local_memory import set_hr_recruitment_workflow_pointer

        set_hr_recruitment_workflow_pointer(
            wid,
            job_name=jn,
            job_folder=jf,
            jd_config_path=str(jd_path.resolve()),
            resume_pending_dir=pending_dir,
            lark_chat_id=None,
            scheduler_pending_confirm=True,
        )
    except Exception as e:
        logger.warning("更新 HR workflow 指针失败: %s", e)
        return json.dumps({"ok": False, "error": f"指针更新失败: {e}"}, ensure_ascii=False)

    body = _format_scheduler_confirm_lark_text(
        jn,
        greet_harvest_switch_interval_minutes=sw,
        greet_target=gt,
        max_count_per_harvest_tick=mch,
        analyze_threshold=at,
        resume_collect_target=rct,
        enable_greet_recommend=eg,
    )

    tech = {**fields, "max_count_per_harvest_tick": mch}

    lark_ok = False
    lark_detail: dict = {}
    try:
        from l3_node.channels.lark.hr_recruitment_notify import send_hr_recruitment_progress_message

        lark_detail = send_hr_recruitment_progress_message(
            body,
            technical_detail=json.dumps(tech, ensure_ascii=False),
            message_kind="hr_scheduler_confirm",
        )
        lark_ok = bool(lark_detail.get("ok") or lark_detail.get("status") == "success")
    except Exception as e:
        logger.warning("发送飞书调度确认消息失败: %s", e)
        lark_detail = {"error": str(e)}

    try:
        from l3_node.hr_audit_log import append_hr_recruitment_audit_event

        append_hr_recruitment_audit_event(
            "scheduler_confirm_sent",
            {"lark_sent": lark_ok, "lark_detail_keys": list(lark_detail.keys())},
            job_folder=jf,
            job_name=jn,
        )
    except Exception:
        pass

    return json.dumps(
        {
            "ok": True,
            "job_name": jn,
            "jd_config_path": str(jd_path.resolve()),
            "scheduler_pending_confirm": True,
            "lark_sent": lark_ok,
            "lark_detail": lark_detail,
            "defaults_applied": fields,
            "hint": "请 HR 在飞书回复「同意调度」启动定时任务；或在当前会话让助手调用 add_automated_recruitment_task。",
        },
        ensure_ascii=False,
    )


def start_scheduler_from_jd_pointer(job_name: str = "", jd_config_path: str = "") -> str:
    """
    从岗位名 / jd 路径调用插件内 add_automated_recruitment_task，启动 APScheduler。
    供飞书拦截器使用；参数以 jd.json 为准（见 hr_scheduler_send_confirm_prompt 写入的字段）。
    """
    jn = (job_name or "").strip()
    jdp = (jd_config_path or "").strip()
    if not jn and not jdp:
        return json.dumps({"ok": False, "error": "job_name 与 jd_config_path 不能同时为空"}, ensure_ascii=False)
    if not jn and jdp:
        try:
            raw = json.loads(Path(jdp).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                jn = (raw.get("job_title") or "").strip()
        except Exception as e:
            return json.dumps({"ok": False, "error": f"无法从 jd 解析 job_title: {e}"}, ensure_ascii=False)
    if not jn:
        return json.dumps({"ok": False, "error": "缺少 job_name"}, ensure_ascii=False)
    try:
        from .add_automated_recruitment_task import add_automated_recruitment_task

        return add_automated_recruitment_task(job_name=jn, jd_config_path=jdp)
    except Exception as e:
        logger.warning("start_scheduler_from_jd_pointer: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
