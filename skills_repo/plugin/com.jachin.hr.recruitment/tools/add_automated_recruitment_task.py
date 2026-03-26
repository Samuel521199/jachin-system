"""
MCP 工具：将岗位加入无人值守招聘调度引擎。

委托 l3_node.recruitment_scheduler（L3 内置或同包）。
数值类参数为 None 时优先从 jd.json 读取（与 hr_scheduler_send_confirm_prompt 写入的字段对齐）。
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .boss_utils import (
    canonicalize_boss_job_select,
    primary_job_title_from_boss_select_line,
    strip_leading_recruitment_verbs_for_job_chat,
)
from .hr_data_paths import (
    ensure_job_dirs_by_folder_key,
    get_job_jd_path_by_folder_key,
    init_job_jd_from_template,
    repair_jd_identity_dict,
    resolve_recruitment_data_folder_key,
    sanitize_job_folder,
)
from .jd_full_llm import ensure_jd_full_via_llm_sync

logger = logging.getLogger(__name__)


def _jd_int(doc: dict, key: str, default: int) -> int:
    v = doc.get(key)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _jd_bool(doc: dict, key: str, default: bool) -> bool:
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


def add_automated_recruitment_task(
    job_name: str,
    analyze_threshold: int | None = None,
    jd_config_path: str = "",
    enable_greet_recommend: bool | None = None,
    resume_collect_target: int | None = None,
    max_count_per_harvest_tick: int | None = None,
    greet_target: int | None = None,
    greet_harvest_switch_interval_minutes: int | None = None,
    recommend_interval_minutes: int | None = None,
    greet_only_total_target: int | None = None,
    greet_only_interval_minutes: int | None = None,
) -> str:
    """
    将岗位加入无人值守招聘调度引擎。
    委托 recruitment_scheduler.add_scheduled_job。

    各 int/bool 参数为 None 时从 jd.json 读取（若无则使用插件默认）。
    已不在 MCP 暴露 ``auto_analyze``：透析按简历份数阈值触发；jd.json 若仍有 ``auto_analyze=false`` 会继续生效。
    """
    jd_path = (jd_config_path or "").strip()
    jn = strip_leading_recruitment_verbs_for_job_chat((job_name or "").strip())
    if not jd_path:
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            jd_path = (get_hr_recruitment_workflow_pointer().get("jd_config_path") or "").strip()
        except Exception:
            pass
    if not jn:
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            jn = strip_leading_recruitment_verbs_for_job_chat(
                (get_hr_recruitment_workflow_pointer().get("job_name") or "").strip()
            )
        except Exception:
            pass
    jn = strip_leading_recruitment_verbs_for_job_chat(jn) if jn else jn

    jd_doc: dict = {}
    if jd_path and Path(jd_path).is_file():
        try:
            raw = json.loads(Path(jd_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                jd_doc, _rep = repair_jd_identity_dict(raw)
                if _rep:
                    try:
                        Path(jd_path).write_text(
                            json.dumps(jd_doc, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        logger.info("[add_automated_recruitment_task] 已持久化修复 jd 身份字段: %s", jd_path)
                    except Exception as _w:
                        logger.debug("持久化 repair jd 失败: %s", _w)
        except Exception as e:
            logger.debug("读取 jd.json 失败，使用参数默认: %s", e)

    if not jn:
        jn = strip_leading_recruitment_verbs_for_job_chat((jd_doc.get("job_title") or "").strip())
    if not jn:
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is not None and hasattr(rs, "get_recruitment_status_digest"):
                d = rs.get_recruitment_status_digest("")
                if isinstance(d, dict) and d.get("has_active_job") and d.get("job_name"):
                    jn = strip_leading_recruitment_verbs_for_job_chat(str(d["job_name"]).strip())
        except Exception:
            pass

    sel_raw = ((jd_doc.get("jd_select") or "").strip()) if jd_doc else ""
    canon_sel = (canonicalize_boss_job_select(sel_raw) or sel_raw).strip() if sel_raw else ""
    fk = resolve_recruitment_data_folder_key(
        jd_select_canon=canon_sel,
        job_title=jn,
        jd_doc=jd_doc if jd_doc else None,
    )
    if not fk and jn:
        from .hr_data_paths import infer_folder_key_from_job_display_name

        fk = infer_folder_key_from_job_display_name(jn, jd_doc if jd_doc else None)
    if not fk:
        return json.dumps(
            {
                "ok": False,
                "error": "无法解析岗位数据目录键：jd.json 须含完整 jd_select 或 job_title+城市+薪资，且磁盘上须能唯一定位该岗。",
            },
            ensure_ascii=False,
        )
    want_jd = str(get_job_jd_path_by_folder_key(fk))
    _path_mismatch = (not jd_path) or (Path(jd_path).resolve() != Path(want_jd).resolve())
    if _path_mismatch:
        if jd_path and Path(jd_path).is_file() and Path(jd_path).resolve() != Path(want_jd).resolve():
            logger.info(
                "[add_automated_recruitment_task] 数据目录键=%r，canonical jd=%s（指针/入参曾为 %s）",
                fk,
                want_jd,
                jd_path,
            )
            # HR 确认若只写了「岗位名目录」，而 jd_select 派生 canonical 键：把刚确认的 jd 拷到 canonical，避免读到空/旧文件
            if not Path(want_jd).is_file():
                try:
                    ensure_job_dirs_by_folder_key(fk)
                    shutil.copy2(jd_path, want_jd)
                    logger.info(
                        "[add_automated_recruitment_task] 已将 jd.json 迁移至 canonical：%s",
                        want_jd,
                    )
                except Exception as _mig:
                    logger.warning("[add_automated_recruitment_task] 迁移 jd 至 canonical 失败: %s", _mig)
        jd_path = want_jd
        if Path(jd_path).is_file():
            try:
                raw = json.loads(Path(jd_path).read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    jd_doc, _r2 = repair_jd_identity_dict(raw)
            except Exception:
                jd_doc = {}
        else:
            jd_doc = {}
    if not Path(jd_path).exists() and jn:
        ensure_job_dirs_by_folder_key(fk)
        _ov: dict = {"job_title": jn}
        if canon_sel:
            _ov["jd_select"] = canon_sel
        init_job_jd_from_template(jn, overrides=_ov, data_folder_key=fk)
        jd_path = str(get_job_jd_path_by_folder_key(fk))
        if Path(jd_path).is_file():
            try:
                ensure_jd_full_via_llm_sync(jd_path, jn, extra_context="")
            except Exception as e:
                logger.debug("add_automated_recruitment_task JD LLM 预生成跳过: %s", e)
        try:
            raw = json.loads(Path(jd_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                jd_doc = raw
        except Exception:
            pass
    elif Path(jd_path).is_file() and not jd_doc:
        try:
            raw = json.loads(Path(jd_path).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                jd_doc = raw
        except Exception:
            pass

    if not jn:
        sel_fallback = ((jd_doc.get("jd_select") or "").strip()) if jd_doc else ""
        if sel_fallback:
            cfb = (canonicalize_boss_job_select(sel_fallback) or sel_fallback).strip()
            jn = (primary_job_title_from_boss_select_line(cfb) or "").strip()
            jn = strip_leading_recruitment_verbs_for_job_chat(jn) if jn else jn

    if not jn:
        return json.dumps(
            {"ok": False, "error": "job_name 不能为空；请传岗位名，或确保 jd.json / workflow 指针中有 job_title"},
            ensure_ascii=False,
        )

    # 收网目标与透析阈值统一为同一「份数」（避免 HR 看到 5/10 与 3 阈值两套数）
    if resume_collect_target is not None:
        rct = int(resume_collect_target)
    elif analyze_threshold is not None:
        rct = int(analyze_threshold)
    elif jd_doc.get("resume_collect_target") is not None:
        rct = _jd_int(jd_doc, "resume_collect_target", _jd_int(jd_doc, "analyze_threshold", 4))
    else:
        rct = _jd_int(jd_doc, "analyze_threshold", 4)
    rct = max(1, min(9999, int(rct)))
    at = rct
    mch = int(max_count_per_harvest_tick) if max_count_per_harvest_tick is not None else _jd_int(
        jd_doc, "max_count", 50
    )
    gt = int(greet_target) if greet_target is not None else _jd_int(jd_doc, "greet_target", 3)
    if greet_harvest_switch_interval_minutes is not None:
        sw = int(greet_harvest_switch_interval_minutes)
    elif recommend_interval_minutes is not None:
        sw = int(recommend_interval_minutes)
    elif jd_doc.get("greet_harvest_switch_interval_minutes") is not None:
        sw = _jd_int(jd_doc, "greet_harvest_switch_interval_minutes", 10)
    else:
        sw = _jd_int(jd_doc, "recommend_interval_minutes", 10)
    sw = max(1, min(120, sw))
    rim = (
        int(recommend_interval_minutes)
        if recommend_interval_minutes is not None
        else _jd_int(jd_doc, "recommend_interval_minutes", sw)
    )
    rim = max(1, min(120, int(rim)))
    # jd 未写该键时与 hr_scheduler_send_confirm_prompt / init_job_jd_from_template 一致：默认开「推荐↔收网」交替
    eg = bool(enable_greet_recommend) if enable_greet_recommend is not None else _jd_bool(
        jd_doc, "enable_greet_recommend", True
    )
    aa = _jd_bool(jd_doc, "auto_analyze", True)
    go_total = (
        int(greet_only_total_target)
        if greet_only_total_target is not None
        else _jd_int(jd_doc, "greet_only_total_target", 0)
    )
    go_total = max(0, go_total)
    go_interval = greet_only_interval_minutes
    if go_interval is None and jd_doc.get("greet_only_interval_minutes") is not None:
        go_interval = _jd_int(jd_doc, "greet_only_interval_minutes", 0)
    if go_interval is not None:
        go_interval = max(0, min(120, int(go_interval)))

    try:
        from recruitment_scheduler import add_scheduled_job

        cfg = {
            "job_name": jn,
            "job_folder": fk,
            "jd_config_path": jd_path,
            "jd_path": jd_path,
            "analyze_threshold": at,
            "request_resume": True,
            "enable_greet_recommend": eg,
            "parallel_greet_and_harvest": False,
            "auto_analyze": aa,
            "resume_collect_target": rct,
            "max_count": mch,
            "greet_target": gt,
            "greet_harvest_switch_interval_minutes": sw,
            "recommend_interval_minutes": rim,
        }
        if go_total > 0:
            cfg["greet_only_total_target"] = go_total
            if go_interval and go_interval > 0:
                cfg["greet_only_interval_minutes"] = go_interval
        result = add_scheduled_job(cfg)
        if isinstance(result, dict) and result.get("ok") and jd_path and Path(jd_path).exists():
            try:
                raw = json.loads(Path(jd_path).read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw.pop("harvest_delay_seconds", None)
                    for k in (
                        "enable_greet_recommend",
                        "parallel_greet_and_harvest",
                        "analyze_threshold",
                        "resume_collect_target",
                        "auto_analyze",
                        "max_count",
                        "greet_target",
                        "greet_harvest_switch_interval_minutes",
                        "recommend_interval_minutes",
                    ):
                        if k in cfg:
                            raw[k] = cfg[k]
                    raw["job_title"] = jn
                    raw["data_folder_key"] = fk
                    if go_total > 0:
                        raw["greet_only_total_target"] = go_total
                        if go_interval and go_interval > 0:
                            raw["greet_only_interval_minutes"] = go_interval
                    else:
                        raw.pop("greet_only_total_target", None)
                        raw.pop("greet_only_interval_minutes", None)
                    Path(jd_path).write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception as e:
                logger.debug("回写 jd.json 调度字段失败: %s", e)
        return json.dumps(result, ensure_ascii=False)
    except ImportError as e:
        logger.warning("recruitment_scheduler 未加载: %s", e)
        return json.dumps({"ok": False, "error": f"调度器不可用: {e}"}, ensure_ascii=False)
    except Exception as e:
        logger.warning("add_automated_recruitment_task 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
