"""
L3 招聘心脏起搏器 - 基于 APScheduler 的全自动无人值守守护进程

将单次招聘任务解耦为定时 Job + 规则引擎：
  - 默认（未开并行）：**单一交替任务** `rec_{岗位}_alternate`，每 `greet_harvest_switch_interval_minutes`（默认 10）
    在「推荐牛人打招呼」与「沟通收简历」之间严格轮换；
  - **仅打招呼**：`greet_only_total_target > 0` 时只注册 `rec_{岗位}_greet_only`，累计成功打招呼达标后移除任务并发飞书询问是否收网（无收网、无 `job_check`）；
  - 已废弃「并行双 Job」：Boss 单页仅注册 **一条** 浏览器侧任务（与 chrome_lock 一致）；
  - 规则引擎：`rec_{岗位}_check` 每分钟检查（**非**仅打招呼模式），按简历份数阈值触发 Wasm 透析。
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("l3_node")

try:
    from l3_node.log_broadcaster import broadcast_log
except ImportError:
    def broadcast_log(msg: str, level: str = "INFO") -> None:
        pass

HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"

try:
    from l3_node.execution_resilience import (
        ERROR_PER_ITEM,
        ERROR_PERMANENT,
        build_run_report,
        classify_wasm_error_message,
        log_execution_brief,
        write_run_report_json,
    )
except ImportError:
    ERROR_PER_ITEM = "per_item"
    ERROR_PERMANENT = "permanent"

    def classify_wasm_error_message(msg: str) -> str:
        return "transient"

    def build_run_report(**kwargs):  # type: ignore[misc]
        return {**kwargs}

    def write_run_report_json(output_dir: Path, report: dict[str, Any]) -> None:
        pass

    def log_execution_brief(**kwargs: Any) -> None:
        logger.warning("[ExecutionBrief] fallback %s", kwargs)


def _wasm_return_indicates_failure(wr: Any) -> bool:
    if not wr or not isinstance(wr, str):
        return False
    s = wr.strip()
    return bool(s.startswith("[Wasm") or "Wasm 执行失败" in s or "execute ABI" in s)


def _lark_notify_hr_analysis(
    title: str,
    job_name: str,
    detail: str,
    *,
    human_line: str | None = None,
) -> None:
    """简历 AI 分析关键节点通知飞书：正文用人话；detail 仅写日志（失败不影响调度）。"""
    jn = (job_name or "").strip() or "本岗位"
    tech = (detail or "").strip()
    if tech:
        logger.info("[Scheduler][Lark HR] title=%s job=%s 技术明细:\n%s", title, jn, tech[:12000])

    if human_line and str(human_line).strip():
        msg = str(human_line).strip()
    else:
        t = (title or "").strip()
        if t == "开始分析":
            msg = f"【{jn}】AI 已开始阅读本批简历，请稍等几分钟。"
        elif t == "分析完成":
            msg = f"【{jn}】本批简历已分析完，推荐结果会更新到汇总（若已接飞书表也会同步）。"
        elif t == "分析无产出":
            msg = f"【{jn}】这次没能从简历里跑出有效评价，已记录。可稍后再说「分析简历」。"
        elif t == "分析失败":
            msg = f"【{jn}】读简历时出了点问题，已记录。可说「分析简历」再试。"
        else:
            msg = f"【{jn}】{t}（详情见系统日志）"
    try:
        from l3_node.channels.lark.hr_recruitment_notify import send_hr_recruitment_progress_message

        send_hr_recruitment_progress_message(
            msg,
            technical_detail=tech if tech else None,
            message_kind="hr_resume_analysis",
        )
    except Exception as e:
        logger.debug("[Scheduler] 简历分析 Lark 通知跳过: %s", e)


def _get_hr_recruitment_plugin_root() -> Path | None:
    """HR 招聘 MCP 包根目录。本模块在 HR 包内，包根即 recruitment_scheduler.py 所在目录。"""
    hr_root = Path(__file__).resolve().parent
    if (hr_root / "plugin.json").exists() or (hr_root / "tools" / "atom_inbox_harvester.py").exists():
        return hr_root
    return None


def _ensure_hr_plugin_on_sys_path() -> None:
    """保证包内同级模块（recruitment_task、hr_analysis_persist）可被 import。

    L3 通过 hr_loader 动态加载本模块时会在 import 后恢复 sys.path；
    job_check_and_analyze 等路径上延迟执行的 ``from recruitment_task import ...`` 须依赖本函数补回包根。
    """
    hr_root = _get_hr_recruitment_plugin_root()
    if not hr_root:
        return
    cache_str = str(hr_root.resolve())
    if cache_str not in sys.path:
        sys.path.insert(0, cache_str)


def _load_atom_inbox_harvester_full_flow():
    """从本包加载 atom_inbox_harvester（统一 HR MCP 代码根）。"""
    hr_root = _get_hr_recruitment_plugin_root()
    if not hr_root:
        raise ImportError(
            "HR 招聘 MCP 包未找到。请从 L1 订阅 com.jachin.hr.recruitment 并下载到 l3_mcp_cache，"
            "或确保 skills_repo/plugin/com.jachin.hr.recruitment 存在。"
        )
    harvester_py = hr_root / "tools" / "atom_inbox_harvester.py"
    if not harvester_py.exists():
        raise ImportError(f"atom_inbox_harvester 未找到: {harvester_py}")
    cache_str = str(hr_root.resolve())
    if cache_str not in sys.path:
        sys.path.insert(0, cache_str)
    from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow
    return atom_inbox_harvester_full_flow


def _get_hr_data_root() -> Path:
    """招聘数据根目录：永远落在用户目录 ~/.jachin/workspace/hr_recruitment（或 JACHIN_HR_DATA_ROOT），禁止回退到仓库 skills_repo/plugin/data。"""
    hr_root = _get_hr_recruitment_plugin_root()
    if hr_root and (hr_root / "tools" / "config.py").exists():
        cache_str = str(hr_root.resolve())
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        try:
            from tools.config import get_data_root

            return get_data_root()
        except Exception:
            pass
    jroot = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).expanduser().resolve()
    custom = os.environ.get("JACHIN_HR_DATA_ROOT", "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        return p if p.is_absolute() else (jroot / p)
    return jroot / "workspace" / "hr_recruitment"


def _get_scheduler_state_dir() -> Path:
    """调度器状态目录：~/.jachin/workspace/hr_recruitment/hr_analysis（与 tools.config 一致），禁止回退到项目 data/。"""
    hr_root = _get_hr_recruitment_plugin_root()
    if hr_root and (hr_root / "tools" / "config.py").exists():
        cache_str = str(hr_root.resolve())
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        try:
            from tools.config import get_scheduler_state_dir

            return get_scheduler_state_dir()
        except Exception:
            pass
    return _get_hr_data_root() / "hr_analysis"


PLUGIN_DATA_ROOT = _get_hr_data_root()


def _effective_greet_harvest_switch_minutes(cfg: dict[str, Any]) -> int:
    """推荐↔沟通收简历交替周期（分钟）；兼容旧 jd 仅有 recommend_interval_minutes。"""
    for key in ("greet_harvest_switch_interval_minutes",):
        v = cfg.get(key)
        if v is not None:
            try:
                m = int(v)
                return max(1, min(120, m))
            except (TypeError, ValueError):
                pass
    v2 = cfg.get("recommend_interval_minutes")
    if v2 is not None:
        try:
            return max(1, min(120, int(v2)))
        except (TypeError, ValueError):
            pass
    return 10


def _remove_greet_harvest_browser_jobs(job_folder: str) -> None:
    """移除推荐/收网/交替 三类浏览器任务（保留 check）。"""
    if not _APSCHEDULER_AVAILABLE or scheduler is None:
        return
    jf = (job_folder or "").strip()
    if not jf:
        return
    for jid in (
        f"rec_{jf}_recommend",
        f"rec_{jf}_harvest",
        f"rec_{jf}_alternate",
        f"rec_{jf}_greet_only",
    ):
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass


def _remove_greet_only_job(job_folder: str) -> None:
    if not _APSCHEDULER_AVAILABLE or scheduler is None:
        return
    jf = (job_folder or "").strip()
    if not jf:
        return
    try:
        scheduler.remove_job(f"rec_{jf}_greet_only")
    except Exception:
        pass


def _jd_clear_greet_only_campaign_keys(job_folder: str, jd_config_path: str = "") -> None:
    p = Path(jd_config_path) if (jd_config_path and Path(jd_config_path).is_file()) else PLUGIN_DATA_ROOT / job_folder / "jd.json"
    if not p.is_file():
        return
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return
        doc.pop("greet_only_total_target", None)
        doc.pop("greet_only_interval_minutes", None)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("[Scheduler] 清除 jd 仅打招呼字段失败: %s", e)


def _jd_write_greet_only_campaign(
    job_folder: str, jd_config_path: str, total: int, interval_minutes: int
) -> None:
    p = Path(jd_config_path) if (jd_config_path and Path(jd_config_path).is_file()) else PLUGIN_DATA_ROOT / job_folder / "jd.json"
    if not p.is_file():
        return
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
        doc["greet_only_total_target"] = int(total)
        doc["greet_only_interval_minutes"] = max(1, min(120, int(interval_minutes)))
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[Scheduler] 写入 jd 仅打招呼字段失败: %s", e)


def _notify_lark_greet_only_finished(
    job_name: str, done: int, target: int, *, early: bool = False
) -> None:
    try:
        from l3_node.channels.lark.hr_recruitment_notify import send_hr_recruitment_progress_message

        jn = (job_name or "").strip() or "当前岗位"
        if early:
            body = (
                f"【{jn}】定时**仅打招呼**已提前结束：本轮累计成功打招呼 **{done}** 次"
                f"（计划共 **{target}** 次）。已连续多轮未成功打到人，任务已自动停止。\n\n"
                "若仍需凑满次数，可检查 Boss 推荐页是否还有候选人，或稍后再发：**仅打招呼 N**（N 为次数）。\n\n"
                "如需在沟通里**收网抓简历**，请说明份数，例如：**收网40份**、**再抓 30 份**；"
                "需要**推荐↔收简历**完整无人值守时，请 **同意调度** 或由同事注册交替任务。"
            )
        else:
            body = (
                f"【{jn}】定时**仅打招呼**已完成：累计成功打招呼 **{done}** / **{target}** 次，本任务已结束。\n\n"
                "如需在沟通里**收网抓简历**，请直接说明要抓**多少份**，例如：**收网40份**、**再抓 30 份**；"
                "需要**推荐牛人↔沟通收简历**交替跑起来，请回复 **同意调度** 或让同事调用无人值守注册。"
            )
        send_hr_recruitment_progress_message(
            body,
            technical_detail=f"greet_only_done={done} target={target} early={early}",
            message_kind="hr_greet_only_done",
        )
    except Exception as e:
        logger.debug("[Scheduler] 仅打招呼完成飞书通知跳过: %s", e)


def _finish_greet_only_campaign(
    job_folder: str,
    job_name: str,
    target_total: int,
    cfg: dict[str, Any],
    *,
    early: bool = False,
    done_override: int | None = None,
) -> None:
    _remove_greet_only_job(job_folder)
    st = _load_task_state()
    jk = st.setdefault(job_folder, {})
    done = done_override if done_override is not None else int(jk.get("hr_greet_only_done", 0) or 0)
    jk.pop("hr_greet_only_done", None)
    jk.pop("hr_greet_only_zero_streak", None)
    jk["hr_greet_only_last_finished"] = {
        "target": int(target_total),
        "done": int(done),
        "early": bool(early),
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    last = (st.get("_last_job_configs") or {}).get(job_folder)
    if isinstance(last, dict):
        last = dict(last)
        last.pop("greet_only_total_target", None)
        last.pop("greet_only_interval_minutes", None)
        st.setdefault("_last_job_configs", {})[job_folder] = last
    _save_task_state(st)
    _jd_clear_greet_only_campaign_keys(job_folder, str(cfg.get("jd_config_path") or ""))
    _notify_lark_greet_only_finished(job_name, done, target_total, early=early)
    _hr_audit(
        "greet_only_campaign_finished",
        job_folder=job_folder,
        job_name=(job_name or "").strip() or job_folder,
        detail={"done": done, "target": target_total, "early": early},
    )


def incomplete_greet_only_snapshot(job_folder: str) -> dict[str, Any] | None:
    """
    若该岗位存在「仅打招呼」**未完成**进度（已累计成功次数 >0 且 < 目标，且定时未在跑），返回摘要 dict；
    否则 None。供飞书 / Agent 提示续接或重开。
    """
    jf = (job_folder or "").strip()
    if not jf:
        return None
    st = _load_task_state()
    jk = st.get(jf)
    if not isinstance(jk, dict):
        return None
    done = int(jk.get("hr_greet_only_done", 0) or 0)
    if done <= 0:
        return None

    target = 0
    interval_m = 0
    jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
    if jd_path.is_file():
        try:
            doc = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                if doc.get("greet_only_total_target") is not None:
                    target = int(doc.get("greet_only_total_target") or 0)
                if doc.get("greet_only_interval_minutes") is not None:
                    interval_m = int(doc.get("greet_only_interval_minutes") or 0)
        except Exception:
            pass

    cfg = (st.get("_last_job_configs") or {}).get(jf)
    if isinstance(cfg, dict):
        if target <= 0 and cfg.get("greet_only_total_target") is not None:
            target = int(cfg.get("greet_only_total_target") or 0)
        if interval_m <= 0 and cfg.get("greet_only_interval_minutes") is not None:
            interval_m = int(cfg.get("greet_only_interval_minutes") or 0)

    if target <= 0 or done >= target:
        return None

    if _APSCHEDULER_AVAILABLE and scheduler is not None:
        try:
            if scheduler.get_job(f"rec_{jf}_greet_only") is not None:
                return None
        except Exception:
            pass

    rim = max(1, min(120, interval_m)) if interval_m > 0 else 0
    return {
        "target": target,
        "done": done,
        "remaining": target - done,
        "interval_minutes": rim,
    }


def incomplete_greet_only_for_pointer() -> dict[str, Any] | None:
    """基于 ``hr_recruitment_workflow_pointer`` 当前岗位的 ``incomplete_greet_only_snapshot``。"""
    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        jn = (get_hr_recruitment_workflow_pointer().get("job_name") or "").strip()
    except Exception:
        jn = ""
    if not jn:
        return None
    return incomplete_greet_only_snapshot(_resolve_hr_data_job_folder(jn))


def _modify_alternate_next_run(job_folder: str, *, seconds: int) -> None:
    """将 ``rec_{folder}_alternate`` 下次触发提前到约 ``seconds`` 秒后（用于达招呼上限/沟通列表空时不必等满间隔）。"""
    if not _APSCHEDULER_AVAILABLE or scheduler is None:
        return
    jf = (job_folder or "").strip()
    if not jf:
        return
    jid = f"rec_{jf}_alternate"
    try:
        if scheduler.get_job(jid) is None:
            return
        sec = max(2, int(seconds))
        scheduler.modify_job(jid, next_run_time=datetime.now() + timedelta(seconds=sec))
        logger.info("[Scheduler] 已提前安排下一轮交替 job=%s in %ss", jid, sec)
    except Exception as e:
        logger.warning("[Scheduler] modify_job 提前交替失败: %s", e)


# 兼容全角/半角冒号、多余空白；LLM 有时输出 姓名: 或 姓名：
RE_SUMMARY_PASS = re.compile(
    r"---SUMMARY_PASS---\s*姓名[：:]\s*(.*?)\s*得分[：:]\s*(.*?)\s*核心优势[：:]\s*(.*?)\s*---SUMMARY_PASS---",
    re.DOTALL,
)
RE_SUMMARY_REJECT = re.compile(
    r"---SUMMARY_REJECT---\s*姓名[：:]\s*(.*?)\s*得分[：:]\s*(.*?)\s*淘汰原因[：:]\s*(.*?)\s*---SUMMARY_REJECT---",
    re.DOTALL,
)
# 兜底：从报告标题或正文提取候选人姓名（当 LLM 未输出 SUMMARY 块时）
RE_REPORT_TITLE = re.compile(r"(?:候选人评估报告|评估报告)[：:]\s*([^\n]+)", re.IGNORECASE)
RE_SCORE_IN_REPORT = re.compile(r"(?:总计|综合得分|得分)[：:]\s*[^\d]*(\d+\.?\d*)", re.IGNORECASE)


def _extract_candidate_fields(report: str) -> dict:
    """从 HR 分析报告中提取学历、经验、薪资等字段"""
    r = (report or "").strip()
    out = {"education": "-", "experience": "-", "salary": "-"}
    if not r:
        return out
    # 学历：本科|硕士|博士|大专|专科
    for pat in [r"学历[：:]\s*([^\n]+)", r"(本科|硕士|博士|大专|专科|研究生)[^\n]*"]:
        m = re.search(pat, r)
        if m:
            out["education"] = (m.group(1) or "").strip()[:20]
            break
    # 经验：x年 或 x-y年
    for pat in [r"经验[：:]\s*([^\n]+)", r"(\d+[-~]?\d*)\s*年", r"工作[^\n]*?(\d+[-~]?\d*)\s*年"]:
        m = re.search(pat, r)
        if m:
            out["experience"] = (m.group(1) or "").strip()[:30]
            break
    # 薪资：期望 x-yK 或 月薪
    for pat in [r"薪资[：:]\s*([^\n]+)", r"期望[^\n]*?(\d+[-~]?\d*)[kK]", r"(\d+[-~]\d+)[kK]"]:
        m = re.search(pat, r)
        if m:
            out["salary"] = (m.group(1) or "").strip()[:20]
            break
    return out


def _score_to_stars(score: float) -> str:
    """将得分转为星级，如 4.2 -> ★★★★☆"""
    n = max(0, min(5, round(score)))
    return "★" * n + "☆" * (5 - n)

# APScheduler 心脏起搏器
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.start()
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    scheduler = None
    _APSCHEDULER_AVAILABLE = False

# 全局 Chrome 浏览器排他锁，防止多个 RPA 脚本抢夺同一个浏览器控制权
chrome_lock = threading.Lock()

# 同一岗位 job_check_and_analyze 互斥：避免「飞书连发分析 + 定时 tick + 立即触发」并发跑两轮 Wasm
_job_check_folder_locks: dict[str, threading.Lock] = {}
_job_check_locks_mutex = threading.Lock()


def _job_check_lock_for_folder(job_folder: str) -> threading.Lock:
    jf = (job_folder or "").strip() or "_default"
    with _job_check_locks_mutex:
        if jf not in _job_check_folder_locks:
            _job_check_folder_locks[jf] = threading.Lock()
        return _job_check_folder_locks[jf]

# 任务状态持久化：记录每个岗位的最后分析时间（优先 ~/.jachin）
_DATA_HR = _get_scheduler_state_dir()
TASK_STATE_FILE = _DATA_HR / "scheduler_state.json"

# 全局招聘停止标志：HR 说「停止招聘」后设为 True，所有定时任务在开始时检查并立即退出
_recruitment_stopped_global = False
_recruitment_stopped_lock = threading.Lock()


def set_recruitment_stopped(stopped: bool = True) -> None:
    """设置/清除招聘全局停止标志。HR 说停止时设为 True，阻止后续定时任务执行。"""
    global _recruitment_stopped_global
    with _recruitment_stopped_lock:
        _recruitment_stopped_global = stopped
    logger.info("[Scheduler] 招聘停止标志已%s", "开启" if stopped else "清除")


def is_recruitment_stopped() -> bool:
    """检查招聘是否已被 HR 要求停止。定时任务在入口处调用，若为 True 则立即返回。"""
    with _recruitment_stopped_lock:
        return _recruitment_stopped_global


def _ensure_data_dir() -> None:
    _DATA_HR.mkdir(parents=True, exist_ok=True)
    if not TASK_STATE_FILE.exists():
        TASK_STATE_FILE.write_text("{}", encoding="utf-8")


def _load_task_state() -> dict[str, Any]:
    _ensure_data_dir()
    try:
        return json.loads(TASK_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_task_state(state: dict[str, Any]) -> None:
    _ensure_data_dir()
    TASK_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _hr_audit(event: str, *, job_folder: str = "", job_name: str = "", detail: dict[str, Any] | None = None) -> None:
    try:
        from l3_node.hr_audit_log import append_hr_recruitment_audit_event

        append_hr_recruitment_audit_event(event, detail or {}, job_folder=job_folder, job_name=job_name)
    except Exception:
        pass


def _persist_last_job_config(job_folder: str, job_config: dict[str, Any]) -> None:
    """保存上次无人值守参数，供飞书「继续」时恢复定时任务。"""
    jf = (job_folder or "").strip()
    if not jf or not job_config:
        return
    try:
        cfg = json.loads(json.dumps(job_config, default=str))
        state = _load_task_state()
        state.setdefault("_last_job_configs", {})[jf] = cfg
        _save_task_state(state)
        logger.debug("[Scheduler] 已持久化 job_config job_folder=%s", jf)
    except Exception as e:
        logger.warning("[Scheduler] 持久化 job_config 失败: %s", e)


def _write_jd_scheduling_snapshot_from_cfg(
    cfg: dict[str, Any], *, only_resume_analyze_caps: bool = False
) -> None:
    """
    将 cfg 中的调度数字写回 ``jd_config_path`` 指向的 jd.json。

    飞书简报 ``get_harvest_progress_snapshot`` **优先读 jd.json**；若只改 scheduler_state /
    APScheduler 闭包而不回写 jd，会出现「再抓 N 份已成功、进度行仍显示旧上限」的割裂。
    ``resume_collect_target`` 与 ``analyze_threshold`` 在本链路中为同一「累计收网 = 透析触发份数」口径。

    ``only_resume_analyze_caps=True``：仅写回这两项（用于「再抓 N 份」，避免 cfg 缺字段时误覆盖其它 jd 数字）。
    """
    raw_jdp = str(cfg.get("jd_config_path") or "").strip()
    jd_p = Path(raw_jdp) if raw_jdp else Path()
    if not raw_jdp or not jd_p.is_file():
        jf0 = str(cfg.get("job_folder") or "").strip()
        if jf0:
            jd_p = PLUGIN_DATA_ROOT / jf0 / "jd.json"
    if not jd_p.is_file():
        return
    try:
        jd_doc = json.loads(jd_p.read_text(encoding="utf-8"))
        if not isinstance(jd_doc, dict):
            return
        if only_resume_analyze_caps:
            if cfg.get("resume_collect_target") is not None:
                jd_doc["resume_collect_target"] = int(cfg["resume_collect_target"])
            if cfg.get("analyze_threshold") is not None:
                jd_doc["analyze_threshold"] = int(cfg["analyze_threshold"])
            jd_p.write_text(json.dumps(jd_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        jd_doc["max_count"] = int(cfg.get("max_count", 50))
        jd_doc["greet_target"] = int(cfg.get("greet_target", 3))
        jd_doc["recommend_interval_minutes"] = int(cfg.get("recommend_interval_minutes", 15))
        jd_doc["greet_harvest_switch_interval_minutes"] = int(
            cfg.get("greet_harvest_switch_interval_minutes")
            or _effective_greet_harvest_switch_minutes(cfg)
        )
        if cfg.get("resume_collect_target") is not None:
            jd_doc["resume_collect_target"] = int(cfg["resume_collect_target"])
        if cfg.get("analyze_threshold") is not None:
            jd_doc["analyze_threshold"] = int(cfg["analyze_threshold"])
        jd_p.write_text(json.dumps(jd_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("[Scheduler] 写回 jd.json 调度字段失败: %s", e)


# APScheduler job id 形如 rec_{job_folder}_{suffix}；suffix 按最长优先剥離（避免 job_folder 含下划线时误截）
_REC_SCHEDULER_JOB_ID_SUFFIXES: tuple[str, ...] = (
    "_greet_only",
    "_alternate",
    "_recommend",
    "_harvest",
    "_check",
)


def _job_folder_from_rec_scheduler_job_id(jid: str) -> str:
    if not jid or not str(jid).startswith("rec_"):
        return ""
    rest = str(jid)[4:]
    for suf in _REC_SCHEDULER_JOB_ID_SUFFIXES:
        if rest.endswith(suf):
            raw = rest[: -len(suf)]
            return _sanitize_job_folder(raw) if raw else ""
    return ""


def _enumerate_active_recruitment_job_folders() -> set[str]:
    """当前进程内 APScheduler 中已注册的 ``rec_*`` 任务对应的 job_folder 集合。"""
    if not _APSCHEDULER_AVAILABLE or scheduler is None:
        return set()
    out: set[str] = set()
    try:
        for j in scheduler.get_jobs():
            jid = getattr(j, "id", "") or ""
            jf = _job_folder_from_rec_scheduler_job_id(jid)
            if jf:
                out.add(jf)
    except Exception as e:
        logger.debug("[Scheduler] 枚举 rec 任务 job_folder 跳过: %s", e)
    return out


def _apply_preempt_suspend_marks_before_switch(new_job_folder: str) -> None:
    """
    在 ``remove_all_recruitment_apscheduler_jobs`` 之前调用：
    对「当前仍在跑 rec_*」且 **不等于** 即将启动的新岗目录 的 folder 写入 ``scheduler_suspended``，
    便于换岗后列出挂起项并按岗恢复（事实来源：scheduler_state.json）。
    """
    new_jf = _sanitize_job_folder((new_job_folder or "").strip())
    if not new_jf:
        return
    active = _enumerate_active_recruitment_job_folders()
    if not active:
        return
    state = _load_task_state()
    changed = False
    ts = time.time()
    for old_jf in active:
        if old_jf == new_jf:
            continue
        bucket = state.setdefault(old_jf, {})
        if not isinstance(bucket, dict):
            continue
        bucket["scheduler_suspended"] = {
            "reason": "preempted_by_switch",
            "preempted_by_folder": new_jf,
            "at": ts,
        }
        changed = True
    if changed:
        _save_task_state(state)
        logger.info(
            "[Scheduler] 换岗抢占：已写入挂起标记 preempted=%s -> new=%s",
            sorted(x for x in active if x != new_jf),
            new_jf,
        )
        _hr_audit(
            "scheduler_preempt_suspend",
            job_folder=new_jf,
            job_name=new_jf,
            detail={"preempted_folders": sorted(x for x in active if x != new_jf), "new_folder": new_jf},
        )


def _clear_scheduler_suspended_mark(job_folder: str) -> None:
    """新岗成功注册或按岗恢复后清除该目录键上的挂起标记。"""
    jf = _sanitize_job_folder((job_folder or "").strip())
    if not jf:
        return
    state = _load_task_state()
    bucket = state.get(jf)
    if not isinstance(bucket, dict) or "scheduler_suspended" not in bucket:
        return
    try:
        del bucket["scheduler_suspended"]
        _save_task_state(state)
    except Exception as e:
        logger.debug("[Scheduler] 清除 scheduler_suspended 跳过: %s", e)


def _has_scheduler_jobs_for_folder(job_folder: str) -> bool:
    if not _APSCHEDULER_AVAILABLE or not job_folder:
        return False
    prefix = f"rec_{job_folder}_"
    try:
        for j in scheduler.get_jobs():
            jid = j.id or ""
            if jid.startswith(prefix):
                return True
    except Exception:
        pass
    return False


def _has_greet_harvest_scheduler_jobs(job_folder: str) -> bool:
    """
    是否存在 Boss 侧「打招呼 / 收网 / 交替 / 仅打招呼」定时任务。

    「停止收网」会删掉 alternate/harvest 等但 **保留** ``rec_{岗}_check``（透析轮询）；
    ``_has_scheduler_jobs_for_folder`` 会把仅余 check 误判为「调度仍在跑」，导致飞书「继续」无法恢复主循环。
    """
    if not _APSCHEDULER_AVAILABLE or not (job_folder or "").strip():
        return False
    jf = job_folder.strip()
    wanted = {
        f"rec_{jf}_alternate",
        f"rec_{jf}_harvest",
        f"rec_{jf}_recommend",
        f"rec_{jf}_greet_only",
    }
    try:
        existing = {j.id for j in scheduler.get_jobs() if getattr(j, "id", None)}
        return bool(wanted & existing)
    except Exception:
        return False


def _pointer_hr_job_folder() -> str:
    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        ptr = get_hr_recruitment_workflow_pointer()
        jf = (ptr.get("primary_job_folder") or ptr.get("job_folder") or "").strip()
        if jf:
            return _sanitize_job_folder(jf)
    except Exception:
        pass
    return ""


def _job_folder_from_jd_config_path_hint(hint: str) -> str:
    """
    当显式传入某条 ``jd.json`` 绝对路径时，解析其在 ``PLUGIN_DATA_ROOT`` 下的单段目录键。

    用于飞书「同意」刚落盘新岗 jd、但 ``primary_job_folder`` 仍指向旧岗时：
    ``_resolve_hr_data_job_folder`` 会优先返回指针目录，导致误判 ``scheduler_active`` 而跳过发帖。
    """
    raw = (hint or "").strip()
    if not raw:
        return ""
    try:
        p = Path(raw).resolve()
        if not p.is_file() or p.name.lower() != "jd.json":
            return ""
        job_root = p.parent.resolve()
        rel = job_root.relative_to(PLUGIN_DATA_ROOT.resolve())
    except (OSError, ValueError):
        return ""
    if len(rel.parts) != 1:
        return ""
    return rel.parts[0]


def _resolve_hr_data_job_folder(job_name: str = "") -> str:
    """
    从 workflow 指针 + 岗位显示名解析 ``PLUGIN_DATA_ROOT/{job_folder}/``。

    与 ``get_harvest_progress_snapshot`` 在仅传 job_name 时的语义对齐，并**优先**采用指针里的
    ``jd_config_path``（真实目录常为「职位+城市+薪资」，而非 ``_sanitize_job_folder(job_title)``）。

    删除遗留的「纯岗位名」目录后，若仍只用 sanitize 标题会找不到 ``jd.json`` / ``_last_job_configs``，
    导致飞书「再抓 N 份」等短指令误报无配置。
    """
    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        ptr = get_hr_recruitment_workflow_pointer()
    except Exception:
        ptr = {}

    jn = (job_name or "").strip()
    if not jn:
        jn = (ptr.get("job_name") or "").strip()

    jdp = (ptr.get("jd_config_path") or "").strip()
    if jdp:
        p = Path(jdp)
        if p.is_file() and p.name.lower() == "jd.json":
            try:
                job_root = p.parent.resolve()
                if job_root.parent.resolve() == PLUGIN_DATA_ROOT.resolve():
                    fk = _sanitize_job_folder(job_root.name)
                    if fk and (job_root / "jd.json").is_file():
                        return fk
            except OSError:
                pass

    try:
        from tools.boss_utils import strip_leading_recruitment_verbs_for_job_chat

        jn_stripped = strip_leading_recruitment_verbs_for_job_chat(jn) if jn else ""
    except Exception:
        jn_stripped = jn

    ptr_jf = _pointer_hr_job_folder()

    if not jn_stripped:
        if ptr_jf:
            pj = PLUGIN_DATA_ROOT / ptr_jf
            if pj.is_dir() and ((pj / "jd.json").is_file() or (pj / "pending").is_dir()):
                return ptr_jf
        return ptr_jf or ""

    if ptr_jf:
        pj = PLUGIN_DATA_ROOT / ptr_jf
        if pj.is_dir() and ((pj / "jd.json").is_file() or (pj / "pending").is_dir()):
            return ptr_jf
    try:
        from tools.hr_data_paths import infer_folder_key_from_job_display_name

        inf = infer_folder_key_from_job_display_name(jn_stripped)
        if inf:
            return inf
    except Exception:
        pass
    return ""


def get_harvest_progress_snapshot(job_name: str = "", job_folder: str = "") -> tuple[int, int]:
    """
    返回 (n, cap)，用于飞书「抓取简历 n/m」一行：

    - **n**：``hr_recruitment/{岗位}/pending`` 下 **所有 .pdf 文件个数**（递归），与是否已透析无关；
      多轮收网、重复下载、未删旧文件都会累加，**不是**「去重后的候选人数」。
    - **cap**：优先读该岗位 ``jd.json`` 的 ``resume_collect_target`` / ``analyze_threshold``（与 HR 改目标一致）；
      若 jd 未写有效值，再回退 ``scheduler_state.json`` 里上次持久化的配置。

    与 **待透析估算**（``_count_unprocessed_pdfs`` = PDF 数 − result 下分析报告数）是两套口径，勿混用。
    job_name 为空时从 HR workflow 指针读取。

    **job_folder**：显式数据目录键（与 ``add_scheduled_job`` 持久化的 ``job_folder`` 一致）。传入时优先使用，
    避免「收网 tick 标题是 A 岗、n/cap 却仍按指针读 B 岗目录」的串岗。
    当传入 **job_name** 且能解析到本地目录时，**不再**优先采用指针目录（除非该岗目录尚不存在）。
    """
    jn = (job_name or "").strip()
    if not jn:
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            jn = (get_hr_recruitment_workflow_pointer().get("job_name") or "").strip()
        except Exception:
            pass
    jf = (job_folder or "").strip()
    if jf:
        jf = _sanitize_job_folder(jf)
    elif jn:
        jf = _resolve_hr_data_job_folder(jn)
    else:
        jf = _pointer_hr_job_folder()
    if not jf:
        return 0, 0
    pending_dir = PLUGIN_DATA_ROOT / jf / "pending"
    n = len(list(pending_dir.rglob("*.pdf"))) if pending_dir.exists() else 0
    cap = 0
    jd_cap_path = PLUGIN_DATA_ROOT / jf / "jd.json"
    if jd_cap_path.is_file():
        try:
            _jdoc = json.loads(jd_cap_path.read_text(encoding="utf-8"))
            if isinstance(_jdoc, dict):
                if _jdoc.get("resume_collect_target") is not None:
                    cap = int(_jdoc.get("resume_collect_target") or 0)
                elif _jdoc.get("analyze_threshold") is not None:
                    cap = int(_jdoc.get("analyze_threshold") or 0)
        except Exception:
            pass
    state = _load_task_state()
    cfg = (state.get("_last_job_configs") or {}).get(jf) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if cap <= 0:
        cap = int(cfg.get("resume_collect_target") or cfg.get("analyze_threshold") or 0)
    if cap <= 0:
        cap = 4
    return n, cap


def get_recruitment_status_digest(job_name: str = "", jd_config_path_hint: str = "") -> dict[str, Any]:
    """
    供 Lark 上线简报等：当前岗位、pending 简历数与目标、未透析估算、调度是否在跑、全局停止、手动透析登记。
    job_name 为空时从 HR workflow 指针读取。

    ``jd_config_path_hint``：若为本插件数据根下某岗 ``jd.json`` 的绝对路径，则 **优先** 用其所在目录作为
    ``job_folder`` 计算 ``scheduler_active`` 等，避免岗位名已切到新岗而指针目录仍为旧岗时的串岗误判。
    """
    jn = (job_name or "").strip()
    if not jn:
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            jn = (get_hr_recruitment_workflow_pointer().get("job_name") or "").strip()
        except Exception:
            pass
    try:
        from tools.boss_utils import strip_leading_recruitment_verbs_for_job_chat

        jn = strip_leading_recruitment_verbs_for_job_chat(jn) if jn else jn
    except Exception:
        pass
    if not jn:
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            ptr = get_hr_recruitment_workflow_pointer()
            jfp = (ptr.get("jd_config_path") or "").strip()
            if jfp and Path(jfp).is_file():
                _doc = json.loads(Path(jfp).read_text(encoding="utf-8"))
                if isinstance(_doc, dict):
                    jn = strip_leading_recruitment_verbs_for_job_chat(
                        (_doc.get("job_title") or "").strip()
                    )
        except Exception:
            pass
    if not jn:
        jf0 = _pointer_hr_job_folder()
        if jf0 and (PLUGIN_DATA_ROOT / jf0 / "jd.json").is_file():
            try:
                _doc2 = json.loads((PLUGIN_DATA_ROOT / jf0 / "jd.json").read_text(encoding="utf-8"))
                if isinstance(_doc2, dict):
                    jn = strip_leading_recruitment_verbs_for_job_chat(
                        (_doc2.get("job_title") or "").strip()
                    ) or jf0
            except Exception:
                jn = jf0
    if not jn:
        return {"has_active_job": False}
    jf = _job_folder_from_jd_config_path_hint(jd_config_path_hint) or _resolve_hr_data_job_folder(jn)
    n, cap = get_harvest_progress_snapshot(jn, job_folder=jf)
    output_dir = PLUGIN_DATA_ROOT / jf / "result"
    unprocessed = _count_unprocessed_pdfs(jf, output_dir)
    state = _load_task_state()
    raw_js = state.get(jf)
    job_state = raw_js if isinstance(raw_js, dict) else {}
    manual_pending = bool(job_state.get("pending_manual_analyze"))
    cfg = (state.get("_last_job_configs") or {}).get(jf) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    thr = int(cfg.get("analyze_threshold") or 4)
    max_per_harvest = int(cfg.get("max_count") or 50)
    greet_n = int(cfg.get("greet_target") or 3)
    rec_min = int(cfg.get("recommend_interval_minutes") or 15)
    switch_m = _effective_greet_harvest_switch_minutes(cfg)
    enable_greet = bool(cfg.get("enable_greet_recommend", True))
    got = int(cfg.get("greet_only_total_target") or 0)
    gdone = int(job_state.get("hr_greet_only_done", 0) or 0)
    greet_only_live = False
    if _APSCHEDULER_AVAILABLE and scheduler is not None and got > 0:
        try:
            greet_only_live = scheduler.get_job(f"rec_{jf}_greet_only") is not None
        except Exception:
            greet_only_live = False
    return {
        "has_active_job": True,
        "job_name": jn,
        "job_folder": jf,
        "pending_pdf_count": n,
        "collect_cap": cap,
        "unprocessed_for_analysis": unprocessed,
        "analyze_threshold": thr,
        "max_count_per_harvest": max_per_harvest,
        "greet_target": greet_n,
        "recommend_interval_minutes": rec_min,
        "greet_harvest_switch_interval_minutes": switch_m,
        "enable_greet_recommend": enable_greet,
        "parallel_greet_and_harvest": bool(cfg.get("parallel_greet_and_harvest", False)),
        "greet_only_total_target": got,
        "greet_only_done": gdone,
        "greet_only_scheduler_active": greet_only_live,
        "scheduler_active": _has_greet_harvest_scheduler_jobs(jf),
        "globally_stopped": is_recruitment_stopped(),
        "manual_analyze_pending": manual_pending,
    }


def _count_pdfs_under(dir_path: Path) -> int:
    if not dir_path.exists():
        return 0
    return len([p for p in dir_path.rglob("*.pdf") if p.is_file()])


def build_recruitment_job_memory(job_name: str, job_folder: str = "") -> dict[str, Any]:
    """
    聚合磁盘与 scheduler_state 中该岗位的招聘快照，供再次启动无人值守时向 HR 宣读进度并确认「续接 / 新开」。

    不修改状态；add_scheduled_job 在清空前调用可得到「启动前」的续跑/手动透析标志。
    ``job_folder`` 非空时优先作为数据目录键（与 ``hr_recruitment/{key}/`` 一致）。
    """
    jn = (job_name or "").strip()
    if not jn:
        return {"has_memory": False, "hr_brief_zh": "", "error": "job_name 为空"}
    jf = _sanitize_job_folder((job_folder or "").strip() or jn)
    pending_dir = PLUGIN_DATA_ROOT / jf / "pending"
    output_dir = PLUGIN_DATA_ROOT / jf / "result"
    processed_dir = PLUGIN_DATA_ROOT / jf / "processed"
    jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"

    pending_pdf = _count_pdfs_under(pending_dir)
    analysis_md = len(list(output_dir.glob("*_analysis.md"))) if output_dir.exists() else 0
    processed_pdf = _count_pdfs_under(processed_dir)
    unprocessed = _count_unprocessed_pdfs(jf, output_dir)

    state = _load_task_state()
    raw_js = state.get(jf)
    job_state = raw_js if isinstance(raw_js, dict) else {}
    last_analyze = (job_state.get("last_analyze_time") or "").strip()
    manual_pending = bool(job_state.get("pending_manual_analyze"))
    hr_continue = bool(job_state.get("hr_analyze_continue"))

    cfg = (state.get("_last_job_configs") or {}).get(jf) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    has_saved_cfg = bool(cfg)
    cap = int(cfg.get("resume_collect_target") or cfg.get("analyze_threshold") or 0) if has_saved_cfg else 0
    thr = int(cfg.get("analyze_threshold") or 0) if has_saved_cfg else 0

    jd_go_target = 0
    jd_go_interval = 0
    jd_sched: dict[str, Any] = {}
    if jd_path.is_file():
        try:
            _jdd = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(_jdd, dict):
                jd_sched = _jdd
                if _jdd.get("greet_only_total_target") is not None:
                    jd_go_target = int(_jdd.get("greet_only_total_target") or 0)
                if _jdd.get("greet_only_interval_minutes") is not None:
                    jd_go_interval = int(_jdd.get("greet_only_interval_minutes") or 0)
        except Exception:
            pass

    def _mem_int(doc: dict[str, Any], key: str, default: int) -> int:
        v = doc.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _mem_bool(doc: dict[str, Any], key: str, default: bool) -> bool:
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

    _DEF_MCH, _DEF_GT, _DEF_RCT, _DEF_SW = 50, 3, 4, 10
    if has_saved_cfg:
        mch = (
            int(cfg["max_count"])
            if cfg.get("max_count") is not None
            else _mem_int(jd_sched, "max_count", _DEF_MCH)
        )
        gt = (
            int(cfg["greet_target"])
            if cfg.get("greet_target") is not None
            else _mem_int(jd_sched, "greet_target", _DEF_GT)
        )
        if "enable_greet_recommend" in cfg:
            eg_sched = bool(cfg.get("enable_greet_recommend"))
        else:
            eg_sched = _mem_bool(jd_sched, "enable_greet_recommend", True)
        sw_sched = (
            int(cfg["greet_harvest_switch_interval_minutes"])
            if cfg.get("greet_harvest_switch_interval_minutes") is not None
            else _mem_int(
                jd_sched,
                "greet_harvest_switch_interval_minutes",
                _mem_int(jd_sched, "recommend_interval_minutes", _DEF_SW),
            )
        )
        rct_sched = int(cfg.get("resume_collect_target") or cfg.get("analyze_threshold") or 0)
        if rct_sched <= 0:
            rct_sched = _mem_int(
                jd_sched, "resume_collect_target", _mem_int(jd_sched, "analyze_threshold", _DEF_RCT)
            )
        rct_sched = max(1, min(9999, rct_sched))
        at_sched = int(cfg.get("analyze_threshold") or 0) or rct_sched
    else:
        mch = _mem_int(jd_sched, "max_count", _DEF_MCH)
        gt = _mem_int(jd_sched, "greet_target", _DEF_GT)
        eg_sched = _mem_bool(jd_sched, "enable_greet_recommend", True)
        sw_sched = _mem_int(
            jd_sched,
            "greet_harvest_switch_interval_minutes",
            _mem_int(jd_sched, "recommend_interval_minutes", _DEF_SW),
        )
        rct_sched = _mem_int(
            jd_sched, "resume_collect_target", _mem_int(jd_sched, "analyze_threshold", _DEF_RCT)
        )
        rct_sched = max(1, min(9999, rct_sched))
        at_sched = _mem_int(jd_sched, "analyze_threshold", rct_sched)
    sw_sched = max(1, min(120, int(sw_sched)))

    go_done = int(job_state.get("hr_greet_only_done", 0) or 0)
    go_target = jd_go_target
    if go_target <= 0 and has_saved_cfg:
        go_target = int(cfg.get("greet_only_total_target") or 0)
    go_last = job_state.get("hr_greet_only_last_finished")
    if not isinstance(go_last, dict):
        go_last = {}
    inc_snap = incomplete_greet_only_snapshot(jf)

    sched_active = _has_greet_harvest_scheduler_jobs(jf)
    jd_ok = jd_path.is_file()

    has_memory = (
        pending_pdf > 0
        or analysis_md > 0
        or processed_pdf > 0
        or has_saved_cfg
        or bool(last_analyze)
        or manual_pending
        or hr_continue
        or jd_ok
        or go_done > 0
        or bool(go_last and go_last.get("target") is not None)
    )

    _eg_zh = "开" if eg_sched else "关"
    _src = "上次保存配置" if has_saved_cfg else "jd.json（缺省为系统默认）"
    lines = [
        f"【岗位「{jn}」历史快照】",
        f"· pending 内 PDF：{pending_pdf} 份",
        f"· result 内分析报告：{analysis_md} 份",
        f"· 待透析估算（≈ 有 PDF 尚无报告）：约 {unprocessed} 份",
        f"· processed 内 PDF：{processed_pdf} 份",
        f"· jd.json：{'有' if jd_ok else '无'}",
        (
            f"· **本岗调度数字**（{_src}）：单次收网每 tick 最多 **{mch}** 份；"
            f"每轮「推荐牛人」打招呼最多 **{gt}** 人；推荐牛人 **{_eg_zh}**；"
            f"推荐↔收简历交替约每 **{sw_sched}** 分钟；累计约 **{rct_sched}** 份后停自动收网；"
            f"未出 AI 评价满 **{at_sched}** 份触发透析。"
        ),
        f"· 上次透析/琅琊榜记录时间：{last_analyze or '无'}",
        f"· 手动透析已登记：{'是' if manual_pending else '否'}；透析续跑标志：{'是' if hr_continue else '否'}",
        f"· 当前该岗位 APScheduler 是否在跑：{'是' if sched_active else '否'}",
    ]
    if go_target > 0 or go_done > 0 or go_last:
        if go_target > 0:
            lines.append(
                f"· **仅打招呼**进度：已成功 **{go_done}** / 目标 **{go_target}** 次"
                + (f"（jd 中间隔约 {jd_go_interval} 分钟）" if jd_go_interval > 0 else "")
            )
        elif go_done > 0:
            lines.append(f"· **仅打招呼**：磁盘状态显示已累计 **{go_done}** 次（目标需看 jd 或上次配置）")
        if inc_snap:
            lines.append(
                "  → 有**未完成**的仅打招呼任务且当前**未在跑**；可飞书发 **继续仅打招呼** 续接，"
                f"或 **仅打招呼{inc_snap['target']}重开** 从零计数。"
            )
        if go_last.get("done") is not None and go_last.get("target") is not None:
            lines.append(
                f"· 上一轮仅打招呼**已结束**记录：{go_last.get('done')}/{go_last.get('target')} 次"
                f"（{go_last.get('finished_at') or '时间未知'}，提前结束={'是' if go_last.get('early') else '否'}）"
            )
    lines.append("")
    lines.append(
        "请向 HR 确认：默认仍使用同一 data 目录，即**续接**历史简历与报告。"
        "若需**从零新开**（例如清空 pending、换 JD），请明确说明后再执行清理或新发布（本调度不会自动删盘）。"
    )

    hr_brief = "\n".join(lines)

    return {
        "has_memory": has_memory,
        "job_name": jn,
        "job_folder": jf,
        "pending_pdf_count": pending_pdf,
        "result_analysis_md_count": analysis_md,
        "processed_pdf_count": processed_pdf,
        "unprocessed_for_analysis": unprocessed,
        "last_analyze_time": last_analyze,
        "pending_manual_analyze": manual_pending,
        "hr_analyze_continue": hr_continue,
        "has_saved_scheduler_config": has_saved_cfg,
        "saved_config_summary": {
            "resume_collect_target": cap or None,
            "analyze_threshold": thr or None,
            "enable_greet_recommend": cfg.get("enable_greet_recommend"),
        }
        if has_saved_cfg
        else {},
        "scheduler_jobs_active": sched_active,
        "jd_json_exists": jd_ok,
        "greet_only": {
            "target": go_target or None,
            "done": go_done,
            "incomplete_snapshot": inc_snap,
            "last_finished": go_last if go_last else None,
        },
        "hr_brief_zh": hr_brief,
    }


def resume_hr_recruitment_scheduler() -> dict[str, Any]:
    """
    飞书「继续」：清除全局停止标志、进程内/持久化 STOP_HARVEST；
    若当前岗位已无 APScheduler 任务，则按上次持久化的 job_config（或 jd.json 兜底）重新 add_scheduled_job。
    """
    from l3_node.local_memory import clear_stop_harvest_from_workflow_state, get_hr_recruitment_workflow_pointer
    from l3_node.workflow_signal_bridge import purge_stop_harvest_signals

    set_recruitment_stopped(False)
    ptr = get_hr_recruitment_workflow_pointer()
    jn = (ptr.get("job_name") or "").strip()
    jf = _resolve_hr_data_job_folder(jn)
    wid = (ptr.get("workflow_id") or "").strip() or (f"hr_recruitment_job_{jf}" if jf else "")
    purged = 0
    persisted_cleared = 0
    if wid:
        purged = purge_stop_harvest_signals(wid)
        try:
            persisted_cleared = clear_stop_harvest_from_workflow_state(wid)
        except Exception as e:
            logger.debug("[Scheduler] 清除持久化 STOP 跳过: %s", e)

    n, cap = get_harvest_progress_snapshot(jn, job_folder=jf)
    if jf and _has_greet_harvest_scheduler_jobs(jf):
        return {
            "ok": True,
            "bridge_purged_stop": purged,
            "persist_purged_stop": persisted_cleared,
            "restored_scheduler": False,
            "restore_error": "",
            "already_running": True,
            "pending_pdfs": n,
            "collect_cap": cap,
            "job_folder": jf,
        }

    restored = False
    restore_err = ""
    if jf and not _has_greet_harvest_scheduler_jobs(jf):
        state = _load_task_state()
        saved = (state.get("_last_job_configs") or {}).get(jf)
        if saved and isinstance(saved, dict):
            cfg = dict(saved)
            cfg["job_name"] = (cfg.get("job_name") or jn or jf).strip()
            try:
                r = add_scheduled_job(cfg)
                restored = bool(r.get("ok"))
                if not restored:
                    restore_err = str(r.get("error", ""))
            except Exception as e:
                restore_err = str(e)
                logger.warning("[Scheduler] 继续：恢复任务失败: %s", e)
        else:
            jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
            if jd_path.exists():
                try:
                    _eg, _pg = True, False
                    try:
                        _jdoc = json.loads(jd_path.read_text(encoding="utf-8"))
                        if isinstance(_jdoc, dict):
                            if "enable_greet_recommend" in _jdoc:
                                _eg = _sched_bool_from_jd(_jdoc.get("enable_greet_recommend"), True)
                            if "parallel_greet_and_harvest" in _jdoc:
                                _pg = _sched_bool_from_jd(_jdoc.get("parallel_greet_and_harvest"), False)
                    except Exception:
                        pass
                    r = add_scheduled_job({
                        "job_name": jn or jf,
                        "jd_config_path": str(jd_path),
                        "analyze_threshold": 4,
                        "resume_collect_target": 4,
                        "enable_greet_recommend": _eg,
                        "parallel_greet_and_harvest": _pg,
                        "auto_analyze": True,
                    })
                    restored = bool(r.get("ok"))
                    if not restored:
                        restore_err = str(r.get("error", ""))
                except Exception as e:
                    restore_err = str(e)
                    logger.warning("[Scheduler] 继续：兜底恢复失败: %s", e)
            else:
                restore_err = "无 jd.json 且无上次 job_config，无法自动恢复定时任务"

    n, cap = get_harvest_progress_snapshot(jn, job_folder=jf)
    return {
        "ok": True,
        "bridge_purged_stop": purged,
        "persist_purged_stop": persisted_cleared,
        "restored_scheduler": restored,
        "restore_error": restore_err,
        "already_running": False,
        "pending_pdfs": n,
        "collect_cap": cap,
        "job_folder": jf,
    }


def list_scheduler_suspended_jobs() -> list[dict[str, Any]]:
    """
    列出因 **换岗抢占** 写入 ``scheduler_suspended`` 且仍具备恢复依据的目录键。

    恢复依据：存在 ``_last_job_configs[jf]`` 或 ``{PLUGIN_DATA_ROOT}/{jf}/jd.json``。
    若某岗实际仍有 greet/harvest 定时在跑，则忽略该条（避免陈旧标记误显）。
    """
    state = _load_task_state()
    last_map = state.get("_last_job_configs")
    if not isinstance(last_map, dict):
        last_map = {}
    out: list[dict[str, Any]] = []
    for key, bucket in state.items():
        if not key or str(key).startswith("_"):
            continue
        if not isinstance(bucket, dict):
            continue
        sus = bucket.get("scheduler_suspended")
        if not isinstance(sus, dict):
            continue
        jf = _sanitize_job_folder(str(key).strip())
        if not jf:
            continue
        if _has_greet_harvest_scheduler_jobs(jf):
            continue
        saved = last_map.get(jf)
        jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
        if not (isinstance(saved, dict) and saved) and not jd_path.is_file():
            continue
        jn = ""
        if isinstance(saved, dict):
            jn = str(saved.get("job_name") or "").strip()
        if not jn and jd_path.is_file():
            try:
                doc = json.loads(jd_path.read_text(encoding="utf-8"))
                if isinstance(doc, dict):
                    jn = str(doc.get("job_title") or "").strip()
            except Exception:
                pass
        out.append(
            {
                "job_folder": jf,
                "job_name": jn or jf,
                "suspended": dict(sus),
                "has_saved_config": bool(isinstance(saved, dict) and saved),
            }
        )
    out.sort(key=lambda x: float((x.get("suspended") or {}).get("at") or 0), reverse=True)
    return out


def resume_hr_job_scheduler_for_folder(job_folder: str = "", job_name: str = "") -> dict[str, Any]:
    """
    按 **数据目录键** 恢复无人值守：清 STOP、优先 ``_last_job_configs``，否则 jd.json 兜底，再 ``add_scheduled_job``。
    与 ``resume_hr_recruitment_scheduler`` 不同：不依赖当前指针 primary，用于「换回之前被抢占的岗」。
    """
    jf = _sanitize_job_folder((job_folder or "").strip())
    if not jf and (job_name or "").strip():
        jf = _sanitize_job_folder((job_name or "").strip())
    if not jf:
        return {"ok": False, "error": "job_folder 或 job_name 不能为空"}

    if _has_greet_harvest_scheduler_jobs(jf):
        jn_guess = (job_name or "").strip()
        if not jn_guess:
            state0 = _load_task_state()
            s0 = (state0.get("_last_job_configs") or {}).get(jf)
            if isinstance(s0, dict):
                jn_guess = str(s0.get("job_name") or "").strip()
        n, cap = get_harvest_progress_snapshot(jn_guess or jf, job_folder=jf)
        return {
            "ok": True,
            "already_running": True,
            "job_folder": jf,
            "pending_pdfs": n,
            "collect_cap": cap,
            "restored_scheduler": False,
            "restore_error": "",
        }

    from l3_node.local_memory import clear_stop_harvest_from_workflow_state
    from l3_node.workflow_signal_bridge import purge_stop_harvest_signals

    set_recruitment_stopped(False)
    wid = f"hr_recruitment_job_{jf}"
    purged = 0
    persisted_cleared = 0
    if wid:
        try:
            purged = purge_stop_harvest_signals(wid)
        except Exception as e:
            logger.debug("[Scheduler] resume_by_folder purge_stop 跳过: %s", e)
        try:
            persisted_cleared = clear_stop_harvest_from_workflow_state(wid)
        except Exception as e:
            logger.debug("[Scheduler] resume_by_folder clear_stop persist 跳过: %s", e)

    state = _load_task_state()
    saved = (state.get("_last_job_configs") or {}).get(jf)
    cfg: dict[str, Any] | None = None
    jn = (job_name or "").strip()

    if isinstance(saved, dict) and saved:
        cfg = dict(saved)
        cfg["job_folder"] = jf
        if not (cfg.get("job_name") or "").strip():
            cfg["job_name"] = jn or jf
        else:
            jn = jn or str(cfg.get("job_name") or "").strip()

    if not cfg:
        jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
        if jd_path.is_file():
            _eg, _pg = True, False
            try:
                _jdoc = json.loads(jd_path.read_text(encoding="utf-8"))
                if isinstance(_jdoc, dict):
                    if "enable_greet_recommend" in _jdoc:
                        _eg = _sched_bool_from_jd(_jdoc.get("enable_greet_recommend"), True)
                    if "parallel_greet_and_harvest" in _jdoc:
                        _pg = _sched_bool_from_jd(_jdoc.get("parallel_greet_and_harvest"), False)
                    jn = jn or str(_jdoc.get("job_title") or "").strip() or jf
            except Exception:
                jn = jn or jf
            cfg = {
                "job_name": jn or jf,
                "jd_config_path": str(jd_path),
                "analyze_threshold": 4,
                "resume_collect_target": 4,
                "enable_greet_recommend": _eg,
                "parallel_greet_and_harvest": _pg,
                "auto_analyze": True,
            }

    if not cfg:
        return {
            "ok": False,
            "error": "无该岗 _last_job_configs 且无 jd.json，无法恢复",
            "job_folder": jf,
            "bridge_purged_stop": purged,
            "persist_purged_stop": persisted_cleared,
        }

    cfg.setdefault("job_folder", jf)
    if jn:
        cfg["job_name"] = (cfg.get("job_name") or jn).strip() or jn

    try:
        r = add_scheduled_job(cfg)
    except Exception as e:
        logger.warning("[Scheduler] resume_hr_job_scheduler_for_folder 失败: %s", e)
        return {
            "ok": False,
            "error": str(e),
            "job_folder": jf,
            "bridge_purged_stop": purged,
            "persist_purged_stop": persisted_cleared,
        }

    n, cap = get_harvest_progress_snapshot(str(cfg.get("job_name") or jf), job_folder=jf)
    merged: dict[str, Any] = {
        **(r if isinstance(r, dict) else {"ok": False}),
        "bridge_purged_stop": purged,
        "persist_purged_stop": persisted_cleared,
        "resumed_job_folder": jf,
        "pending_pdfs": n,
        "collect_cap": cap,
    }
    if merged.get("ok"):
        _hr_audit(
            "scheduler_resumed_by_folder",
            job_folder=jf,
            job_name=str(cfg.get("job_name") or jf),
            detail={"source": "last_job_configs" if isinstance(saved, dict) and saved else "jd_json_fallback"},
        )
    return merged


def apply_lark_more_harvest_extra(extra: int) -> dict[str, Any]:
    """
    飞书「再抓 N 份」：在上一次收网目标、当前未处理简历数、pending 目录 PDF 数 三者取高后 **再加 N**，
    作为新的 resume_collect_target，并 **remove + add** 定时任务。

    说明：未处理数 = pending 中 PDF − result 中分析报告数；若 5 份均已透析，未处理=0，
    若仅用「未处理+N」会得到 6 而非 5+6=11，故必须与历史 cap / 磁盘 PDF 取 max。

    APScheduler 的 job 参数在添加时闭包捕获旧 dict，仅改 scheduler_state.json 不会生效，必须重新 add_scheduled_job。
    """
    from l3_node.local_memory import get_hr_recruitment_workflow_pointer

    try:
        n_extra = int(extra)
    except (TypeError, ValueError):
        return {"ok": False, "error": "无效份数"}
    if n_extra <= 0:
        return {"ok": False, "error": "份数须为正整数"}

    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    ptr = get_hr_recruitment_workflow_pointer()
    jn = (ptr.get("job_name") or "").strip()
    if not jn:
        return {
            "ok": False,
            "error": "无当前招聘岗位指针，请先在本会话启动招聘 workflow 或绑定岗位。",
        }

    jf = _resolve_hr_data_job_folder(jn)
    output_dir = PLUGIN_DATA_ROOT / jf / "result"
    output_dir.mkdir(parents=True, exist_ok=True)
    pending_dir = PLUGIN_DATA_ROOT / jf / "pending"
    pending_pdf = len(list(pending_dir.rglob("*.pdf"))) if pending_dir.exists() else 0
    unprocessed = _count_unprocessed_pdfs(jf, output_dir)

    state = _load_task_state()
    raw_js = state.get(jf)
    job_state = raw_js if isinstance(raw_js, dict) else {}
    prev_manual = bool(job_state.get("pending_manual_analyze"))

    saved = (state.get("_last_job_configs") or {}).get(jf)
    cfg: dict[str, Any] | None = None
    if isinstance(saved, dict) and saved:
        cfg = dict(saved)
        cfg["job_name"] = (cfg.get("job_name") or jn).strip()
    else:
        jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
        if jd_path.exists():
            _eg, _pg = True, False
            try:
                _jdoc = json.loads(jd_path.read_text(encoding="utf-8"))
                if isinstance(_jdoc, dict):
                    if "enable_greet_recommend" in _jdoc:
                        _eg = _sched_bool_from_jd(_jdoc.get("enable_greet_recommend"), False)
                    if "parallel_greet_and_harvest" in _jdoc:
                        _pg = _sched_bool_from_jd(_jdoc.get("parallel_greet_and_harvest"), False)
            except Exception:
                pass
            cfg = {
                "job_name": jn,
                "jd_config_path": str(jd_path),
                "enable_greet_recommend": _eg,
                "parallel_greet_and_harvest": _pg,
                "auto_analyze": True,
            }
        else:
            return {
                "ok": False,
                "error": f"岗位「{jn}」无上次调度配置且无 jd.json，无法更新收网。",
            }

    _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
    cfg["job_folder"] = jf
    old_cap = int(cfg.get("resume_collect_target") or cfg.get("analyze_threshold") or 0)
    # 再抓 N = 基准 + N；基准 = max(上次目标, 未处理简历, 磁盘 PDF)，避免「已透析 5 份 → 未处理=0 → 只加到 6」
    base_line = max(old_cap, unprocessed, pending_pdf)
    new_cap = base_line + n_extra
    cfg["resume_collect_target"] = new_cap
    # 累计收网份数与自动透析触发阈值同一口径，避免 jd/简报仍显示旧 analyze_threshold
    cfg["analyze_threshold"] = new_cap

    set_recruitment_stopped(False)
    r = add_scheduled_job(cfg)
    if prev_manual:
        try:
            st2 = _load_task_state()
            cur = st2.get(jf)
            nest = dict(cur) if isinstance(cur, dict) else {}
            nest["pending_manual_analyze"] = True
            st2[jf] = nest
            _save_task_state(st2)
        except Exception as e:
            logger.debug("[Scheduler] 恢复手动透析登记失败: %s", e)

    if not r.get("ok"):
        return {
            "ok": False,
            "error": str(r.get("error", "add_scheduled_job 失败")),
            "job_name": jn,
            "job_folder": jf,
            "unprocessed": unprocessed,
            "new_cap": new_cap,
            "old_cap": old_cap,
        }

    _write_jd_scheduling_snapshot_from_cfg(cfg, only_resume_analyze_caps=True)

    logger.info(
        "[Scheduler] Lark 再抓 extra=%s → cap %s→%s base=max(%s,%s,%s) pdfs=%s job=%s",
        n_extra,
        old_cap,
        new_cap,
        old_cap,
        unprocessed,
        pending_pdf,
        pending_pdf,
        jn,
    )
    return {
        "ok": True,
        "job_name": jn,
        "job_folder": jf,
        "unprocessed": unprocessed,
        "pending_pdf": pending_pdf,
        "base_line": base_line,
        "new_cap": new_cap,
        "old_cap": old_cap,
        "analyze_threshold": int(cfg.get("analyze_threshold", new_cap)),
    }


def apply_lark_harvest_only_scheduling(job_name: str = "") -> dict[str, Any]:
    """
    飞书 / 工具：**仅收网**——关闭 ``enable_greet_recommend``，按 jd + 上次配置重新注册调度。

    效果与 ``add_scheduled_job(..., enable_greet_recommend=False)`` 一致：只跑按间隔的
    ``job_harvest_resumes``，不再与推荐/打招呼交替。
    """
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    from l3_node.local_memory import get_hr_recruitment_workflow_pointer

    jn = (job_name or "").strip()
    if not jn:
        jn = (get_hr_recruitment_workflow_pointer().get("job_name") or "").strip()
    if not jn:
        return {"ok": False, "error": "无岗位名；请先绑定岗位或说明职位。"}

    jf = _resolve_hr_data_job_folder(jn)
    jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
    if not jd_path.is_file():
        return {"ok": False, "error": f"岗位「{jn}」缺少 jd.json，请先为该职位生成配置。"}

    try:
        doc = json.loads(jd_path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
        doc["enable_greet_recommend"] = False
        jd_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"写入 jd.json 失败: {e}"}

    state = _load_task_state()
    saved = (state.get("_last_job_configs") or {}).get(jf)
    cfg: dict[str, Any] | None = None
    if isinstance(saved, dict) and saved:
        cfg = dict(saved)
        cfg["job_name"] = (cfg.get("job_name") or jn).strip()
    else:
        _pg = False
        try:
            _jdoc = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(_jdoc, dict) and "parallel_greet_and_harvest" in _jdoc:
                _pg = _sched_bool_from_jd(_jdoc.get("parallel_greet_and_harvest"), False)
        except Exception:
            pass
        cfg = {
            "job_name": jn,
            "jd_config_path": str(jd_path),
            "enable_greet_recommend": False,
            "parallel_greet_and_harvest": _pg,
            "auto_analyze": True,
        }

    _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
    cfg["enable_greet_recommend"] = False
    cfg["job_folder"] = jf
    cfg["jd_config_path"] = str(jd_path)
    cfg.pop("greet_only_total_target", None)
    cfg.pop("greet_only_interval_minutes", None)
    cfg.pop("_greet_only_resume", None)

    set_recruitment_stopped(False)
    try:
        r = add_scheduled_job(cfg)
    except Exception as e:
        return {"ok": False, "error": str(e), "job_name": jn}

    if not r.get("ok"):
        return {
            "ok": False,
            "error": str(r.get("error", "add_scheduled_job 失败")),
            "job_name": jn,
        }

    logger.info("[Scheduler] 仅收网模式：已关闭打招呼并重新注册 job=%s", jn[:48])
    return {"ok": True, "job_name": jn, "job_folder": jf, "harvest_only": True}


def apply_lark_greet_only_campaign(
    total: int,
    interval_minutes: int | None = None,
    *,
    resume: bool = False,
) -> dict[str, Any]:
    """
    飞书 / 工具：启动「定时仅打招呼」——累计成功打招呼达 ``total`` 次后自动停表并发飞书询问是否收网。
    ``resume=True``：不重置已累计次数，从 ``incomplete_greet_only_snapshot`` 恢复目标与间隔。
    不注册收网交替任务与 ``job_check``（透析）；与完整无人值守互斥，以 ``add_scheduled_job`` 为准。
    """
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    from l3_node.local_memory import get_hr_recruitment_workflow_pointer

    ptr = get_hr_recruitment_workflow_pointer()
    jn = (ptr.get("job_name") or "").strip()
    if not jn:
        return {
            "ok": False,
            "error": "无当前招聘岗位指针，请先绑定岗位或在本会话启动招聘 workflow。",
        }

    jf = _resolve_hr_data_job_folder(jn)
    n_total = 0
    if not resume:
        try:
            n_total = int(total)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效次数"}
        if n_total <= 0:
            return {"ok": False, "error": "次数须为正整数"}
    else:
        snap = incomplete_greet_only_snapshot(jf)
        if not snap:
            return {
                "ok": False,
                "error": "没有可续接的仅打招呼进度（可能已完成、正在跑、或从未开始）。",
                "job_name": jn,
                "job_folder": jf,
            }
        n_total = int(snap["target"])
        if interval_minutes is None or int(interval_minutes) <= 0:
            interval_minutes = int(snap["interval_minutes"] or 0) or None

    state = _load_task_state()
    saved = (state.get("_last_job_configs") or {}).get(jf)
    cfg: dict[str, Any] | None = None
    if isinstance(saved, dict) and saved:
        cfg = dict(saved)
        cfg["job_name"] = (cfg.get("job_name") or jn).strip()
    else:
        jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
        if jd_path.exists():
            _eg = True
            try:
                _jdoc = json.loads(jd_path.read_text(encoding="utf-8"))
                if isinstance(_jdoc, dict) and "enable_greet_recommend" in _jdoc:
                    _eg = _sched_bool_from_jd(_jdoc.get("enable_greet_recommend"), False)
            except Exception:
                pass
            cfg = {
                "job_name": jn,
                "jd_config_path": str(jd_path),
                "enable_greet_recommend": _eg,
                "parallel_greet_and_harvest": False,
                "auto_analyze": True,
            }
        else:
            return {
                "ok": False,
                "error": f"岗位「{jn}」无 jd.json，无法启动仅打招呼。",
            }

    assert cfg is not None
    _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
    cfg["job_folder"] = jf
    cfg["greet_only_total_target"] = n_total
    if resume:
        cfg["_greet_only_resume"] = True
    if interval_minutes is not None and int(interval_minutes) > 0:
        cfg["greet_only_interval_minutes"] = max(1, min(120, int(interval_minutes)))
    else:
        cfg.pop("greet_only_interval_minutes", None)

    set_recruitment_stopped(False)
    r = add_scheduled_job(cfg)
    if not r.get("ok"):
        return {
            "ok": False,
            "error": str(r.get("error", "add_scheduled_job 失败")),
            "job_name": jn,
            "job_folder": jf,
        }
    done_hint = 0
    try:
        done_hint = int((_load_task_state().get(jf) or {}).get("hr_greet_only_done") or 0)
    except Exception:
        pass
    return {
        "ok": True,
        "job_name": jn,
        "job_folder": jf,
        "greet_only_total_target": n_total,
        "greet_only_interval_minutes": int(cfg.get("greet_only_interval_minutes") or 0),
        "greet_only_resume": bool(resume),
        "greet_only_done_after_register": done_hint,
    }


def apply_lark_hr_batch_limits(
    *,
    max_count: int | None = None,
    greet_target: int | None = None,
    recommend_interval_minutes: int | None = None,
) -> dict[str, Any]:
    """
    飞书聊天修改「每轮收网最多处理左侧会话数」「每轮推荐打招呼人数」「推荐定时间隔」。
    合并 _last_job_configs 后 add_scheduled_job 重新注册（与「再抓 N 份」同理）。
    当修改 max_count（收网改成 N 人）时，同时将 resume_collect_target、analyze_threshold 设为 N，
    使「累计收简历目标」「自动透析触发份数」与每轮收网上限同数字，避免 HR 看到 10 人收网却仍 4 份目标。
    """
    if max_count is None and greet_target is None and recommend_interval_minutes is None:
        return {"ok": False, "error": "未指定要修改的项"}

    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    from l3_node.local_memory import get_hr_recruitment_workflow_pointer

    ptr = get_hr_recruitment_workflow_pointer()
    jn = (ptr.get("job_name") or "").strip()
    if not jn:
        return {"ok": False, "error": "无当前招聘岗位指针，请先绑定岗位或启动无人值守。"}

    jf = _resolve_hr_data_job_folder(jn)
    state = _load_task_state()
    saved = (state.get("_last_job_configs") or {}).get(jf)
    cfg: dict[str, Any] | None = None
    if isinstance(saved, dict) and saved:
        cfg = dict(saved)
        cfg["job_name"] = (cfg.get("job_name") or jn).strip()
    else:
        jd_path = PLUGIN_DATA_ROOT / jf / "jd.json"
        if not jd_path.exists():
            return {"ok": False, "error": "无上次调度配置且无 jd.json，请先「继续」或重新发布无人值守。"}
        cfg = {
            "job_name": jn,
            "jd_config_path": str(jd_path),
            "analyze_threshold": 4,
            "resume_collect_target": 4,
            "max_count": 50,
            "greet_target": 3,
            "recommend_interval_minutes": 15,
            "greet_harvest_switch_interval_minutes": 10,
            "enable_greet_recommend": True,
            "parallel_greet_and_harvest": False,
            "auto_analyze": True,
            "request_resume": True,
        }
        try:
            _jdoc = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(_jdoc, dict):
                if "enable_greet_recommend" in _jdoc:
                    cfg["enable_greet_recommend"] = _sched_bool_from_jd(_jdoc.get("enable_greet_recommend"), True)
                if "parallel_greet_and_harvest" in _jdoc:
                    cfg["parallel_greet_and_harvest"] = _sched_bool_from_jd(
                        _jdoc.get("parallel_greet_and_harvest"), False
                    )
                if _jdoc.get("analyze_threshold") is not None:
                    cfg["analyze_threshold"] = int(_jdoc.get("analyze_threshold") or 4)
                if _jdoc.get("resume_collect_target") is not None:
                    cfg["resume_collect_target"] = int(_jdoc.get("resume_collect_target") or cfg["analyze_threshold"])
                if _jdoc.get("max_count") is not None:
                    cfg["max_count"] = int(_jdoc.get("max_count") or 50)
                if _jdoc.get("greet_target") is not None:
                    cfg["greet_target"] = int(_jdoc.get("greet_target") or 3)
                if _jdoc.get("recommend_interval_minutes") is not None:
                    cfg["recommend_interval_minutes"] = int(_jdoc.get("recommend_interval_minutes") or 15)
                if _jdoc.get("greet_harvest_switch_interval_minutes") is not None:
                    cfg["greet_harvest_switch_interval_minutes"] = int(
                        _jdoc.get("greet_harvest_switch_interval_minutes") or 10
                    )
        except Exception:
            pass

    _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
    cfg["job_folder"] = jf
    cfg["greet_harvest_switch_interval_minutes"] = _effective_greet_harvest_switch_minutes(cfg)

    if max_count is not None:
        mc = int(max_count)
        if mc < 1 or mc > 500:
            return {"ok": False, "error": "收网每轮最多沟通人数须在 1～500"}
        cfg["max_count"] = mc
        cfg["resume_collect_target"] = mc
        cfg["analyze_threshold"] = mc
    if greet_target is not None:
        gt = int(greet_target)
        if gt < 1 or gt > 200:
            return {"ok": False, "error": "每轮推荐打招呼人数须在 1～200"}
        cfg["greet_target"] = gt
    if recommend_interval_minutes is not None:
        rim = int(recommend_interval_minutes)
        if rim < 1 or rim > 240:
            return {"ok": False, "error": "推荐/收简历交替间隔须在 1～240 分钟"}
        cfg["recommend_interval_minutes"] = rim
        cfg["greet_harvest_switch_interval_minutes"] = rim

    set_recruitment_stopped(False)
    r = add_scheduled_job(cfg)
    if not r.get("ok"):
        return {
            "ok": False,
            "error": str(r.get("error", "add_scheduled_job 失败")),
            "job_name": jn,
        }

    _write_jd_scheduling_snapshot_from_cfg(cfg)

    logger.info(
        "[Scheduler] Lark 批次参数已更新 job=%s max_count=%s greet_target=%s rec_min=%s",
        jn,
        cfg.get("max_count"),
        cfg.get("greet_target"),
        cfg.get("recommend_interval_minutes"),
    )
    return {
        "ok": True,
        "job_name": jn,
        "job_folder": jf,
        "jd_config_path": str(cfg.get("jd_config_path") or "").strip(),
        "max_count": int(cfg.get("max_count", 50)),
        "greet_target": int(cfg.get("greet_target", 3)),
        "recommend_interval_minutes": int(cfg.get("recommend_interval_minutes", 15)),
        "greet_harvest_switch_interval_minutes": int(
            cfg.get("greet_harvest_switch_interval_minutes")
            or _effective_greet_harvest_switch_minutes(cfg)
        ),
        "resume_collect_target": int(cfg.get("resume_collect_target", 4)),
        "analyze_threshold": int(cfg.get("analyze_threshold", 4)),
    }


def _resolve_job_config_for_manual_analyze(job_folder: str, job_display_name: str = "") -> dict[str, Any] | None:
    """
    飞书「分析简历」需 job_check_and_analyze(job_config)。
    优先用上次无人值守持久化的 _last_job_configs；否则用 ~/.jachin/.../jd.json 拼最小配置。
    """
    jf = (job_folder or "").strip()
    if not jf:
        return None
    state = _load_task_state()
    last = (state.get("_last_job_configs") or {}).get(jf)
    if isinstance(last, dict) and last:
        cfg = dict(last)
        cfg["job_name"] = (cfg.get("job_name") or job_display_name or jf).strip()
        cfg["job_folder"] = (cfg.get("job_folder") or jf).strip()
        _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
        return cfg
    jd = PLUGIN_DATA_ROOT / jf / "jd.json"
    if not jd.exists():
        logger.error(
            "[Scheduler] 无法执行透析：无 _last_job_configs[%s] 且缺少 jd.json（路径 %s）。"
            "请先重新「发布无人值守」或保证该岗位目录下有 jd.json。",
            jf,
            jd,
        )
        return None
    jn = (job_display_name or jf).strip()
    cfg: dict[str, Any] = {
        "job_name": jn,
        "job_folder": jf,
        "jd_config_path": str(jd),
        "analyze_threshold": 1,
        "resume_collect_target": 99,
        "auto_analyze": True,
        "enable_greet_recommend": False,
        "parallel_greet_and_harvest": True,
    }
    _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
    return cfg


def request_scheduler_manual_analyze(job_name: str) -> bool:
    """
    Lark/控制台「立即分析」：登记后由 job_check_and_analyze（约每分钟）在满足 pending 有 PDF 时
    执行与「达 analyze_threshold」相同的 Wasm 透析 + 琅琊榜 + Lark 同步。
    不再使用按时间间隔自动透析；手动与阈值共用同一套逻辑。

    重要：若 L3 冷启动后尚未恢复 APScheduler 的 rec_{岗位}_* 任务，仅登记 flag 永远不会被轮询。
    此时在解析出 job_config 后立即后台执行一次 job_check_and_analyze。
    """
    jn = (job_name or "").strip()
    if not jn:
        return False
    jf = _resolve_hr_data_job_folder(jn)
    state = _load_task_state()
    if jf not in state:
        state[jf] = {}
    state[jf]["pending_manual_analyze"] = True
    _save_task_state(state)
    logger.info("[Scheduler] 已登记手动透析请求 job_folder=%s（pending 有 PDF 时执行）", jf)

    cfg = _resolve_job_config_for_manual_analyze(jf, job_display_name=jn)
    if not cfg:
        logger.warning("[Scheduler] 手动透析无 job_config，Lark 将回退独立透析 hr_analyze_resume（若可用）")
        return False

    has_tick = _has_scheduler_jobs_for_folder(jf)
    if has_tick:
        logger.info(
            "[Scheduler] 当前已有 APScheduler 招聘任务；已登记手动透析并将立即尝试 job_check（与定时 tick 互斥）",
        )
        broadcast_log(
            f"[透析镜] 已登记手动分析（岗位 {jf}），已触发立即检查；若岗位互斥忙则等下一轮 tick（若 pending 有 PDF）",
            "INFO",
        )
        # 立刻后台跑一轮，避免 HR 误以为卡住去狂按「分析」；与定时 tick 共用岗位级互斥
        def _immediate_check() -> None:
            try:
                job_check_and_analyze(cfg)
            except Exception:
                logger.exception("[Scheduler] 立即 job_check_and_analyze（手动透析）失败")

        threading.Thread(
            target=_immediate_check,
            daemon=True,
            name=f"hr-manual-check-now-{jf[:20]}",
        ).start()
        return True

    logger.warning(
        "[Scheduler] 当前无 APScheduler 任务（常见于 L3 重启后未点「继续」恢复），"
        "立即后台执行 job_check_and_analyze，避免仅登记无执行",
    )
    broadcast_log(f"[透析镜] 无定时任务，立即后台启动 Wasm 分析（岗位 {jf}）…", "WARNING")

    def _bg_manual_analyze() -> None:
        try:
            job_check_and_analyze(cfg)
        except Exception:
            logger.exception("[Scheduler] 后台 job_check_and_analyze（手动透析）失败")

    threading.Thread(target=_bg_manual_analyze, daemon=True, name=f"hr-manual-analyze-{jf[:20]}").start()
    return True


def _sync_hr_workflow_pointer_for_lark(job_config: dict[str, Any]) -> None:
    """供飞书「停止/分析」解析 workflow_id 与简历目录（与 inject_signal 目标一致）。"""
    try:
        from l3_node.local_memory import set_hr_recruitment_workflow_pointer

        job_name = (job_config.get("job_name") or "").strip()
        jf = _job_folder_from_job_config(job_config)
        if not jf:
            return
        wid = f"hr_recruitment_job_{jf}"
        jd_path = str(job_config.get("jd_config_path") or "")
        pend = str(PLUGIN_DATA_ROOT / jf / "pending")
        set_hr_recruitment_workflow_pointer(
            wid,
            job_name=job_name,
            job_folder=jf,
            jd_config_path=jd_path,
            resume_pending_dir=pend,
        )
    except Exception as e:
        logger.debug("[Scheduler] Lark HR workflow 指针同步失败: %s", e)


def _sched_bool_from_jd(val: Any, default: bool) -> bool:
    """与 MCP/agent 一致解析 jd.json 中的布尔字段。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("0", "false", "no", "否", "关", "off"):
        return False
    if s in ("1", "true", "yes", "是", "开", "on"):
        return True
    return default


def _merge_scheduling_flags_from_jd_if_missing(job_config: dict[str, Any], job_folder: str) -> None:
    """jd.json 可持久化 enable_greet_recommend 等；仅当 job_config 未包含该键时才从 jd 合并。"""
    raw_path = (job_config.get("jd_config_path") or "").strip()
    p = Path(raw_path) if raw_path else Path()
    if not raw_path or not p.exists():
        p = PLUGIN_DATA_ROOT / job_folder / "jd.json"
    if not p.exists():
        return
    try:
        jd = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(jd, dict):
        return
    if "enable_greet_recommend" not in job_config and "enable_greet_recommend" in jd:
        job_config["enable_greet_recommend"] = _sched_bool_from_jd(jd.get("enable_greet_recommend"), True)
    if "parallel_greet_and_harvest" not in job_config and "parallel_greet_and_harvest" in jd:
        job_config["parallel_greet_and_harvest"] = _sched_bool_from_jd(jd.get("parallel_greet_and_harvest"), False)
    if "max_count" not in job_config and jd.get("max_count") is not None:
        try:
            job_config["max_count"] = int(jd.get("max_count") or 50)
        except (TypeError, ValueError):
            pass
    if "greet_target" not in job_config and jd.get("greet_target") is not None:
        try:
            job_config["greet_target"] = int(jd.get("greet_target") or 3)
        except (TypeError, ValueError):
            pass
    if "recommend_interval_minutes" not in job_config and jd.get("recommend_interval_minutes") is not None:
        try:
            job_config["recommend_interval_minutes"] = int(jd.get("recommend_interval_minutes") or 15)
        except (TypeError, ValueError):
            pass
    if "harvest_interval_minutes" not in job_config and jd.get("harvest_interval_minutes") is not None:
        try:
            job_config["harvest_interval_minutes"] = int(jd.get("harvest_interval_minutes") or 1)
        except (TypeError, ValueError):
            pass
    if "greet_harvest_switch_interval_minutes" not in job_config and jd.get(
        "greet_harvest_switch_interval_minutes"
    ) is not None:
        try:
            job_config["greet_harvest_switch_interval_minutes"] = int(
                jd.get("greet_harvest_switch_interval_minutes") or 10
            )
        except (TypeError, ValueError):
            pass
    if "dynamic_schedule" not in job_config and "dynamic_schedule" in jd:
        job_config["dynamic_schedule"] = _sched_bool_from_jd(jd.get("dynamic_schedule"), True)
    if "auto_analyze" not in job_config and "auto_analyze" in jd:
        job_config["auto_analyze"] = _sched_bool_from_jd(jd.get("auto_analyze"), True)
    if "greet_only_total_target" not in job_config and jd.get("greet_only_total_target") is not None:
        try:
            job_config["greet_only_total_target"] = int(jd.get("greet_only_total_target") or 0)
        except (TypeError, ValueError):
            pass
    if "greet_only_interval_minutes" not in job_config and jd.get("greet_only_interval_minutes") is not None:
        try:
            job_config["greet_only_interval_minutes"] = int(jd.get("greet_only_interval_minutes") or 0)
        except (TypeError, ValueError):
            pass


def _reload_job_config_for_tick(job_folder: str, tick_config: dict[str, Any]) -> dict[str, Any]:
    """合并持久化 job_config 与 jd.json，供动态重调度使用（tick 入参可能滞后于 jd/飞书改参）。"""
    jf = (job_folder or "").strip()
    st = _load_task_state()
    saved = (st.get("_last_job_configs") or {}).get(jf)
    if isinstance(saved, dict) and saved:
        cfg = dict(saved)
    else:
        cfg = dict(tick_config)
    cfg["job_folder"] = jf
    jn = (tick_config.get("job_name") or cfg.get("job_name") or "").strip()
    if jn:
        cfg["job_name"] = jn
    _merge_scheduling_flags_from_jd_if_missing(cfg, jf)
    return cfg


def _persist_inbox_pipeline_snapshot(job_folder: str, tick_result: dict[str, Any]) -> None:
    """将最近一次收网 MCP 对 Boss 左侧会话列表的观测写入 scheduler_state（供动态调度，非 pending PDF 口径）。"""
    jf = (job_folder or "").strip()
    if not jf or not isinstance(tick_result, dict):
        return
    try:
        st = _load_task_state()
        bucket = st.setdefault(jf, {})
        bucket["inbox_chat_list_count"] = int(tick_result.get("inbox_chat_list_count", -1))
        bucket["inbox_no_chats"] = bool(tick_result.get("inbox_no_chats", False))
        err = str(tick_result.get("inbox_harvest_error") or "").strip()
        if err:
            bucket["inbox_last_error"] = err[:500]
        _save_task_state(st)
    except Exception as e:
        logger.debug("[Scheduler] 持久化 inbox 观测失败: %s", e)


def _load_inbox_pipeline_snapshot(job_folder: str) -> dict[str, Any]:
    jf = (job_folder or "").strip()
    if not jf:
        return {"inbox_chat_list_count": -1, "inbox_no_chats": False}
    try:
        st = _load_task_state()
        raw = st.get(jf)
        bucket = raw if isinstance(raw, dict) else {}
        ic = bucket.get("inbox_chat_list_count", -1)
        try:
            ic_i = int(ic)
        except (TypeError, ValueError):
            ic_i = -1
        return {
            "inbox_chat_list_count": ic_i,
            "inbox_no_chats": bool(bucket.get("inbox_no_chats", False)),
        }
    except Exception:
        return {"inbox_chat_list_count": -1, "inbox_no_chats": False}


def _dynamic_schedule_enabled(cfg: dict[str, Any]) -> bool:
    return _sched_bool_from_jd(cfg.get("dynamic_schedule"), True)


def _reschedule_hr_interval_job(
    job_id: str,
    func: Any,
    minutes: int,
    args: list[Any],
    *,
    misfire_grace_time: int,
    delay_seconds: int = 12,
) -> None:
    if not _APSCHEDULER_AVAILABLE or scheduler is None:
        return
    minutes = max(1, int(minutes))
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    scheduler.add_job(
        func,
        "interval",
        minutes=minutes,
        id=job_id,
        next_run_time=datetime.now() + timedelta(seconds=max(3, int(delay_seconds))),
        args=args,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=misfire_grace_time,
    )


def _llm_suggest_hr_interval_minutes(
    job_folder: str,
    job_name: str,
    *,
    inbox_chat_list_count: int,
    inbox_no_chats: bool,
    base_r: int,
    base_h: int,
    heuristic_r: int,
    heuristic_h: int,
) -> tuple[int, int] | None:
    flag = (os.environ.get("JACHIN_HR_SCHEDULE_LLM") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return None
    try:
        cd = int(os.environ.get("JACHIN_HR_SCHEDULE_LLM_COOLDOWN_SEC") or "1800")
    except ValueError:
        cd = 1800
    st = _load_task_state()
    jk = st.setdefault(job_folder, {})
    import time

    now = time.time()
    try:
        last = float(jk.get("dynamic_llm_last_ts", 0))
    except (TypeError, ValueError):
        last = 0.0
    if now - last < cd:
        return None

    sys_prompt = (
        "你是招聘自动化调度器。仅根据数据输出 JSON，不要其它文字。"
        "字段 recommend_minutes（推荐牛人间隔分钟）、harvest_minutes（收网间隔分钟），均为整数。"
        "约束：recommend_minutes 在 3～45；harvest_minutes 在 1～10。"
        "依据 Boss 沟通页左侧「可遍历的会话条数」判断：列表空或极少时，应更频繁推荐牛人以新开对话，"
        "并拉长收网间隔（无人可聊时高频收网无意义）；会话较多时可缩短收网、推荐按基准略放慢以减锁竞争。"
    )
    user_prompt = (
        f"职位={job_name!r} 左侧会话条数={inbox_chat_list_count} inbox_empty={inbox_no_chats} "
        f"基准 推荐={base_r}min 收网={base_h}min；"
        f"启发式建议 推荐={heuristic_r}min 收网={heuristic_h}min。"
        f'请输出形如 {{"recommend_minutes": 8, "harvest_minutes": 2}} 的 JSON。'
    )

    async def _call() -> str:
        from l3_node.__main__ import _create_engine_standalone

        eng = _create_engine_standalone()
        out = await eng.generate_response(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.15,
            max_tokens=120,
        )
        if isinstance(out, dict):
            return str(out.get("content") or "")
        return str(out or "")

    try:
        text = asyncio.run(_call())
    except RuntimeError:
        logger.debug("[Scheduler] LLM 动态间隔跳过（事件循环冲突或非 L3 环境）")
        return None
    except Exception as e:
        logger.debug("[Scheduler] LLM 动态间隔调用失败: %s", e)
        return None

    fragment = text.strip()
    if not fragment.startswith("{"):
        m = re.search(r"\{[^{}]*\}", text.replace("\n", " "))
        fragment = m.group(0) if m else ""
    try:
        obj = json.loads(fragment) if fragment else None
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        rr = int(obj.get("recommend_minutes", heuristic_r))
        hh = int(obj.get("harvest_minutes", heuristic_h))
    except (TypeError, ValueError):
        return None
    rr = max(3, min(45, rr))
    hh = max(1, min(10, hh))
    jk["dynamic_llm_last_ts"] = now
    _save_task_state(st)
    logger.info(
        "[Scheduler][动态调度] LLM 建议 interval 推荐=%smin 收网=%smin (folder=%s)",
        rr,
        hh,
        job_folder,
    )
    return rr, hh


def _maybe_reschedule_dynamic_intervals(job_folder: str, tick_config: dict[str, Any]) -> None:
    if is_recruitment_stopped() or not _APSCHEDULER_AVAILABLE or scheduler is None:
        return
    jf = (job_folder or "").strip()
    if not jf:
        return
    cfg = _reload_job_config_for_tick(jf, tick_config)
    if not _dynamic_schedule_enabled(cfg):
        return
    jn = (cfg.get("job_name") or "").strip()
    if not jn:
        return
    try:
        _ensure_hr_plugin_on_sys_path()
        from tools.hr_dynamic_intervals import compute_intervals_heuristic
    except Exception as e:
        logger.debug("[Scheduler] 动态间隔模块不可用: %s", e)
        return

    pipe = _load_inbox_pipeline_snapshot(jf)
    ic = int(pipe.get("inbox_chat_list_count", -1))
    indry = bool(pipe.get("inbox_no_chats", False))
    base_r = int(cfg.get("recommend_interval_minutes", 15))
    base_h = int(cfg.get("harvest_interval_minutes", 1) or 1)
    enable_greet = bool(cfg.get("enable_greet_recommend", True))

    rim, him = compute_intervals_heuristic(
        base_recommend_minutes=base_r,
        base_harvest_minutes=base_h,
        enable_greet=enable_greet,
        inbox_chat_list_count=ic,
        inbox_no_chats=indry,
    )
    ov = _llm_suggest_hr_interval_minutes(
        jf,
        jn,
        inbox_chat_list_count=ic,
        inbox_no_chats=indry,
        base_r=base_r,
        base_h=base_h,
        heuristic_r=rim,
        heuristic_h=him,
    )
    if ov:
        rim, him = ov

    him_eff = max(1, int(him))
    alt_m = max(1, min(60, (rim + him_eff + 1) // 2))

    st = _load_task_state()
    jk = st.setdefault(jf, {})
    try:
        prev_h = int(jk.get("dynamic_effective_him", -1))
        prev_alt = int(jk.get("dynamic_effective_alt_m", -1))
    except (TypeError, ValueError):
        prev_h, prev_alt = -1, -1

    live_args = [cfg]
    job_id_h = f"rec_{jf}_harvest"
    job_id_a = f"rec_{jf}_alternate"
    has_a = scheduler.get_job(job_id_a) is not None
    has_h_only = (not has_a) and scheduler.get_job(job_id_h) is not None
    if not has_a and not has_h_only:
        return

    changed: list[str] = []
    try:
        if has_a:
            if alt_m == prev_alt:
                return
            _reschedule_hr_interval_job(
                job_id_a,
                job_alternate_greet_harvest,
                alt_m,
                live_args,
                misfire_grace_time=180,
            )
            changed.append(f"交替每{alt_m}min")
        elif has_h_only:
            if him_eff == prev_h:
                return
            _reschedule_hr_interval_job(
                job_id_h,
                job_harvest_resumes,
                him_eff,
                live_args,
                misfire_grace_time=120,
            )
            changed.append(f"仅收网每{him_eff}min")
    except Exception as e:
        logger.warning("[Scheduler] 重调度浏览器任务失败: %s", e)

    if changed:
        st2 = _load_task_state()
        jk2 = st2.setdefault(jf, {})
        jk2["dynamic_effective_rim"] = rim
        jk2["dynamic_effective_him"] = him_eff
        jk2["dynamic_effective_alt_m"] = alt_m
        _save_task_state(st2)
        logger.info(
            "[Scheduler][动态调度] %s (%s) 左侧会话=%s no_chats=%s → %s",
            jn,
            jf,
            ic,
            indry,
            "，".join(changed),
        )
        broadcast_log(
            f"[动态调度] 已按沟通列表调整间隔：{'，'.join(changed)}（左侧会话 {ic}，empty={indry}）",
            "INFO",
        )


def _sanitize_job_folder(job_name: str, max_len: int = 96) -> str:
    """将岗位名转为安全文件夹名"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


def _job_folder_from_job_config(job_config: dict[str, Any]) -> str:
    """
    调度 / DAG 用的磁盘目录键，必须与 ``jd.json`` 所在目录一致（如 ``Python工程师_杭州15-25K``）。

    若仅 ``job_name`` 为「Python 工程师」而 jd 在 canonical 目录，旧逻辑用 ``_sanitize_job_folder(job_name)``
    会得到 **另一套路径**，从而新建 ``Python 工程师/pending``，与主目录分裂。

    解析顺序：显式 ``job_folder`` → ``jd_config_path`` 的父目录名 → 最后才 ``sanitize(job_name)``。
    """
    if not isinstance(job_config, dict):
        return ""
    jf = str(job_config.get("job_folder") or "").strip()
    if jf:
        return _sanitize_job_folder(jf)
    jdp = str(job_config.get("jd_config_path") or "").strip()
    if jdp:
        try:
            jp = Path(jdp).resolve()
            if jp.is_file() and jp.name.lower() == "jd.json":
                return _sanitize_job_folder(jp.parent.name)
        except OSError:
            pass
    jn = str(job_config.get("job_name") or "").strip()
    return _sanitize_job_folder(jn) if jn else ""


def _hr_dag_workflow_id(job_folder: str) -> str:
    """与飞书 inject_signal / 本地指针一致：每岗位独立 workflow id。"""
    return f"hr_recruitment_job_{job_folder}"


def _run_hr_recruitment_dag_tick(
    job_config: dict[str, Any],
    *,
    tick_mode: str,
) -> dict[str, Any]:
    """
    经 DAG 状态机执行一轮收网循环（不打透析镜节点）。

    tick_mode:
        ``greet`` — 仅打招呼（对齐原 job_recommend 单轮，target_resumes=0 冻结算网）
        ``harvest`` — 仅收网（对齐原 job_harvest 单轮，target_greets=0 冻结打招呼）

    返回 dict 含 greeted_count / resume_count（或 downloaded 别名）、success、error。
    """
    try:
        from core.workflow_engine import WorkflowContext
        from l3_node.primitives.skills.hr_recruitment_dag import build_hr_recruitment_dag
    except ImportError as e:
        logger.exception("[Scheduler] 无法加载 HR DAG（需 core + l3_node）: %s", e)
        return {
            "success": False,
            "error": f"hr_dag_import: {e}",
            "greeted_count": 0,
            "resume_count": 0,
            "downloaded": 0,
        }

    job_name = (job_config.get("job_name") or "").strip()
    job_folder = _job_folder_from_job_config(job_config)
    jd_config_path = job_config.get("jd_config_path", "")
    cdp_url = job_config.get("cdp_url", "http://127.0.0.1:9222")
    wid = _hr_dag_workflow_id(job_folder)
    greet_target = int(job_config.get("greet_target", 3))

    if tick_mode == "greet":
        # 必须 cold：热岗在 target_resumes=0 时会跳过「无收网」分支，误进入打招呼前逻辑
        ctx = WorkflowContext(
            {
                "_dag_workflow_id": wid,
                "jd_config_path": jd_config_path,
                "cdp_url": cdp_url,
                "job_folder": job_folder,
                "job_name": job_name,
                "job_heat": "cold",
                "target_greets": greet_target,
                "target_resumes": 0,
                "greeted_count": 0,
                "resume_count": 0,
                "max_greet_per_inner_call": greet_target,
                "cold_greet_inner_rounds": 1,
                "harvest_loop_max_iterations": 1,
                "skip_hr_plan_init_node": True,
                "skip_hr_progress_restore": True,
            }
        )
    elif tick_mode == "harvest":
        save_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
        save_dir.mkdir(parents=True, exist_ok=True)
        output_dir = PLUGIN_DATA_ROOT / job_folder / "result"
        output_dir.mkdir(parents=True, exist_ok=True)
        analyze_threshold = int(job_config.get("analyze_threshold", 4))
        collect_cap = int(job_config.get("resume_collect_target") or analyze_threshold)
        unprocessed_before = _count_unprocessed_pdfs(job_folder, output_dir)
        if not job_config.get("_dag_harvest_always_run") and unprocessed_before >= collect_cap:
            return {
                "success": True,
                "skipped_reason": "resume_full",
                "greeted_count": 0,
                "resume_count": 0,
                "downloaded": 0,
            }

        max_items = int(job_config.get("max_count", 50))
        stop_when = max(0, collect_cap - unprocessed_before)
        filter_tab = job_config.get("filter_tab", "全部") or "全部"
        request_if_no_resume = job_config.get("request_resume", True)
        heat = str(job_config.get("job_heat", "cold") or "cold")

        ctx = WorkflowContext(
            {
                "_dag_workflow_id": wid,
                "jd_config_path": jd_config_path,
                "cdp_url": cdp_url,
                "job_folder": job_folder,
                "job_name": job_name,
                "job_heat": heat,
                "target_greets": 0,
                "target_resumes": max_items,
                "greeted_count": 0,
                "resume_count": 0,
                "inbox_max_items": max_items,
                "inbox_save_dir": str(save_dir),
                "stop_when_downloaded": stop_when if stop_when > 0 else 0,
                "inbox_filter_tab": filter_tab,
                "request_if_no_resume": bool(request_if_no_resume),
                "use_all_positions": bool(job_config.get("use_all_positions", False)),
                "harvest_loop_max_iterations": 1,
                "skip_hr_plan_init_node": True,
                "skip_hr_progress_restore": True,
            }
        )
    else:
        return {
            "success": False,
            "error": f"invalid_tick_mode:{tick_mode}",
            "greeted_count": 0,
            "resume_count": 0,
            "downloaded": 0,
        }

    wf = build_hr_recruitment_dag(wid, include_analyze=False)
    out = wf.run(wid, initial_context=ctx)
    gc = int(out.get("greeted_count", 0) or 0)
    rc = int(out.get("resume_count", 0) or 0)
    st = out.get("status") or out.get("dag_status")
    inbox_cnt = int(out.get("_last_inbox_chat_list_count", -1))
    inbox_nc = bool(out.get("_last_inbox_no_chats", False))
    ih_err = str(out.get("_last_inbox_harvest_error") or "").strip()
    ih_ok = bool(out.get("_last_inbox_harvest_success", True))
    ret_tick: dict[str, Any] = {
        "success": True,
        "greeted_count": gc,
        "resume_count": rc,
        "downloaded": rc,
        "dag_status": st,
    }
    if tick_mode == "harvest":
        ret_tick["inbox_chat_list_count"] = inbox_cnt
        ret_tick["inbox_no_chats"] = inbox_nc
        ret_tick["inbox_harvest_error"] = ih_err
        # 收网 MCP 明确 success=False 时须向上标失败，否则 job_harvest_resumes 误报「✅ 成功」
        if not ih_ok:
            ret_tick["success"] = False
            ret_tick["error"] = ih_err or "收网流程失败（详见日志 atom_inbox_harvester）"
    return ret_tick


# ---------------------------------------------------------------------------
# 定时「仅打招呼」：累计达 N 次成功后结束，不发收网、不注册 check（透析）
# ---------------------------------------------------------------------------
_GREET_ONLY_ZERO_STREAK_MAX = 5


def job_greet_only_campaign(job_config: dict[str, Any]) -> dict[str, Any]:
    """APScheduler：只跑推荐牛人打招呼，累计成功达 ``greet_only_total_target`` 后移除自身并发飞书。"""
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过仅打招呼任务")
        return {"success": False, "skipped": "recruitment_stopped"}

    cfg0 = dict(job_config)
    job_name = (cfg0.get("job_name") or "").strip()
    job_folder = _job_folder_from_job_config(cfg0)
    cfg = _reload_job_config_for_tick(job_folder, cfg0)
    cfg["job_folder"] = job_folder

    total = int(cfg.get("greet_only_total_target") or 0)
    if total <= 0:
        logger.info("[Scheduler] greet_only_total_target 无效，移除仅打招呼任务 folder=%s", job_folder)
        _remove_greet_only_job(job_folder)
        return {"success": False, "error": "invalid_greet_only_total"}

    st = _load_task_state()
    jk = st.setdefault(job_folder, {})
    done = int(jk.get("hr_greet_only_done", 0) or 0)
    remaining = total - done
    if remaining <= 0:
        _finish_greet_only_campaign(job_folder, job_name, total, cfg, early=False, done_override=done)
        return {"success": True, "greet_only_finished": True, "greeted_total": done}

    # 仅打招呼战役：每 tick 一次应打满「剩余次数」（经 _tick_greet_cap → atom 的 max_greet_per_run）。
    # 交替调度里 greet_target=3 表示「每轮最多 3 人再换收网」；此处若仍 min(greet_target, remaining)，
    # 则「仅打招呼 20 人」会变成每间隔只打 3 人，与产品预期不符。
    per_tick = max(1, min(int(remaining), 9999))
    cfg2 = dict(cfg)
    cfg2["_tick_greet_cap"] = per_tick

    broadcast_log(
        f"[仅打招呼] 职位={job_name} 进度 {done}/{total} 本轮目标={per_tick}（单次 tick 打满剩余，直至列表耗尽或 Boss 限制）",
        "INFO",
    )
    ret = job_recommend_candidates(cfg2)
    n = int(ret.get("greeted_count", 0) or 0) if isinstance(ret, dict) else 0

    if isinstance(ret, dict) and ret.get("success"):
        if n > 0:
            jk["hr_greet_only_done"] = done + n
            jk["hr_greet_only_zero_streak"] = 0
            done = done + n
        else:
            zs = int(jk.get("hr_greet_only_zero_streak", 0) or 0) + 1
            jk["hr_greet_only_zero_streak"] = zs
            if zs >= _GREET_ONLY_ZERO_STREAK_MAX:
                _save_task_state(st)
                _finish_greet_only_campaign(
                    job_folder, job_name, total, cfg, early=True, done_override=done
                )
                return {
                    **ret,
                    "greet_only_early_stop": True,
                    "greet_only_zero_streak": zs,
                    "greet_only_done": done,
                }
    _save_task_state(st)

    if done >= total:
        _finish_greet_only_campaign(job_folder, job_name, total, cfg, early=False, done_override=done)
        return {**ret, "greet_only_finished": True, "greet_only_done": done} if isinstance(ret, dict) else ret

    return ret if isinstance(ret, dict) else {"success": False}


# ---------------------------------------------------------------------------
# 默认模式：推荐打招呼 ↔ 沟通收简历 严格交替（单一定时任务）
# ---------------------------------------------------------------------------
def job_alternate_greet_harvest(job_config: dict[str, Any]) -> dict[str, Any]:
    """
    Boss 沟通/推荐 **同一浏览器页面**，因此 **同一时间只跑一种动作**：单 APScheduler 任务内严格交替
    「牛人沟通（推荐/打招呼）→ 抓简历（沟通页）→ …」。

    - 基准节奏：``greet_harvest_switch_interval_minutes``（默认 10）为「上一轮结束到下一轮若未提前触发」的间隔。
    - **提前交替**：本轮打招呼人数已达 ``greet_target`` → 不必等满间隔，约数秒后即进入收网；
      本轮收网下载 0 且沟通列表为空（无可聊会话）→ 约数秒后即回到打招呼。
    """
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过交替调度任务")
        return {"success": False, "skipped": "recruitment_stopped"}

    cfg0 = dict(job_config)
    job_name = (cfg0.get("job_name") or "").strip()
    job_folder = _job_folder_from_job_config(cfg0)
    cfg = _reload_job_config_for_tick(job_folder, cfg0)
    cfg["job_folder"] = job_folder
    cfg["_alternate_scheduler"] = True

    enable_greet = bool(cfg.get("enable_greet_recommend", True))
    if not enable_greet:
        return job_harvest_resumes(cfg)

    st = _load_task_state()
    jk = st.setdefault(job_folder, {})
    next_greet = bool(jk.get("hr_alternate_next_greet", True))
    prev_was_greet = next_greet

    phase = "牛人沟通（推荐/打招呼）" if next_greet else "抓简历（沟通页收网）"
    broadcast_log(
        f"[交替调度] 本轮={phase} 职位={job_name} folder={job_folder}",
        "INFO",
    )
    ret: dict[str, Any] = {}
    try:
        if next_greet:
            ret = job_recommend_candidates(cfg)
        else:
            ret = job_harvest_resumes(cfg)
    finally:
        jk["hr_alternate_next_greet"] = not next_greet
        _save_task_state(st)

    try:
        early_sec_greet = int(os.environ.get("HR_ALTERNATE_EARLY_AFTER_GREET_SEC", "5") or "5")
        early_sec_harv = int(os.environ.get("HR_ALTERNATE_EARLY_AFTER_HARVEST_SEC", "5") or "5")
    except (TypeError, ValueError):
        early_sec_greet, early_sec_harv = 5, 5

    if _APSCHEDULER_AVAILABLE and scheduler is not None and scheduler.get_job(f"rec_{job_folder}_alternate"):
        if prev_was_greet and isinstance(ret, dict) and ret.get("success"):
            n = int(ret.get("greeted_count", 0) or 0)
            gt = max(1, int(cfg.get("greet_target", 3) or 3))
            if n >= gt:
                broadcast_log(
                    f"[交替调度] 已达本轮打招呼上限 {n}/{gt}，提前进入收网（约 {early_sec_greet}s 后）",
                    "INFO",
                )
                _modify_alternate_next_run(job_folder, seconds=early_sec_greet)
        elif (not prev_was_greet) and isinstance(ret, dict) and ret.get("success"):
            if ret.get("skipped_reason") == "resume_full":
                pass
            else:
                dl = int(ret.get("downloaded", 0) or 0)
                ic = int(ret.get("inbox_chat_list_count", -1))
                indry = bool(ret.get("inbox_no_chats", False))
                empty_inbox = indry or (ic >= 0 and ic == 0)
                if dl == 0 and empty_inbox:
                    broadcast_log(
                        f"[交替调度] 沟通侧暂无可抓会话（empty={empty_inbox}），提前回到牛人沟通（约 {early_sec_harv}s 后）",
                        "INFO",
                    )
                    _modify_alternate_next_run(job_folder, seconds=early_sec_harv)
    _maybe_reschedule_dynamic_intervals(job_folder, cfg)
    return ret if isinstance(ret, dict) else {"success": False}


# ---------------------------------------------------------------------------
# Job 1: 定时推荐牛人（打招呼 MCP）
# 仅 **并行模式** 下作为独立 interval 任务；默认模式已改用 job_alternate_greet_harvest，不再做「打满 N 人再 20s 后挂收网」。
# ---------------------------------------------------------------------------
def job_recommend_candidates(job_config: dict[str, Any]) -> dict[str, Any]:
    """经 DAG 执行一轮推荐牛人打招呼。不再修改 APScheduler（旧版满员衔接收网已废弃）。"""
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过推荐牛人任务")
        return {"success": False, "greeted_count": 0, "skipped": "recruitment_stopped"}
    cfg0 = dict(job_config)
    job_name = (cfg0.get("job_name") or "").strip()
    job_folder = _job_folder_from_job_config(cfg0)
    cfg = _reload_job_config_for_tick(job_folder, cfg0)
    cfg["job_folder"] = job_folder
    _cap = cfg0.get("_tick_greet_cap")
    if _cap is not None:
        try:
            cfg["greet_target"] = max(1, int(_cap))
        except (TypeError, ValueError):
            pass

    jd_config_path = cfg.get("jd_config_path", "")
    _sync_hr_workflow_pointer_for_lark({**cfg, "job_folder": job_folder})
    broadcast_log(f"[推荐牛人] 职位={job_name} jd={jd_config_path or '(未配置)'}", "INFO")
    ret: dict[str, Any] = {"success": False, "greeted_count": 0}
    with chrome_lock:
        broadcast_log("[推荐牛人] 🟢 成功获取锁！经 DAG 状态机执行打招呼 tick...", "SUCCESS")
        try:
            result = _run_hr_recruitment_dag_tick(cfg, tick_mode="greet")
            err = (result.get("error") or "").strip()
            if err:
                broadcast_log(f"[推荐牛人] ❌ DAG 执行失败: {err}", "ERROR")
                logger.warning("[Scheduler] job_recommend_candidates DAG 失败: %s", result)
                ret = {"success": False, "greeted_count": 0, "error": err}
            else:
                n = int(result.get("greeted_count", 0))
                broadcast_log(f"[推荐牛人] ✅ 任务执行成功，已推荐 {n} 人。", "SUCCESS")
                logger.info("[Scheduler] [%s] 推荐牛人(DAG)完成: %s", job_name or "default", result)

                ret = {"success": True, "greeted_count": int(result.get("greeted_count", 0)), "dag_status": result.get("dag_status")}
        except Exception as e:
            broadcast_log(f"[推荐牛人] ❌ 任务失败: {str(e)}", "ERROR")
            logger.warning("[Scheduler] job_recommend_candidates 失败: %s", e)
            ret = {"success": False, "greeted_count": 0, "error": str(e)}
        finally:
            broadcast_log("[推荐牛人] 🔓 已释放 Chrome 浏览器控制权。", "INFO")
    if ret.get("success"):
        _hr_audit(
            "greet_tick_completed",
            job_folder=job_folder,
            job_name=job_name,
            detail={"greeted_count": int(ret.get("greeted_count", 0) or 0)},
        )
    _maybe_reschedule_dynamic_intervals(job_folder, cfg)
    return ret


# ---------------------------------------------------------------------------
# Job 2: 定时收网抓取（简历抓取 MCP）
# ---------------------------------------------------------------------------
def _notify_lark_harvest_progress_if_configured(job_name: str, job_folder: str = "") -> None:
    """收网 tick 结束后向飞书推送「抓取简历 n/m」。"""
    try:
        from l3_node.channels.lark.hr_recruitment_notify import (
            format_hr_recruitment_progress_line_for_lark,
            send_hr_recruitment_progress_message,
        )

        line = format_hr_recruitment_progress_line_for_lark(job_name, job_folder=job_folder)
        if not line:
            return
        jn = (job_name or "").strip() or "招聘任务"
        tech = ""
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is not None and hasattr(rs, "get_harvest_progress_snapshot"):
                n, cap = rs.get_harvest_progress_snapshot(job_name, job_folder=job_folder)
                tech = (
                    f"pending_pdf_count={n} resume_collect_target={cap} "
                    f"job_name={job_name!r} job_folder={job_folder!r}"
                )
        except Exception:
            pass
        send_hr_recruitment_progress_message(
            f"【{jn}】{line}",
            technical_detail=tech.strip() or None,
            message_kind="hr_harvest_tick",
        )
    except Exception:
        pass


def _spawn_lark_harvest_progress_notify(job_name: str, job_folder: str = "") -> None:
    """
    收网 tick 后的飞书通知内含同步 LLM 润色，可能数十秒；
    必须在 **不持有 chrome_lock** 时执行，且勿阻塞 APScheduler worker，故放后台线程。
    """
    jn = (job_name or "").strip()
    if not jn:
        return
    jf = (job_folder or "").strip()

    def _run() -> None:
        try:
            _notify_lark_harvest_progress_if_configured(jn, job_folder=jf)
        except Exception:
            logger.exception("[Scheduler] 异步飞书收网进度通知失败 job=%s", jn[:48])

    threading.Thread(
        target=_run,
        daemon=True,
        name="lark-harvest-progress",
    ).start()


def _job_text_for_harvest(job_config: dict[str, Any]) -> str:
    """从 jd.json 获取 jd_select 用于 Boss「全部职位」精确匹配（格式：岗位名称 _ 杭州 最低-最高K）"""
    job_name = (job_config.get("job_name") or "").strip()
    jd_path = job_config.get("jd_config_path", "")
    if not jd_path or not Path(jd_path).exists():
        return job_name
    try:
        _hr = _get_hr_recruitment_plugin_root()
        if _hr and str(_hr) not in sys.path:
            sys.path.insert(0, str(_hr))
        from tools.atom_post_job_boss import load_jd_config, get_jd_select
        jd_data = load_jd_config(jd_path, job_name)
        return get_jd_select(jd_data) or job_name
    except Exception:
        return job_name


def job_harvest_resumes(job_config: dict[str, Any]) -> dict[str, Any]:
    """经 ``build_hr_recruitment_dag(...).run`` 执行收网 tick（非直接 atom）。
    在「全部职位」中选择 jd_select 对应岗位，仅抓取该岗位简历。"""
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过收网抓取任务")
        return {"success": False, "downloaded": 0, "skipped": "recruitment_stopped"}
    job_name = job_config.get("job_name", "")
    job_text = _job_text_for_harvest(job_config)
    job_folder = _job_folder_from_job_config(job_config)
    job_config = dict(job_config)
    job_config["job_folder"] = job_folder
    _sync_hr_workflow_pointer_for_lark(job_config)
    save_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    save_dir.mkdir(parents=True, exist_ok=True)
    output_dir = PLUGIN_DATA_ROOT / job_folder / "result"
    output_dir.mkdir(parents=True, exist_ok=True)
    analyze_threshold = int(job_config.get("analyze_threshold", 4))
    collect_cap = int(job_config.get("resume_collect_target") or analyze_threshold)

    broadcast_log(f"[收网抓取] 职位={job_name} folder={job_folder} job_select={job_text}", "INFO")
    ret: dict[str, Any] = {"success": False, "downloaded": 0}
    lark_harvest_notify_after_unlock = False
    with chrome_lock:
        broadcast_log(f"[收网抓取] 🟢 成功获取锁！经 DAG 状态机执行收网 tick，目标职位: {job_text}", "SUCCESS")
        try:
            # 已有未处理简历数，若已达「收网目标份数」则跳过抓取
            unprocessed_before = _count_unprocessed_pdfs(job_folder, output_dir)
            if unprocessed_before >= collect_cap:
                broadcast_log(
                    f"[收网抓取] 简历已达收集目标 {unprocessed_before}/{collect_cap} 份，跳过抓取",
                    "INFO",
                )
                if _APSCHEDULER_AVAILABLE:
                    try:
                        _remove_greet_harvest_browser_jobs(job_folder)
                        broadcast_log("[收网抓取] 已停止打招呼/收网/交替任务", "SUCCESS")
                    except Exception:
                        pass
                lark_harvest_notify_after_unlock = True
                ret = {"success": True, "downloaded": 0, "skipped_reason": "resume_full"}
            else:
                result = _run_hr_recruitment_dag_tick(job_config, tick_mode="harvest")
                if result.get("skipped_reason") == "resume_full":
                    broadcast_log("[收网抓取] 简历已满，跳过抓取（DAG 二次校验）", "INFO")
                    lark_harvest_notify_after_unlock = True
                    ret = {"success": True, "downloaded": 0, "skipped_reason": "resume_full"}
                else:
                    err = (result.get("error") or "").strip()
                    if err:
                        broadcast_log(f"[收网抓取] ❌ DAG 失败: {err}", "ERROR")
                        ret = {"success": False, "downloaded": 0, "error": err}
                    else:
                        n = int(result.get("downloaded", 0))
                        broadcast_log(f"[收网抓取] ✅ 任务执行成功，已下载 {n} 份简历。", "SUCCESS")
                        logger.info("[Scheduler] [%s] 收网抓取(DAG)完成: %s", job_name or "default", result)
                        lark_harvest_notify_after_unlock = True
                        ret = {
                            "success": True,
                            "downloaded": n,
                            "dag_status": result.get("dag_status"),
                            "inbox_chat_list_count": int(result.get("inbox_chat_list_count", -1)),
                            "inbox_no_chats": bool(result.get("inbox_no_chats", False)),
                            "inbox_harvest_error": str(result.get("inbox_harvest_error") or "").strip(),
                        }
        except Exception as e:
            broadcast_log(f"[收网抓取] ❌ 任务失败: {str(e)}", "ERROR")
            logger.warning("[Scheduler] job_harvest_resumes 失败: %s", e)
            ret = {"success": False, "downloaded": 0, "error": str(e)}
        finally:
            broadcast_log("[收网抓取] 🔓 已释放 Chrome 浏览器控制权。", "INFO")
    if lark_harvest_notify_after_unlock:
        _spawn_lark_harvest_progress_notify(job_name, job_folder=job_folder)
    if ret.get("success") and "skipped_reason" not in ret:
        _persist_inbox_pipeline_snapshot(job_folder, ret)
        _hr_audit(
            "harvest_tick_completed",
            job_folder=job_folder,
            job_name=job_name,
            detail={
                "downloaded": int(ret.get("downloaded", 0) or 0),
                "inbox_chat_list_count": ret.get("inbox_chat_list_count"),
                "inbox_no_chats": ret.get("inbox_no_chats"),
            },
        )
    _maybe_reschedule_dynamic_intervals(job_folder, job_config)
    return ret


# ---------------------------------------------------------------------------
# Job 3: 动态规则引擎与分析触发器
# ---------------------------------------------------------------------------
def _count_unprocessed_pdfs(job_folder: str, output_dir: Path) -> int:
    """统计未处理 PDF 数量：hr_recruitment/{职位}/pending 下 PDF 数 - output_dir 下 *_analysis.md 数"""
    pending_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    if not pending_dir.exists():
        return 0
    pdf_count = len(list(pending_dir.rglob("*.pdf")))
    analysis_count = len(list(output_dir.glob("*_analysis.md")))
    return max(0, pdf_count - analysis_count)


def _scheduler_hr_max_collect_files() -> int:
    try:
        from recruitment_task import _hr_max_collect_files

        return _hr_max_collect_files()
    except Exception:
        try:
            return max(1, min(500, int(os.environ.get("HR_ANALYZER_MAX_FILES", "200"))))
        except ValueError:
            return 200


def _pdfs_missing_analysis_reports(job_folder: str, output_dir: Path) -> list[Path]:
    """
    与 job_check 相同的简历枚举来源，但只保留「result 下尚无 {stem}_analysis.md」的 PDF。
    透析应「有多少缺报告就分析多少」，并在同一次互斥锁内多轮续跑直至无缺口或无法进展。
    """
    pending_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    cap = _scheduler_hr_max_collect_files()
    try:
        from tools.hr_data_paths import collect_resume_paths_for_analysis

        pdf_paths, _ = collect_resume_paths_for_analysis(
            primary_dir=pending_dir,
            max_files=cap,
            extensions=frozenset({".pdf"}),
        )
    except Exception as e:
        logger.warning("[Scheduler] _pdfs_missing_analysis_reports collect 失败，回退 pending rglob: %s", e)
        pdf_paths = (
            [p.resolve() for p in pending_dir.rglob("*.pdf") if p.is_file()]
            if pending_dir.exists()
            else []
        )
    missing: list[Path] = []
    for p in pdf_paths:
        md = output_dir / f"{p.stem}_analysis.md"
        if not md.is_file():
            missing.append(p)
    return missing


def _run_wasm_analysis_sequential(
    base_input: dict[str, Any],
    pdf_paths: list[Path],
    output_dir: Path,
    job_folder: str,
) -> tuple[list[dict], list[dict], list[dict[str, Any]]]:
    """
    多份 PDF 时逐份调用 run_tool，避免单次 Wasm 内线性内存/堆在 N 较大时 OOB（与 recruitment_task、hr_analyze_resume 默认一致）。
    批量单实例仅当环境变量 HR_ANALYZER_BATCH_WASM=1。
    返回 (passed_list, eliminated_list, failed_items)；failed_items 符合 RunReport.failed_items 形状。
    """
    import time as _time

    _ensure_hr_plugin_on_sys_path()
    from l3_node.primitives import run_tool
    from recruitment_task import _append_verdict_from_recruitment_report

    passed_list: list[dict] = []
    eliminated_list: list[dict] = []
    failed_items: list[dict[str, Any]] = []
    ntot = len(pdf_paths)
    broadcast_log(
        f"[透析镜] 逐份模式：共 {ntot} 份 PDF，每份独立 Wasm（防内存越界）；"
        f"单次批量请设 HR_ANALYZER_BATCH_WASM=1",
        "INFO",
    )
    for idx, p in enumerate(pdf_paths, 1):
        one_input = {**base_input, "_hr_files": str(p).replace("\\", "/")}
        inp = json.dumps({**one_input, "capability": "execute"}, ensure_ascii=False)
        t0 = _time.perf_counter()
        try:
            r = run_tool(HR_SKILL_ID, inp, allowed_skills=None, ndjson_queue=None) or ""
        except Exception as e:
            logger.exception("[Scheduler] 透析镜逐份失败 (%d/%d) %s: %s", idx, ntot, p.name, e)
            broadcast_log(f"[透析镜] ⚠️ 第 {idx}/{ntot} 份失败: {p.name} — {e}", "WARNING")
            failed_items.append(
                {
                    "id": p.name,
                    "stage": "wasm",
                    "error_class": classify_wasm_error_message(str(e)),
                    "message": str(e)[:240],
                }
            )
            continue
        dt = _time.perf_counter() - t0
        md_file = output_dir / f"{p.stem}_analysis.md"
        report = md_file.read_text(encoding="utf-8", errors="replace") if md_file.is_file() else ""
        md_path_str = str(md_file.resolve()) if md_file.is_file() else ""
        if not report.strip() and r:
            logger.warning(
                "[Scheduler] 逐份 %d/%d %s 未读到 %s，run_tool 预览: %s",
                idx,
                ntot,
                p.name,
                md_file.name,
                (r or "")[:220],
            )
        if not md_file.is_file():
            failed_items.append(
                {
                    "id": p.name,
                    "stage": "persist",
                    "error_class": classify_wasm_error_message(str(r or "")),
                    "message": (str(r)[:240] if r else "no_analysis_md_written"),
                }
            )
            broadcast_log(f"[透析镜] ⚠️ 第 {idx}/{ntot} 份未落盘: {p.name}", "WARNING")
        elif not report.strip() and _wasm_return_indicates_failure(r):
            failed_items.append(
                {
                    "id": p.name,
                    "stage": "llm",
                    "error_class": classify_wasm_error_message(str(r)),
                    "message": (str(r)[:240]),
                }
            )
            broadcast_log(f"[透析镜] ⚠️ 第 {idx}/{ntot} 份 Wasm/LLM 返回失败: {p.name}", "WARNING")
        _append_verdict_from_recruitment_report(
            report, p.name, str(p), md_path_str, passed_list, eliminated_list
        )
        sz = md_file.stat().st_size if md_file.is_file() else 0
        logger.info(
            "[Scheduler] 透析镜逐份 (%d/%d) 完成 %s 耗时 %.1fs 落盘 %s (%d bytes)",
            idx,
            ntot,
            p.name,
            dt,
            md_file.name,
            sz,
        )
        if md_file.is_file() and (report.strip() or not _wasm_return_indicates_failure(r)):
            broadcast_log(f"[透析镜] 进度 {idx}/{ntot} ✅ {p.name}（{dt:.0f}s）", "INFO")
    return passed_list, eliminated_list, failed_items


def _load_jd_from_config_path(jd_config_path: str, job_name: str) -> str:
    """从 data/{职位}/jd.json 加载完整 JD 内容，供透析镜分析使用。确保「当前职位」不丢失。"""
    if not jd_config_path or not Path(jd_config_path).exists():
        return ""
    try:
        _hr = _get_hr_recruitment_plugin_root()
        if _hr and str(_hr) not in sys.path:
            sys.path.insert(0, str(_hr))
        from tools.atom_post_job_boss import load_jd_config
        jd = load_jd_config(jd_config_path, job_name)
        if not jd:
            return ""
        jd_full = (jd.get("jd_full") or "").strip()
        if jd_full:
            title = (jd.get("job_title") or job_name or "").strip()
            if title:
                return f"岗位：{title}\n\n【学历】{jd.get('education', '不限')} 【经验】{jd.get('experience', '不限')}\n\n{jd_full}"
            return jd_full
        return ""
    except Exception as e:
        logger.warning("[Scheduler] 从 jd_config_path 加载 JD 失败: %s", e)
        return ""


def _run_wasm_analysis_sync(
    job_config: dict[str, Any],
    pdf_paths: list[Path],
    output_dir: Path,
    job_folder: str,
) -> tuple[list[dict], list[dict], list[dict[str, Any]], dict[str, Any]]:
    """同步执行 Wasm 分析。返回 (passed_list, eliminated_list, failed_items, meta)；failed_items 对齐 RunReport。"""
    import queue

    _ensure_hr_plugin_on_sys_path()
    from recruitment_task import (
        _job_name_fallback_jd,
        _fetch_jd_from_db,
        _save_jd_to_db,
    )
    from l3_node.primitives import run_tool
    from l3_node.primitives.tools.loader import _extract_stem_from_hr_report
    from hr_analysis_persist import persist_hr_analysis_batch_item

    job_name = job_config.get("job_name", "")
    jd_config_path = job_config.get("jd_config_path", "")

    if jd_config_path and Path(jd_config_path).is_file():
        try:
            from tools.jd_full_llm import ensure_jd_full_via_llm_sync

            _jd_llm_extra = (job_config.get("jd_llm_extra_context") or "").strip()
            _jd_llm_r = ensure_jd_full_via_llm_sync(
                jd_config_path, job_name, extra_context=_jd_llm_extra
            )
            if _jd_llm_r.get("written"):
                logger.info(
                    "[Scheduler] 无发帖/空 jd_full：已由 LLM 生成并落盘 jd.json path=%s",
                    jd_config_path,
                )
        except Exception as e:
            logger.warning("[Scheduler] ensure_jd_full_via_llm 失败（将用兜底 JD）: %s", e)

    jd_content = (job_config.get("jd_content") or "").strip()
    if not jd_content and jd_config_path:
        jd_content = _load_jd_from_config_path(jd_config_path, job_name)
    jd_final = jd_content or _job_name_fallback_jd(job_name) or _fetch_jd_from_db() or "岗位：请根据岗位名称评估候选人匹配度。"
    if jd_content:
        _save_jd_to_db(jd_content)

    logger.info("[Scheduler] 透析镜分析 职位=%s folder=%s jd来源=%s", job_name, job_folder, "jd_config_path" if jd_config_path and jd_content else "fallback")
    broadcast_log(
        f"[透析镜] Wasm 线程即将启动：共 {len(pdf_paths)} 份 PDF，输出目录 result → {output_dir}",
        "INFO",
    )

    pending_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    base_input: dict[str, Any] = {
        "target_dir": str(pending_dir),
        "jd_template": jd_final,
        "job_name": (job_name or "").strip(),
        "strictness": job_config.get("strictness", "standard"),
        "output_dir": str(output_dir),
    }
    focus_keywords = job_config.get("focus_keywords", "")
    if focus_keywords:
        base_input["focus_keywords"] = focus_keywords

    force_batch = os.environ.get("HR_ANALYZER_BATCH_WASM", "").strip().lower() in ("1", "true", "yes")
    env_seq = os.environ.get("HR_ANALYZER_SEQUENTIAL_WASM", "").strip().lower() in ("1", "true", "yes")
    use_sequential = env_seq or (not force_batch and len(pdf_paths) > 1)
    if use_sequential:
        pl, el, fi = _run_wasm_analysis_sequential(base_input, pdf_paths, output_dir, job_folder)
        return pl, el, fi, {}

    paths_str = "|||".join(str(p).replace("\\", "/") for p in pdf_paths if p)
    input_data: dict[str, Any] = {**base_input, "_hr_files": paths_str}

    ndjson_queue: queue.Queue[str] = queue.Queue()
    thread_result: dict[str, Any] = {"done": False, "error": None, "wasm_return": None}

    def _run() -> None:
        try:
            inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
            thread_result["wasm_return"] = run_tool(HR_SKILL_ID, inp, allowed_skills=None, ndjson_queue=ndjson_queue)
        except Exception as e:
            thread_result["error"] = str(e)
        finally:
            thread_result["done"] = True
            ndjson_queue.put(json.dumps({"status": "thread_done"}))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info(
        "[Scheduler] 透析镜 Wasm 线程已启动 pdf=%d 预览=%s",
        len(pdf_paths),
        ", ".join(p.name for p in pdf_paths[:6]) + (" …" if len(pdf_paths) > 6 else ""),
    )

    passed_list: list[dict] = []
    eliminated_list: list[dict] = []
    _progress_seq = 0

    def _consume_ndjson_item(item: dict) -> None:
        nonlocal passed_list, eliminated_list, _progress_seq
        if item.get("status") != "progress":
            return
        _progress_seq += 1
        report = item.get("report_content")
        fn = item.get("filename", "")
        pdf_path = next((str(p) for p in pdf_paths if fn and (str(p).endswith(fn) or Path(p).name == fn)), "")
        stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
        if not stem or re.match(r"^resume_\d+$", stem):
            stem = _extract_stem_from_hr_report(report or "") or stem or "unknown"
        md_path = ""
        if report:
            out_dir_str = str(output_dir.resolve()).replace("\\", "/")
            md_path = persist_hr_analysis_batch_item(
                HR_SKILL_ID,
                report,
                stem,
                config={
                    "output_dir": out_dir_str,
                    "output_dir_use_absolute": True,
                    "use_absolute_path": True,
                },
            ) or ""
            if not md_path:
                logger.warning(
                    "[Scheduler] 透析镜 persist 未写入 path stem=%s out_dir=%s（请查 ~/.jachin/workspace/hr_analysis 兜底）",
                    stem[:60],
                    out_dir_str[:120],
                )
        pass_match = RE_SUMMARY_PASS.search(report or "") if report else None
        reject_match = None
        fields = _extract_candidate_fields(report or "") if report else {}
        if pass_match:
            try:
                score = float((pass_match.group(2) or "0").strip())
            except (ValueError, TypeError):
                score = 0.0
            passed_list.append(
                {
                    "name": (pass_match.group(1) or "").strip(),
                    "score": score,
                    "advantage": (pass_match.group(3) or "").strip(),
                    "pdf_path": pdf_path,
                    "md_path": md_path,
                    "education": fields.get("education", "-"),
                    "experience": fields.get("experience", "-"),
                    "salary": fields.get("salary", "-"),
                    "stars": _score_to_stars(score),
                }
            )
        else:
            reject_match = RE_SUMMARY_REJECT.search(report or "") if report else None
            if reject_match:
                try:
                    score = float((reject_match.group(2) or "0").strip())
                except (ValueError, TypeError):
                    score = 0.0
                eliminated_list.append(
                    {
                        "name": (reject_match.group(1) or "").strip(),
                        "score": score,
                        "reason": (reject_match.group(3) or "").strip(),
                        "pdf_path": pdf_path,
                        "md_path": md_path,
                        "education": fields.get("education", "-"),
                        "experience": fields.get("experience", "-"),
                        "salary": fields.get("salary", "-"),
                        "stars": _score_to_stars(score),
                    }
                )
            elif report and not report.strip().startswith("⚠️"):
                fallback_name = ""
                for m in RE_REPORT_TITLE.finditer(report):
                    fallback_name = (m.group(1) or "").strip()
                    if fallback_name and len(fallback_name) < 20:
                        break
                if not fallback_name and stem:
                    for pat in [r"】([^_]+)_", r"】(.+?)(?:_\d+[a-f0-9]*)?$", r"^(.+?)_"]:
                        m = re.search(pat, stem)
                        if m:
                            fallback_name = (m.group(1) or "").strip()
                            if fallback_name and len(fallback_name) < 30:
                                break
                if not fallback_name:
                    fallback_name = stem or fn or "未知"
                score = 0.0
                for m in RE_SCORE_IN_REPORT.finditer(report):
                    try:
                        score = float(m.group(1))
                        break
                    except (ValueError, TypeError):
                        pass
                eliminated_list.append(
                    {
                        "name": fallback_name[:30],
                        "score": score,
                        "reason": "报告格式未包含标准 SUMMARY 块，需人工复核",
                        "pdf_path": pdf_path,
                        "md_path": md_path,
                        "education": fields.get("education", "-"),
                        "experience": fields.get("experience", "-"),
                        "salary": fields.get("salary", "-"),
                        "stars": _score_to_stars(score),
                    }
                )
                logger.info("[Scheduler] 透析镜兜底：从报告提取 name=%s score=%.1f（无 SUMMARY 块）", fallback_name, score)
        verdict = "PASS" if pass_match else ("REJECT" if reject_match else "FALLBACK")
        if report and str(report).strip().startswith("⚠️"):
            verdict = "READ_FAIL_OR_WARN"
        logger.info(
            "[Scheduler] 透析镜 第 %d 份 progress 已消费 filename=%s stem=%s persist_md=%s verdict=%s",
            _progress_seq,
            (fn or "-")[:80],
            (stem or "-")[:50],
            "yes" if md_path else "no",
            verdict,
        )

    while not thread_result["done"] or not ndjson_queue.empty():
        try:
            line = ndjson_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            item = json.loads((line or "").strip())
        except json.JSONDecodeError as je:
            _lp = (line or "")[:500]
            logger.warning(
                "[Scheduler] 透析镜 NDJSON 行解析失败（该份可能未落盘 result，请重编 hr-analyzer4 wasm 或检查简历文本）: %s prefix=%r",
                je,
                _lp + ("…" if len(line or "") > 500 else ""),
            )
            continue
        if not isinstance(item, dict):
            logger.debug("[Scheduler] 透析镜 NDJSON 跳过非对象: %s", type(item).__name__)
            continue
        if item.get("status") == "thread_done":
            break
        _consume_ndjson_item(item)

    while True:
        try:
            line = ndjson_queue.get_nowait()
        except queue.Empty:
            break
        try:
            item = json.loads((line or "").strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("status") == "thread_done":
            continue
        _consume_ndjson_item(item)

    t.join(timeout=7200.0)
    if t.is_alive():
        logger.error("[Scheduler] 透析镜 Wasm 线程 join 超时后仍在运行（可能 LLM 卡住）")
    wr = thread_result.get("wasm_return")
    thr_err = thread_result.get("error")
    if thr_err:
        # 不在此抛错，避免整次 job_check 被 except 截断，外层多轮续跑无法继续（部分成功应自动补齐剩余 PDF）。
        logger.error(
            "[Scheduler] 透析镜 Wasm 线程异常（已保留本批已消费的 %d 条 progress，续跑将处理仍缺报告的 PDF）: %s",
            _progress_seq,
            thr_err,
        )
        broadcast_log(
            f"[透析镜] ⚠️ 单次 Wasm 线程异常，不取消后续任务：已落盘部分保留，将由续跑补齐。{str(thr_err)[:180]}",
            "WARNING",
        )
    if wr and isinstance(wr, str):
        _wr_st = wr.strip()
        if _wasm_return_indicates_failure(_wr_st):
            logger.warning(
                "[Scheduler] 透析镜 run_tool 返回失败串（已写入的 NDJSON 仍有效；续跑仅处理仍缺 *_analysis.md 的 PDF）: %s",
                _wr_st[:500],
            )
            broadcast_log(
                "[透析镜] ⚠️ Wasm 返回错误串，续跑逻辑继续；建议默认逐份模式，避免 HR_ANALYZER_BATCH_WASM 单实例爆内存",
                "WARNING",
            )
        else:
            logger.info("[Scheduler] 透析镜 run_tool 返回预览: %s", _wr_st[:500])

    # Wasm 若未调用 host_stream_ndjson，队列无 progress，但 run_tool 仍可能返回完整 Markdown。
    # 此时从返回值兜底：嵌入的 NDJSON 行 → 按 # 📄 切分 → 单段多 PDF 时按文件各落一条。
    if not passed_list and not eliminated_list and isinstance(wr, str) and len(wr.strip()) > 30:
        wrs = wr.strip()
        embedded = 0
        for line in wrs.splitlines():
            s = line.strip()
            if not s.startswith("{"):
                continue
            try:
                o = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict) and o.get("status") == "progress":
                _consume_ndjson_item(o)
                embedded += 1
        if embedded:
            logger.info("[Scheduler] 透析镜 fallback：从 run_tool 文本中解析 %d 条 progress NDJSON", embedded)

        if not passed_list and not eliminated_list:
            parts = [p.strip() for p in re.split(r"(?m)(?=^#\s*📄)", wrs) if p.strip()]
            if len(parts) > 1:
                logger.warning(
                    "[Scheduler] 透析镜 fallback：未收到 host_stream_ndjson，按 Markdown「# 📄」切为 %d 段并与 PDF 对齐（建议重编 hr-analyzer4 以逐份 stream）",
                    len(parts),
                )
                for i, pp in enumerate(pdf_paths):
                    if i >= len(parts):
                        break
                    _consume_ndjson_item(
                        {"status": "progress", "report_content": parts[i], "filename": pp.name}
                    )
            else:
                body = parts[0] if parts else wrs
                if body.strip():
                    if len(pdf_paths) > 1:
                        logger.warning(
                            "[Scheduler] 透析镜 fallback：仅 1 段报告 + %d 份 PDF（Wasm 可能未逐份调用 host_stream_ndjson）；"
                            "将按文件名各写入一条相同正文，便于琅琊榜占位与排查 LLM/插件",
                            len(pdf_paths),
                        )
                        for pp in pdf_paths:
                            _consume_ndjson_item(
                                {"status": "progress", "report_content": body, "filename": pp.name}
                            )
                    elif pdf_paths:
                        _consume_ndjson_item(
                            {"status": "progress", "report_content": body, "filename": pdf_paths[0].name}
                        )

    if not passed_list and not eliminated_list:
        logger.warning(
            "[Scheduler] 透析镜 Wasm 线程已结束但无任何录用/淘汰条目（progress 事件数=%d）。"
            "若 NDJSON 解析失败或 persist 失败，见上文 WARNING。run_tool 摘要: %s",
            _progress_seq,
            (wr[:300] if isinstance(wr, str) else wr),
        )
        broadcast_log(
            "[透析镜] Wasm 已跑完但未生成排行榜数据，请查日志中的 NDJSON 解析 / persist / LLM",
            "WARNING",
        )

    failed_items: list[dict[str, Any]] = []
    if thr_err:
        failed_items.append(
            {
                "id": "*batch*",
                "stage": "wasm",
                "error_class": classify_wasm_error_message(str(thr_err)),
                "message": str(thr_err)[:240],
            }
        )
    elif _wasm_return_indicates_failure(wr):
        failed_items.append(
            {
                "id": "*batch*",
                "stage": "wasm",
                "error_class": classify_wasm_error_message(str(wr)),
                "message": str(wr)[:240],
            }
        )

    meta: dict[str, Any] = {}
    missing = [p for p in pdf_paths if not (output_dir / f"{p.stem}_analysis.md").is_file()]
    if force_batch and missing:
        reason = (
            "wasm_thread_error"
            if thr_err
            else ("wasm_return_error" if _wasm_return_indicates_failure(wr) else "incomplete_after_batch")
        )
        logger.warning(
            "[StrategyShift] domain=hr step=hr_analyze from=batch_wasm to=sequential reason=%s missing=%d",
            reason,
            len(missing),
        )
        broadcast_log(
            f"[透析镜] [StrategyShift] 批量后仍有 {len(missing)} 份缺 *_analysis.md，自动逐份补齐（{reason}）",
            "WARNING",
        )
        pl2, el2, fi2 = _run_wasm_analysis_sequential(base_input, missing, output_dir, job_folder)
        passed_list.extend(pl2)
        eliminated_list.extend(el2)
        failed_items.extend(fi2)
        meta["sequential_fallback_for_batch"] = True
        meta["sequential_fallback_reason"] = reason

    return passed_list, eliminated_list, failed_items, meta


def _write_summary_md(job_dir: Path, passed_list: list, eliminated_list: list, job_folder: str) -> None:
    """生成排行榜 Summary 到 data/{职位}/排行榜_Summary.md"""
    _ensure_hr_plugin_on_sys_path()
    from recruitment_task import _write_summary_md as _write
    _write(job_dir, passed_list, eliminated_list, use_absolute_path=False, job_folder=job_folder)


def job_check_and_analyze(job_config: dict[str, Any]) -> dict[str, Any]:
    """
    动态规则引擎：每 1 分钟检查一次，满足条件时触发 Wasm 透析。

    触发条件（pending 中仍有「缺分析报告」的 PDF 时）：
    1. 未处理计数 >= analyze_threshold；
    2. 或 Lark/手动登记 pending_manual_analyze；
    3. 或上轮未跑完登记 hr_analyze_continue（避免「只分析了部分、计数低于阈值」后永久卡住）。

    同一次互斥锁内：按「缺报告的 PDF」多轮调用 Wasm 直至无缺口、无进展、或达 HR_ANALYZER_ROUNDS_PER_LOCK。
    飞书「分析完成」仅在 unprocessed 计数归零时发送；续跑过程见日志前缀 [透析轮次] / [透析续跑]。
    """
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过规则引擎检查")
        return {"fired": False, "skipped": "recruitment_stopped"}
    job_name = job_config.get("job_name", "")
    job_folder = _job_folder_from_job_config(job_config)
    _lk = _job_check_lock_for_folder(job_folder)
    if not _lk.acquire(blocking=False):
        logger.info(
            "[Scheduler] job_check_and_analyze 跳过：岗位 %s 已有实例在执行（互斥）",
            job_folder,
        )
        return {"fired": False, "skipped": "concurrent_job_check", "job_folder": job_folder}
    try:
        return _job_check_and_analyze_impl(job_config, job_name, job_folder)
    finally:
        _lk.release()


def _job_check_and_analyze_impl(
    job_config: dict[str, Any],
    job_name: str,
    job_folder: str,
) -> dict[str, Any]:
    output_dir = PLUGIN_DATA_ROOT / job_folder / "result"
    output_dir.mkdir(parents=True, exist_ok=True)

    analyze_threshold = int(job_config.get("analyze_threshold", 4))
    collect_cap = int(job_config.get("resume_collect_target") or analyze_threshold)

    unprocessed = _count_unprocessed_pdfs(job_folder, output_dir)

    if not job_config.get("auto_analyze", True):
        if unprocessed >= collect_cap and job_name:
            remove_scheduled_job(job_name)
            broadcast_log(
                f"[规则引擎] 职位={job_name} 已达收集目标 {unprocessed}/{collect_cap} 份，"
                f"未启用自动透析镜，无人值守已停止。",
                "SUCCESS",
            )
            return {
                "fired": True,
                "stopped": True,
                "reason": "collect_target_no_auto_analyze",
                "unprocessed": unprocessed,
            }
        return {
            "fired": False,
            "unprocessed": unprocessed,
            "collect_target": collect_cap,
            "auto_analyze": False,
        }

    state = _load_task_state()
    job_key = job_folder or "default"
    if job_key not in state:
        state[job_key] = {}
    job_state = state[job_key]

    manual_pending = bool(job_state.get("pending_manual_analyze"))
    hr_continue = bool(job_state.get("hr_analyze_continue"))
    if unprocessed == 0:
        _st_dirty = False
        if manual_pending:
            job_state["pending_manual_analyze"] = False
            manual_pending = False
            _st_dirty = True
            logger.info(
                "[Scheduler] 已清除手动透析请求（当前无待处理 PDF） job=%s folder=%s",
                job_name,
                job_folder,
            )
        if job_state.get("hr_analyze_continue"):
            job_state["hr_analyze_continue"] = False
            hr_continue = False
            _st_dirty = True
        if _st_dirty:
            _save_task_state(state)

    should_fire = (
        unprocessed > 0
        and (
            unprocessed >= analyze_threshold
            or manual_pending
            or hr_continue
        )
    )

    if should_fire:
        _why = []
        if unprocessed >= analyze_threshold:
            _why.append(f"未处理≥阈值({analyze_threshold})")
        if manual_pending:
            _why.append("手动透析登记")
        if hr_continue:
            _why.append("上轮透析未跑完续跑(hr_analyze_continue)")
        logger.info(
            "[Scheduler] job_check_and_analyze 将触发: unprocessed=%s analyze_threshold=%s manual_pending=%s "
            "hr_continue=%s 原因=%s",
            unprocessed,
            analyze_threshold,
            manual_pending,
            hr_continue,
            "+".join(_why) if _why else "未知",
        )

    if not should_fire:
        return {
            "fired": False,
            "unprocessed": unprocessed,
            "threshold": analyze_threshold,
            "manual_pending": manual_pending,
            "hr_analyze_continue": hr_continue,
        }

    triggered_by_manual = manual_pending
    now = datetime.now(timezone(timedelta(hours=8)))
    job_dir = PLUGIN_DATA_ROOT / job_folder
    _pend = PLUGIN_DATA_ROOT / job_folder / "pending"

    pdf_missing_initial = _pdfs_missing_analysis_reports(job_folder, output_dir)
    if not pdf_missing_initial:
        _reason = "无待生成报告的 PDF（均已存在 *_analysis.md，或 pending/副本中未发现 PDF）"
        logger.warning(
            "[Scheduler] job_check_and_analyze 跳过: %s job=%s folder=%s unprocessed计数=%s",
            _reason,
            job_name,
            job_folder,
            unprocessed,
        )
        print(
            f"\n[Scheduler] ⚠️ {_reason}\n  job_name={job_name} job_folder={job_folder}\n",
            flush=True,
        )
        st = _load_task_state()
        if job_key not in st:
            st[job_key] = {}
        st[job_key]["hr_analyze_continue"] = False
        _save_task_state(st)
        return {"fired": False, "unprocessed": unprocessed, "reason": "无待分析PDF"}

    state = _load_task_state()
    if job_key not in state:
        state[job_key] = {}
    job_state = state[job_key]
    if triggered_by_manual:
        job_state["pending_manual_analyze"] = False
        _save_task_state(state)
        broadcast_log(
            f"[规则引擎] 职位={job_name} folder={job_folder} Lark/手动指令触发透析镜",
            "WARNING",
        )
    elif hr_continue:
        broadcast_log(
            f"[规则引擎] 职位={job_name} folder={job_folder} 透析续跑（hr_analyze_continue），补全未生成报告的 PDF",
            "WARNING",
        )
    else:
        broadcast_log(
            f"[规则引擎] 职位={job_name} folder={job_folder} 简历池达阈值（≥{analyze_threshold}），正在唤醒透析镜",
            "WARNING",
        )

    logger.info(
        "[Scheduler] job_check_and_analyze 触发 职位=%s folder=%s 缺报告PDF=%d (unprocessed计数=%d)",
        job_name,
        job_folder,
        len(pdf_missing_initial),
        unprocessed,
    )

    if os.environ.get("HR_RECRUITMENT_LARK_ANALYSIS_START", "").strip().lower() in ("1", "true", "yes"):
        _lark_start = (
            f"待分析 PDF（缺报告）：{len(pdf_missing_initial)} 份\n"
            f"pending：{_pend}\n"
            f"分析报告将写入：{output_dir}\n"
            f"（默认不再发「开始」；设 HR_RECRUITMENT_LARK_ANALYSIS_START=1 可恢复）"
        )
        _lark_notify_hr_analysis("开始分析", job_name, _lark_start)

    max_rounds = int(os.environ.get("HR_ANALYZER_ROUNDS_PER_LOCK", "64") or "64")
    all_passed: list[dict] = []
    all_eliminated: list[dict] = []
    all_failed: list[dict[str, Any]] = []
    batch_fallback_any = False
    round_idx = 0

    try:
        while round_idx < max_rounds:
            pdf_paths = _pdfs_missing_analysis_reports(job_folder, output_dir)
            if not pdf_paths:
                logger.info(
                    "[Scheduler] [透析轮次] 多轮循环结束：已无缺报告 PDF（上一共执行 %d 轮）job=%s",
                    round_idx,
                    job_name,
                )
                break
            round_idx += 1
            n_miss = len(pdf_paths)
            ucnt = _count_unprocessed_pdfs(job_folder, output_dir)
            if round_idx == 1:
                logger.info(
                    "[Scheduler] [透析轮次] 第 1 轮 — 本会话开始，本批缺分析报告 %d 份，unprocessed计数=%d job=%s",
                    n_miss,
                    ucnt,
                    job_name,
                )
            else:
                logger.warning(
                    "[Scheduler] [透析轮次] 第 %d 轮 — 「一次未跑完」二次续跑（同一互斥锁内继续 Wasm），"
                    "本批仍剩 %d 份缺分析报告，unprocessed计数=%d job=%s",
                    round_idx,
                    n_miss,
                    ucnt,
                    job_name,
                )
                broadcast_log(
                    f"[透析镜] ⚠️ 续跑第 {round_idx} 轮：仍有 {n_miss} 份 PDF 无分析报告，继续 Wasm",
                    "WARNING",
                )

            pl, el, fi, wasm_meta = _run_wasm_analysis_sync(job_config, pdf_paths, output_dir, job_folder)
            all_passed.extend(pl)
            all_eliminated.extend(el)
            all_failed.extend(fi)
            if wasm_meta.get("sequential_fallback_for_batch"):
                batch_fallback_any = True

            after_miss = len(_pdfs_missing_analysis_reports(job_folder, output_dir))
            after_u = _count_unprocessed_pdfs(job_folder, output_dir)
            logger.info(
                "[Scheduler] [透析轮次] 第 %d 轮结束 缺报告 %d→%d，unprocessed %d→%d job=%s",
                round_idx,
                n_miss,
                after_miss,
                ucnt,
                after_u,
                job_name,
            )

            if after_miss == 0:
                st = _load_task_state()
                st.setdefault(job_key, {})["hr_analyze_continue"] = False
                _save_task_state(st)
                logger.info(
                    "[Scheduler] [透析续跑] 已全部补齐分析报告（最后一轮=%d）job=%s",
                    round_idx,
                    job_name,
                )
                break

            if after_miss >= n_miss:
                st = _load_task_state()
                st.setdefault(job_key, {})["hr_analyze_continue"] = True
                _save_task_state(st)
                logger.error(
                    "[Scheduler] [透析续跑] 第 %d 轮未减少缺报告份数（%d→%d），停止本锁内循环；"
                    "已设置 hr_analyze_continue，下周期 job_check 将重试 job=%s",
                    round_idx,
                    n_miss,
                    after_miss,
                    job_name,
                )
                log_execution_brief(
                    domain="hr",
                    goal="dialysis_missing_reports",
                    outcome="stalled_no_progress",
                    message=(
                        f"round={round_idx} missing {n_miss}->{after_miss} folder={job_folder}; "
                        "hr_analyze_continue set for next tick"
                    ),
                )
                break

        if round_idx >= max_rounds and _pdfs_missing_analysis_reports(job_folder, output_dir):
            st = _load_task_state()
            st.setdefault(job_key, {})["hr_analyze_continue"] = True
            _save_task_state(st)
            logger.warning(
                "[Scheduler] [透析续跑] 已达单锁最大轮数 %d，仍有缺报告；已登记续跑 job=%s",
                max_rounds,
                job_name,
            )

        unprocessed_after = _count_unprocessed_pdfs(job_folder, output_dir)
        still_missing_n = len(_pdfs_missing_analysis_reports(job_folder, output_dir))
        ok_count = max(0, len(pdf_missing_initial) - still_missing_n)

        def _flush_hr_run_report(
            status: str, extra_failed: list[dict[str, Any]] | None = None
        ) -> None:
            items = list(all_failed)
            if extra_failed:
                items.extend(extra_failed)
            rep = build_run_report(
                status=status,
                ok_count=ok_count,
                failed_items=items[:300],
                degraded=batch_fallback_any or round_idx > 1,
                fallback_used="sequential_after_batch" if batch_fallback_any else None,
                extra={
                    "domain": "hr_recruitment",
                    "job_folder": job_folder,
                    "analyze_rounds": round_idx,
                    "unprocessed_after": unprocessed_after,
                    "still_missing_reports": still_missing_n,
                },
            )
            write_run_report_json(output_dir, rep)

        if not all_passed and not all_eliminated:
            _flush_hr_run_report(
                "failed",
                extra_failed=[
                    {
                        "id": "*",
                        "stage": "wasm",
                        "error_class": ERROR_PERMANENT,
                        "message": "no_resume_analysis_output",
                    }
                ],
            )
            log_execution_brief(
                domain="hr",
                goal="dialysis_leaderboard",
                outcome="no_output",
                message=(
                    f"folder={job_folder} rounds={round_idx} initial_missing={len(pdf_missing_initial)} "
                    "no passed/eliminated entries"
                ),
            )
            _human = (
                "透析镜未分析到任何简历（无录用/淘汰结果）。"
                "已跳过排行榜更新与 Lark 同步；请检查 pending/processed/副本 中 PDF 是否有效。"
            )
            broadcast_log(f"[规则引擎] ⚠️ {_human}", "WARNING")
            logger.warning(
                "[Scheduler] job_check_and_analyze 无分析产出: %s folder=%s 缺报告初始=%d 执行轮次=%d",
                _human,
                job_folder,
                len(pdf_missing_initial),
                round_idx,
            )
            print(
                f"\n[Scheduler] ⚠️ {_human}\n"
                f"  job_name={job_name} job_folder={job_folder}\n",
                flush=True,
            )
            _lark_notify_hr_analysis(
                "分析无产出",
                job_name,
                f"{_human}\n缺报告初始={len(pdf_missing_initial)} 轮次={round_idx}，请查 NDJSON/Wasm/LLM。",
            )
            return {
                "fired": True,
                "skipped_followup": True,
                "reason": "no_resume_analysis_output",
                "unprocessed": unprocessed,
                "passed": 0,
                "eliminated": 0,
                "analyze_rounds": round_idx,
            }

        rr_status = "success"
        if still_missing_n > 0 or all_failed:
            rr_status = "partial_success"
        _flush_hr_run_report(rr_status)

        _write_summary_md(job_dir, all_passed, all_eliminated, job_folder)
        broadcast_log("[规则引擎] 🏆 琅琊榜战报生成完毕！", "SUCCESS")
        summary_path = job_dir / "排行榜_Summary.md"
        if summary_path.exists():
            try:
                from l3_node.channels.lark import sync_bitable_from_md
                replace_from_first = os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "true").lower() in ("1", "true", "yes")
                sync_result = sync_bitable_from_md(md_path=str(summary_path), notify_group=True, replace_entire_table=replace_from_first)
                if sync_result.get("success"):
                    logger.info("[Scheduler] Lark 多维表已同步 job=%s count=%d", job_folder, sync_result.get("count", 0))
                elif sync_result.get("skipped"):
                    logger.info("[Scheduler] Lark 多维表同步已跳过（未配置应用凭证）")
                else:
                    logger.warning("[Scheduler] Lark 同步失败: %s", sync_result.get("error", ""))
            except Exception as e:
                logger.warning("[Scheduler] Lark 同步异常: %s", e)

        st = _load_task_state()
        if job_key not in st:
            st[job_key] = {}
        st[job_key]["last_analyze_time"] = now.isoformat()
        if unprocessed_after == 0:
            st[job_key]["hr_analyze_continue"] = False
        else:
            st[job_key]["hr_analyze_continue"] = True
        _save_task_state(st)

        job_name = job_config.get("job_name", "")
        collect_cap2 = int(job_config.get("resume_collect_target") or analyze_threshold)
        if job_name and unprocessed_after >= collect_cap2:
            remove_scheduled_job(job_name)
            broadcast_log(
                f"[规则引擎] 分析完成；未处理简历 {unprocessed_after} 份已达收集上限 {collect_cap2}，无人值守流程结束。",
                "WARNING",
            )
        elif job_name and unprocessed_after < collect_cap2:
            if unprocessed_after == 0:
                broadcast_log(
                    f"[规则引擎] 透析已全部完成（缺报告 0 份），收网与检查将继续 job={job_name}",
                    "SUCCESS",
                )
            else:
                broadcast_log(
                    f"[规则引擎] 透析本会话结束，当前未处理 {unprocessed_after}/{collect_cap2} 份（已登记续跑 hr_analyze_continue）",
                    "INFO",
                )

        if unprocessed_after == 0:
            jn_done = (job_name or "").strip() or "本岗位"
            _lark_notify_hr_analysis(
                "分析完成",
                job_name,
                f"（全部简历已生成分析报告）\n"
                f"透析轮次：{round_idx}\n"
                f"录用倾向：{len(all_passed)} 人，待复核/淘汰：{len(all_eliminated)} 人\n"
                f"报告目录：{output_dir}\n"
                f"琅琊榜：{job_dir / '排行榜_Summary.md'}",
                human_line=(
                    f"【{jn_done}】本批简历读完了：约 **{len(all_passed)}** 位建议重点看，"
                    f"**{len(all_eliminated)}** 位建议再斟酌。汇总若接了飞书表会自动更新。"
                ),
            )
        else:
            logger.warning(
                "[Scheduler] [透析续跑] 本次未全部完成：仍有未处理约 %d 份；"
                "「分析完成」Lark 仅在全部补齐后发送；下周期将自动续跑 job=%s",
                unprocessed_after,
                job_name,
            )

        return {
            "fired": True,
            "unprocessed": unprocessed,
            "unprocessed_after": unprocessed_after,
            "passed": len(all_passed),
            "eliminated": len(all_eliminated),
            "last_analyze_time": now.isoformat(),
            "analyze_rounds": round_idx,
            "hr_analyze_continue": unprocessed_after > 0,
        }
    except Exception as e:
        logger.warning("[Scheduler] job_check_and_analyze Wasm 分析失败: %s", e)
        try:
            st = _load_task_state()
            st.setdefault(job_key, {})["hr_analyze_continue"] = True
            _save_task_state(st)
        except Exception:
            pass
        _lark_notify_hr_analysis("分析失败", job_name, f"{e}\n请查看 L3 日志与 pending PDF 是否可读。")
        return {"fired": True, "error": str(e), "unprocessed": unprocessed}


# ---------------------------------------------------------------------------
# 调度器 API：增删任务
# ---------------------------------------------------------------------------
def add_scheduled_job(job_config: dict[str, Any]) -> dict[str, Any]:
    """向 scheduler 添加招聘定时任务。

    单任务交替：牛人沟通 → 抓简历 → …，周期为 greet_harvest_switch_interval_minutes（默认 10）；达招呼上限或沟通列表空时可提前切换。
    若 ``greet_only_total_target > 0``：只注册 ``rec_*_greet_only``，累计打招呼达标后停表并发飞书（无收网、无 job_check）。
    否则规则引擎 job_check 每分钟检查；达 analyze_threshold 等触发透析。
    """
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    job_name = (job_config.get("job_name") or "").strip()
    if not job_name:
        return {"ok": False, "error": "job_name 不能为空"}

    job_folder = _job_folder_from_job_config(job_config)
    job_memory_at_start = build_recruitment_job_memory(job_name, job_folder=job_folder)
    job_id_harvest = f"rec_{job_folder}_harvest"
    job_id_alternate = f"rec_{job_folder}_alternate"
    job_id_check = f"rec_{job_folder}_check"
    job_id_greet_only = f"rec_{job_folder}_greet_only"

    # 注入/覆盖关键参数
    job_config = dict(job_config)
    job_config["job_folder"] = job_folder
    _merge_scheduling_flags_from_jd_if_missing(job_config, job_folder)
    # 收网目标与透析阈值统一为同一「份数」，避免 jd 改了目标但进度仍显示旧 cap
    _rct = int(job_config.get("resume_collect_target") or job_config.get("analyze_threshold") or 4)
    _rct = max(1, min(9999, _rct))
    job_config["resume_collect_target"] = _rct
    job_config["analyze_threshold"] = _rct
    at = _rct
    job_config.setdefault("greet_target", 3)
    job_config.setdefault("max_count", 50)
    job_config.setdefault("recommend_interval_minutes", 15)
    job_config.setdefault("harvest_interval_minutes", 1)
    job_config.setdefault("enable_greet_recommend", True)
    job_config.setdefault("parallel_greet_and_harvest", False)
    job_config.setdefault("auto_analyze", True)
    job_config["greet_harvest_switch_interval_minutes"] = _effective_greet_harvest_switch_minutes(job_config)
    if job_config.get("parallel_greet_and_harvest"):
        logger.warning(
            "[Scheduler] parallel_greet_and_harvest=true 已忽略：Boss 单页仅支持牛人沟通↔抓简历单轨交替"
        )
    job_config["parallel_greet_and_harvest"] = False

    greet_only_total = int(job_config.get("greet_only_total_target") or 0)
    if greet_only_total <= 0:
        job_config.pop("greet_only_total_target", None)
        job_config.pop("greet_only_interval_minutes", None)

    # 换岗抢占：在卸定时前先标记「仍在跑的其它目录键」为挂起，便于 list / 按岗恢复
    _apply_preempt_suspend_marks_before_switch(job_folder)
    # 移除全部已有招聘任务，确保换岗后不会与旧目录并行
    remove_all_recruitment_apscheduler_jobs()
    # 新启无人值守时清除上一轮「手动透析」登记，避免 pending_manual_analyze + 少量旧 PDF 误触透析/琅琊榜
    try:
        _st = _load_task_state()
        if job_folder not in _st:
            _st[job_folder] = {}
        _st[job_folder]["pending_manual_analyze"] = False
        _st[job_folder]["hr_analyze_continue"] = False
        _save_task_state(_st)
    except Exception as _e:
        logger.debug("[Scheduler] 清除手动透析登记失败: %s", _e)
    # 新启动岗位时清除停止标志，允许该岗位的定时任务执行（用户停止后再次发布时需此逻辑）
    set_recruitment_stopped(False)
    _sync_hr_workflow_pointer_for_lark(job_config)

    try:
        _now = datetime.now()
        switch_m = max(1, int(job_config.get("greet_harvest_switch_interval_minutes", 10) or 10))

        if greet_only_total > 0:
            rim = int(job_config.get("greet_only_interval_minutes") or 0)
            if rim <= 0:
                rim = max(
                    1,
                    int(job_config.get("recommend_interval_minutes") or switch_m),
                )
            rim = max(1, min(120, rim))
            job_config["greet_only_interval_minutes"] = rim
            job_config["enable_greet_recommend"] = True
            _resume_greet_only = bool(job_config.pop("_greet_only_resume", False))
            try:
                _st_go = _load_task_state()
                _bucket = _st_go.setdefault(job_folder, {})
                if not _resume_greet_only:
                    _bucket["hr_greet_only_done"] = 0
                    _bucket["hr_greet_only_zero_streak"] = 0
                _save_task_state(_st_go)
            except Exception:
                pass
            scheduler.add_job(
                job_greet_only_campaign,
                "interval",
                minutes=rim,
                id=job_id_greet_only,
                next_run_time=_now + timedelta(seconds=30),
                args=[job_config],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=180,
            )
            mode_bits = [f"仅打招呼累计{greet_only_total}次，每{rim}min一步"]
            logger.info(
                "[Scheduler] add_scheduled_job greet_only_total=%s interval=%smin",
                greet_only_total,
                rim,
            )
            _persist_last_job_config(job_folder, job_config)
            _jd_write_greet_only_campaign(
                job_folder,
                str(job_config.get("jd_config_path") or ""),
                greet_only_total,
                rim,
            )
            try:
                from l3_node.local_memory import get_hr_recruitment_workflow_pointer, set_hr_recruitment_workflow_pointer

                ptr_once = get_hr_recruitment_workflow_pointer()
                set_hr_recruitment_workflow_pointer(
                    (ptr_once.get("workflow_id") or "").strip() or _hr_dag_workflow_id(job_folder),
                    job_name=job_name,
                    job_folder=job_folder,
                    jd_config_path=str(job_config.get("jd_config_path") or "").strip()
                    or (ptr_once.get("jd_config_path") or "").strip(),
                    resume_pending_dir=str(PLUGIN_DATA_ROOT / job_folder / "pending"),
                    lark_chat_id=None,
                    scheduler_pending_confirm=False,
                )
            except Exception as _e:
                logger.debug("[Scheduler] greet_only 写 workflow 指针跳过: %s", _e)
            _hr_audit(
                "scheduler_started",
                job_folder=job_folder,
                job_name=job_name,
                detail={"modes": mode_bits, "greet_only_total_target": greet_only_total},
            )
            _clear_scheduler_suspended_mark(job_folder)
            return {
                "ok": True,
                "job_name": job_name,
                "job_folder": job_folder,
                "greet_only_total_target": greet_only_total,
                "greet_only_interval_minutes": rim,
                "enable_greet_recommend": True,
                "max_count_per_harvest_tick": int(job_config.get("max_count", 50)),
                "greet_target": int(job_config.get("greet_target", 3)),
                "greet_harvest_switch_interval_minutes": int(rim),
                "job_memory_at_start": job_memory_at_start,
                "job_memory_brief_zh": (job_memory_at_start.get("hr_brief_zh") or "").strip(),
            }

        enable_greet = bool(job_config.get("enable_greet_recommend", True))
        try:
            _st_alt = _load_task_state()
            _st_alt.setdefault(job_folder, {})["hr_alternate_next_greet"] = True
            _save_task_state(_st_alt)
        except Exception:
            pass
        logger.info(
            "[Scheduler] add_scheduled_job enable_greet_recommend=%r（单轨交替，无并行双 Job）",
            job_config.get("enable_greet_recommend"),
        )

        if enable_greet:
            scheduler.add_job(
                job_alternate_greet_harvest,
                "interval",
                minutes=switch_m,
                id=job_id_alternate,
                next_run_time=_now + timedelta(seconds=30),
                args=[job_config],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=180,
            )
        else:
            scheduler.add_job(
                job_harvest_resumes,
                "interval",
                minutes=switch_m,
                id=job_id_harvest,
                next_run_time=_now + timedelta(seconds=30),
                args=[job_config],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
            )
        # 3. 规则引擎：达阈值透析 / 或仅达收集目标停表（见 job_check_and_analyze）
        scheduler.add_job(
            job_check_and_analyze,
            "interval",
            minutes=1,
            id=job_id_check,
            next_run_time=_now + timedelta(seconds=45),
            args=[job_config],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            # 透析 5 份 PDF 常需数分钟；过短会导致「实例已满」刷屏，属正常（本轮仍在跑）
            misfire_grace_time=3600,
        )
        mode_bits = []
        if enable_greet:
            mode_bits.append(f"牛人沟通↔抓简历交替每{switch_m}min（可提前切换）")
        else:
            mode_bits.append(f"仅收网每{switch_m}min")
        logger.info(
            "[Scheduler] 已添加岗位 %s: %s；收集上限=%s 份；分析阈值=%s；auto_analyze=%s；enable_greet_recommend=%s",
            job_folder,
            "，".join(mode_bits),
            job_config.get("resume_collect_target", at),
            at,
            job_config.get("auto_analyze", True),
            job_config.get("enable_greet_recommend", True),
        )
        _persist_last_job_config(job_folder, job_config)
        _maybe_reschedule_dynamic_intervals(job_folder, job_config)
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer, set_hr_recruitment_workflow_pointer

            ptr_once = get_hr_recruitment_workflow_pointer()
            set_hr_recruitment_workflow_pointer(
                (ptr_once.get("workflow_id") or "").strip() or _hr_dag_workflow_id(job_folder),
                job_name=job_name,
                job_folder=job_folder,
                jd_config_path=str(job_config.get("jd_config_path") or "").strip()
                or (ptr_once.get("jd_config_path") or "").strip(),
                resume_pending_dir=str(PLUGIN_DATA_ROOT / job_folder / "pending"),
                lark_chat_id=None,
                scheduler_pending_confirm=False,
            )
        except Exception as _e:
            logger.debug("[Scheduler] 清除 scheduler_pending_confirm 跳过: %s", _e)
        _hr_audit(
            "scheduler_started",
            job_folder=job_folder,
            job_name=job_name,
            detail={"modes": mode_bits, "resume_collect_target": job_config.get("resume_collect_target", at)},
        )
        _jd_clear_greet_only_campaign_keys(job_folder, str(job_config.get("jd_config_path") or ""))
        _clear_scheduler_suspended_mark(job_folder)
        return {
            "ok": True,
            "job_name": job_name,
            "job_folder": job_folder,
            "enable_greet_recommend": bool(job_config.get("enable_greet_recommend", True)),
            "resume_collect_target": int(job_config.get("resume_collect_target", at)),
            "analyze_threshold": int(job_config.get("analyze_threshold", at)),
            "max_count_per_harvest_tick": int(job_config.get("max_count", 50)),
            "greet_target": int(job_config.get("greet_target", 3)),
            "greet_harvest_switch_interval_minutes": int(switch_m),
            "job_memory_at_start": job_memory_at_start,
            "job_memory_brief_zh": (job_memory_at_start.get("hr_brief_zh") or "").strip(),
        }
    except Exception as e:
        logger.warning("[Scheduler] add_scheduled_job 失败: %s", e)
        return {
            "ok": False,
            "error": str(e),
            "job_memory_at_start": job_memory_at_start,
            "job_memory_brief_zh": (job_memory_at_start.get("hr_brief_zh") or "").strip(),
        }


def remove_harvest_scheduler_jobs(job_name: str = "", *, job_folder_hint: str = "") -> dict[str, Any]:
    """
    飞书「停止收网」配套：只移除推荐牛人 + 收网抓取，保留 job_check_and_analyze（透析/规则仍可按分钟轮询）。
    避免 STOP 注入后当前 Playwright 仍在跑时，下一分钟又启动第二个 harvest 实例（max_instances=1 时刷屏 skipped）。

    **目录键**必须与 ``add_scheduled_job`` 一致（多为 ``职位_城市薪资``，如 ``产品经理_杭州25-40K``），
    不能仅用 ``_sanitize_job_folder(岗位显示名)``，否则会得到 ``产品经理``，与真实任务 id
    ``rec_产品经理_杭州25-40K_alternate`` 对不上，remove 全失败、停止无效。
    """
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    jn = (job_name or "").strip()
    jf_hint = _sanitize_job_folder((job_folder_hint or "").strip())
    # 指针里的 primary_job_folder 与 add_scheduled_job 注册的 rec_{folder}_* 一致，优先于仅按岗位名推断
    job_folder = jf_hint or (
        _resolve_hr_data_job_folder(jn) if jn else _resolve_hr_data_job_folder("")
    )
    if not job_folder and jn:
        job_folder = _sanitize_job_folder(jn)
    if not job_folder:
        return {"ok": False, "error": "job_name 与指针均无法解析岗位数据目录键"}

    ids = [
        f"rec_{job_folder}_recommend",
        f"rec_{job_folder}_harvest",
        f"rec_{job_folder}_alternate",
        f"rec_{job_folder}_greet_only",
    ]
    removed: list[str] = []
    for jid in ids:
        try:
            scheduler.remove_job(jid)
            removed.append(jid)
        except Exception:
            pass
    logger.info("[Scheduler] 已移除收网/推荐定时任务（保留 check）: %s removed=%s", job_folder, removed)
    if not removed:
        logger.warning(
            "[Scheduler] remove_harvest_scheduler_jobs 未卸掉任何 APScheduler 任务 folder=%s（可能 id 仍不匹配或已先卸过）",
            job_folder,
        )
    disp = (jn or job_folder).strip() or job_folder
    _hr_audit(
        "scheduler_harvest_stopped",
        job_folder=job_folder,
        job_name=disp,
        detail={"removed": removed},
    )
    return {"ok": True, "job_name": disp, "job_folder": job_folder, "removed": removed}


def remove_all_recruitment_apscheduler_jobs() -> list[str]:
    """移除所有 ``rec_*`` 招聘定时任务。换岗 / 新注册前调用，避免旧目录任务与新区间并行跑。"""
    if not _APSCHEDULER_AVAILABLE or scheduler is None:
        return []
    removed: list[str] = []
    try:
        for j in list(scheduler.get_jobs()):
            jid = getattr(j, "id", "") or ""
            if not jid.startswith("rec_"):
                continue
            try:
                scheduler.remove_job(jid)
                removed.append(jid)
            except Exception:
                pass
    except Exception as e:
        logger.warning("[Scheduler] remove_all_recruitment_apscheduler_jobs: %s", e)
    if removed:
        logger.info("[Scheduler] 已移除全部招聘定时任务 %d 个", len(removed))
    return removed


def remove_scheduled_job(job_name: str) -> dict[str, Any]:
    """暂停并移除岗位相关的浏览器定时任务（推荐/收网/交替）与规则引擎 check。"""
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    jn = (job_name or "").strip()
    job_folder = _resolve_hr_data_job_folder(jn) if jn else ""
    if not job_folder:
        return {"ok": False, "error": "job_name 不能为空或无法解析岗位数据目录（请绑定岗或提供 jd_select/城市/薪资）"}

    ids = [
        f"rec_{job_folder}_recommend",
        f"rec_{job_folder}_harvest",
        f"rec_{job_folder}_alternate",
        f"rec_{job_folder}_greet_only",
        f"rec_{job_folder}_check",
    ]
    removed = []
    for jid in ids:
        try:
            scheduler.remove_job(jid)
            removed.append(jid)
        except Exception:
            pass
    logger.info("[Scheduler] 已移除岗位任务: %s removed=%s", job_folder, removed)
    _hr_audit(
        "scheduler_all_stopped",
        job_folder=job_folder,
        job_name=(job_name or "").strip() or job_folder,
        detail={"removed": removed},
    )
    return {"ok": True, "job_name": job_name, "removed": removed}


def clear_scheduler_state_bucket_for_folder(job_folder: str) -> None:
    """从 ``scheduler_state.json`` 删除某岗的持久化块与 ``_last_job_configs`` 条目（不删磁盘简历）。"""
    jf = _sanitize_job_folder((job_folder or "").strip())
    if not jf:
        return
    state = _load_task_state()
    changed = False
    if jf in state:
        try:
            del state[jf]
            changed = True
        except Exception:
            pass
    lst = state.get("_last_job_configs")
    if isinstance(lst, dict) and jf in lst:
        try:
            del lst[jf]
            state["_last_job_configs"] = lst
            changed = True
        except Exception:
            pass
    if changed:
        _save_task_state(state)
        logger.info("[Scheduler] 已清除 scheduler_state 中岗位块 job_folder=%s", jf)


def clear_recruitment_scheduler_memory_for_job(job_name: str) -> dict[str, Any]:
    """
    移除该岗位全部 APScheduler 任务，并清除 ``scheduler_state`` 中对应 bucket / 上次 job_config。
    **不删除** ``~/.jachin/workspace/hr_recruitment/{岗}/`` 下 pending、jd.json 等文件。
    """
    jn = (job_name or "").strip()
    if not jn:
        return {"ok": False, "error": "job_name 为空"}
    jf = _resolve_hr_data_job_folder(jn)
    removed_ids: list[str] = []
    if _APSCHEDULER_AVAILABLE:
        r1 = remove_scheduled_job(jn)
        removed_ids = list(r1.get("removed") or [])
    clear_scheduler_state_bucket_for_folder(jf)
    _hr_audit(
        "scheduler_memory_cleared_job",
        job_folder=jf,
        job_name=jn,
        detail={"removed": removed_ids},
    )
    try:
        from tools.hr_data_paths import set_jd_show_in_hr_briefing_for_job_folder

        set_jd_show_in_hr_briefing_for_job_folder(jf, False)
    except Exception as _e:
        logger.debug("[Scheduler] 清除单岗后写 jd show_in_hr_briefing 跳过: %s", _e)
    return {
        "ok": True,
        "job_name": jn,
        "job_folder": jf,
        "removed_scheduler_ids": removed_ids,
    }


def clear_all_recruitment_scheduler_memory() -> dict[str, Any]:
    """移除所有 ``rec_*`` 定时任务，并清空各岗 state 块；保留 ``scheduler_state`` 中以 ``_`` 开头的元数据键。"""
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}
    removed_ids: list[str] = []
    try:
        for j in list(scheduler.get_jobs()):
            jid = getattr(j, "id", None) or ""
            if not jid.startswith("rec_"):
                continue
            try:
                scheduler.remove_job(jid)
                removed_ids.append(jid)
            except Exception:
                pass
    except Exception as e:
        logger.warning("[Scheduler] 清除全部定时任务: %s", e)

    state = _load_task_state()
    new_state: dict[str, Any] = {}
    for k, v in state.items():
        if not k.startswith("_"):
            continue
        if k == "_last_job_configs":
            new_state[k] = {}
        else:
            new_state[k] = v
    if "_last_job_configs" not in new_state:
        new_state["_last_job_configs"] = {}
    _save_task_state(new_state)
    set_recruitment_stopped(False)
    logger.info("[Scheduler] 已清除全部岗位调度记忆；移除 APScheduler 任务 %d 个", len(removed_ids))
    _hr_audit(
        "scheduler_memory_cleared_all",
        job_folder="",
        job_name="",
        detail={"removed_count": len(removed_ids)},
    )
    try:
        from tools.hr_data_paths import set_all_jd_show_in_hr_briefing

        _n = set_all_jd_show_in_hr_briefing(False)
        logger.info("[Scheduler] 清除全部调度记忆后已写 jd show_in_hr_briefing=false，共 %d 个文件", _n)
    except Exception as _e:
        logger.debug("[Scheduler] 清除全部后批量写 jd show_in_hr_briefing 跳过: %s", _e)
    return {"ok": True, "removed_scheduler_job_ids": removed_ids}


def list_scheduled_jobs() -> list[dict[str, Any]]:
    """返回当前后台运行的自动化招聘任务列表"""
    if not _APSCHEDULER_AVAILABLE:
        return []

    jobs = []
    seen_folders: set[str] = set()
    for j in scheduler.get_jobs():
        jid = j.id or ""
        if jid.startswith("rec_") and "_" in jid:
            parts = jid.split("_")
            if len(parts) >= 3:
                folder = "_".join(parts[1:-1])
                if folder not in seen_folders:
                    seen_folders.add(folder)
                    state = _load_task_state()
                    job_key = folder
                    last_time = state.get(job_key, {}).get("last_analyze_time", "")
                    jobs.append({
                        "job_folder": folder,
                        "job_id_recommend": f"rec_{folder}_recommend",
                        "job_id_harvest": f"rec_{folder}_harvest",
                        "job_id_alternate": f"rec_{folder}_alternate",
                        "job_id_greet_only": f"rec_{folder}_greet_only",
                        "job_id_check": f"rec_{folder}_check",
                        "last_analyze_time": last_time,
                        "next_run": str(j.next_run_time) if j.next_run_time else None,
                    })
    return jobs
