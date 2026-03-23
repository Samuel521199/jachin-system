"""
L3 招聘心脏起搏器 - 基于 APScheduler 的全自动无人值守守护进程

将单次招聘任务解耦为三个独立定时 Job：
  Job 1: 定时推荐牛人（打招呼 MCP）
  Job 2: 定时收网抓取（简历抓取 MCP）
  Job 3: 动态规则引擎（条件触发 Wasm 分析）
"""

from __future__ import annotations

import asyncio
import json
import os
import logging
import re
import sys
import threading
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


def _get_hr_recruitment_plugin_root() -> Path | None:
    """HR 招聘 MCP 包根目录。本模块在 HR 包内，包根即 recruitment_scheduler.py 所在目录。"""
    hr_root = Path(__file__).resolve().parent
    if (hr_root / "plugin.json").exists() or (hr_root / "tools" / "atom_inbox_harvester.py").exists():
        return hr_root
    return None


def _load_atom_inbox_harvester_full_flow():
    """从 com.jachin.hr.recruitment 加载 atom_inbox_harvester，完全解耦 2-track。"""
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


def _sync_hr_workflow_pointer_for_lark(job_config: dict[str, Any]) -> None:
    """供飞书「停止/分析」解析 workflow_id 与简历目录（与 inject_signal 目标一致）。"""
    try:
        from l3_node.local_memory import set_hr_recruitment_workflow_pointer

        job_name = (job_config.get("job_name") or "").strip()
        jf = (job_config.get("job_folder") or "").strip() or _sanitize_job_folder(job_name)
        if not jf:
            return
        wid = f"hr_recruitment_job_{jf}"
        jd_path = str(job_config.get("jd_config_path") or "")
        pend = str(PLUGIN_DATA_ROOT / jf / "pending")
        set_hr_recruitment_workflow_pointer(wid, job_name=jf, jd_config_path=jd_path, resume_pending_dir=pend)
    except Exception as e:
        logger.debug("[Scheduler] Lark HR workflow 指针同步失败: %s", e)


def _sanitize_job_folder(job_name: str, max_len: int = 60) -> str:
    """将岗位名转为安全文件夹名"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


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
        from l3_node.skills.hr_recruitment_dag import build_hr_recruitment_dag
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
    job_folder = str(job_config.get("job_folder") or "").strip() or _sanitize_job_folder(job_name)
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
        unprocessed_before = _count_unprocessed_pdfs(job_folder, output_dir)
        if not job_config.get("_dag_harvest_always_run") and unprocessed_before >= analyze_threshold:
            return {
                "success": True,
                "skipped_reason": "resume_full",
                "greeted_count": 0,
                "resume_count": 0,
                "downloaded": 0,
            }

        max_items = int(job_config.get("max_count", 50))
        stop_when = max(0, analyze_threshold - unprocessed_before)
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
                "use_all_positions": bool(job_config.get("use_all_positions", True)),
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
    return {
        "success": True,
        "greeted_count": gc,
        "resume_count": rc,
        "downloaded": rc,
        "dag_status": st,
    }


# ---------------------------------------------------------------------------
# Job 1: 定时推荐牛人（打招呼 MCP）
# 每 15 分钟执行，每轮成功打招呼 3 人即结束本轮，20 秒后启动抓简历
# ---------------------------------------------------------------------------
def job_recommend_candidates(job_config: dict[str, Any]) -> dict[str, Any]:
    """经 ``build_hr_recruitment_dag(...).run`` 执行打招呼 tick（非直接 atom）。满 greet_target 人即停，20 秒后启动抓简历。"""
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过推荐牛人任务")
        return {"success": False, "greeted_count": 0, "skipped": "recruitment_stopped"}
    job_name = job_config.get("job_name", "")
    job_folder = _sanitize_job_folder(job_name)
    greet_target = int(job_config.get("greet_target", 3))
    harvest_delay_seconds = int(job_config.get("harvest_delay_seconds", 20))

    jd_config_path = job_config.get("jd_config_path", "")
    _sync_hr_workflow_pointer_for_lark({**job_config, "job_folder": job_folder})
    broadcast_log(f"[推荐牛人] 职位={job_name} jd={jd_config_path or '(未配置)'}", "INFO")
    with chrome_lock:
        broadcast_log("[推荐牛人] 🟢 成功获取锁！经 DAG 状态机执行打招呼 tick...", "SUCCESS")
        try:
            result = _run_hr_recruitment_dag_tick(job_config, tick_mode="greet")
            err = (result.get("error") or "").strip()
            if err:
                broadcast_log(f"[推荐牛人] ❌ DAG 执行失败: {err}", "ERROR")
                logger.warning("[Scheduler] job_recommend_candidates DAG 失败: %s", result)
                return {"success": False, "greeted_count": 0, "error": err}
            n = int(result.get("greeted_count", 0))
            broadcast_log(f"[推荐牛人] ✅ 任务执行成功，已推荐 {n} 人。", "SUCCESS")
            logger.info("[Scheduler] [%s] 推荐牛人(DAG)完成: %s", job_name or "default", result)

            # 满 greet_target 人即停，20 秒后启动抓简历
            if n >= greet_target and _APSCHEDULER_AVAILABLE:
                try:
                    job_id_recommend = f"rec_{job_folder}_recommend"
                    job_id_harvest = f"rec_{job_folder}_harvest"
                    scheduler.remove_job(job_id_recommend)
                    broadcast_log(f"[推荐牛人] 已打招呼 {n} 人，达到目标，20 秒后启动收网抓简历。", "SUCCESS")
                    _now = datetime.now()
                    scheduler.add_job(
                        job_harvest_resumes,
                        "interval",
                        minutes=1,
                        id=job_id_harvest,
                        next_run_time=_now + timedelta(seconds=harvest_delay_seconds),
                        args=[job_config],
                        replace_existing=True,
                        max_instances=1,
                        misfire_grace_time=120,
                    )
                except Exception as ex:
                    logger.warning("[Scheduler] 切换到抓简历阶段失败: %s", ex)
            return {"success": True, "greeted_count": n, "dag_status": result.get("dag_status")}
        except Exception as e:
            broadcast_log(f"[推荐牛人] ❌ 任务失败: {str(e)}", "ERROR")
            logger.warning("[Scheduler] job_recommend_candidates 失败: %s", e)
            return {"success": False, "greeted_count": 0, "error": str(e)}
        finally:
            broadcast_log("[推荐牛人] 🔓 已释放 Chrome 浏览器控制权。", "INFO")


# ---------------------------------------------------------------------------
# Job 2: 定时收网抓取（简历抓取 MCP）
# ---------------------------------------------------------------------------
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
    job_folder = _sanitize_job_folder(job_name)
    job_config = dict(job_config)
    job_config["job_folder"] = job_folder
    _sync_hr_workflow_pointer_for_lark(job_config)
    save_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    save_dir.mkdir(parents=True, exist_ok=True)
    output_dir = PLUGIN_DATA_ROOT / job_folder / "result"
    output_dir.mkdir(parents=True, exist_ok=True)
    analyze_threshold = int(job_config.get("analyze_threshold", 4))

    broadcast_log(f"[收网抓取] 职位={job_name} folder={job_folder} job_select={job_text}", "INFO")
    with chrome_lock:
        broadcast_log(f"[收网抓取] 🟢 成功获取锁！经 DAG 状态机执行收网 tick，目标职位: {job_text}", "SUCCESS")
        try:
            # 已有未处理简历数，若已满则跳过抓取，让规则引擎直接触发分析
            unprocessed_before = _count_unprocessed_pdfs(job_folder, output_dir)
            if unprocessed_before >= analyze_threshold:
                broadcast_log(f"[收网抓取] 简历已满 {unprocessed_before} 份，跳过抓取，规则引擎将触发分析", "INFO")
                if _APSCHEDULER_AVAILABLE:
                    try:
                        job_id_harvest = f"rec_{job_folder}_harvest"
                        job_id_recommend = f"rec_{job_folder}_recommend"
                        scheduler.remove_job(job_id_harvest)
                        scheduler.remove_job(job_id_recommend)
                        broadcast_log("[收网抓取] 已停止打招呼与抓简历任务", "SUCCESS")
                    except Exception:
                        pass
                return {"success": True, "downloaded": 0, "skipped_reason": "resume_full"}
            result = _run_hr_recruitment_dag_tick(job_config, tick_mode="harvest")
            if result.get("skipped_reason") == "resume_full":
                broadcast_log("[收网抓取] 简历已满，跳过抓取（DAG 二次校验）", "INFO")
                return {"success": True, "downloaded": 0, "skipped_reason": "resume_full"}
            err = (result.get("error") or "").strip()
            if err:
                broadcast_log(f"[收网抓取] ❌ DAG 失败: {err}", "ERROR")
                return {"success": False, "downloaded": 0, "error": err}
            n = int(result.get("downloaded", 0))
            broadcast_log(f"[收网抓取] ✅ 任务执行成功，已下载 {n} 份简历。", "SUCCESS")
            logger.info("[Scheduler] [%s] 收网抓取(DAG)完成: %s", job_name or "default", result)
            return {"success": True, "downloaded": n, "dag_status": result.get("dag_status")}
        except Exception as e:
            broadcast_log(f"[收网抓取] ❌ 任务失败: {str(e)}", "ERROR")
            logger.warning("[Scheduler] job_harvest_resumes 失败: %s", e)
            return {"success": False, "downloaded": 0, "error": str(e)}
        finally:
            broadcast_log("[收网抓取] 🔓 已释放 Chrome 浏览器控制权。", "INFO")


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
) -> tuple[list[dict], list[dict]]:
    """同步执行 Wasm 分析，返回 passed_list, eliminated_list。务必使用当前职位的 jd.json 作为分析依据。"""
    import queue
    from recruitment_task import (
        _job_name_fallback_jd,
        _fetch_jd_from_db,
        _save_jd_to_db,
    )
    from l3_node.skills import run_tool
    from l3_node.skills.loader import _extract_stem_from_hr_report
    from hr_analysis_persist import persist_hr_analysis_batch_item

    job_name = job_config.get("job_name", "")
    jd_config_path = job_config.get("jd_config_path", "")

    jd_content = (job_config.get("jd_content") or "").strip()
    if not jd_content and jd_config_path:
        jd_content = _load_jd_from_config_path(jd_config_path, job_name)
    jd_final = jd_content or _job_name_fallback_jd(job_name) or _fetch_jd_from_db() or "岗位：请根据岗位名称评估候选人匹配度。"
    if jd_content:
        _save_jd_to_db(jd_content)

    logger.info("[Scheduler] 透析镜分析 职位=%s folder=%s jd来源=%s", job_name, job_folder, "jd_config_path" if jd_config_path and jd_content else "fallback")

    pending_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    paths_str = "|||".join(str(p).replace("\\", "/") for p in pdf_paths if p)
    input_data: dict[str, Any] = {
        "target_dir": str(pending_dir),
        "_hr_files": paths_str,
        "jd_template": jd_final,
        "strictness": job_config.get("strictness", "standard"),
        "output_dir": str(output_dir),
    }
    focus_keywords = job_config.get("focus_keywords", "")
    if focus_keywords:
        input_data["focus_keywords"] = focus_keywords

    ndjson_queue: queue.Queue[str] = queue.Queue()
    thread_result: dict[str, Any] = {"done": False, "error": None}

    def _run() -> None:
        try:
            inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
            run_tool(HR_SKILL_ID, inp, allowed_skills=None, ndjson_queue=ndjson_queue)
        except Exception as e:
            thread_result["error"] = str(e)
        finally:
            thread_result["done"] = True
            ndjson_queue.put(json.dumps({"status": "thread_done"}))

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    passed_list: list[dict] = []
    eliminated_list: list[dict] = []
    while not thread_result["done"] or not ndjson_queue.empty():
        try:
            line = ndjson_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            item = json.loads((line or "").strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("status") == "thread_done":
            break
        if item.get("status") != "progress":
            continue
        report = item.get("report_content")
        fn = item.get("filename", "")
        pdf_path = next((str(p) for p in pdf_paths if fn and (str(p).endswith(fn) or Path(p).name == fn)), "")
        stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
        if not stem or re.match(r"^resume_\d+$", stem):
            stem = _extract_stem_from_hr_report(report or "") or stem or "unknown"
        md_path = ""
        if report:
            # 使用 resolve() 与正斜杠，确保 ~/.jachin/workspace/hr_recruitment/{职位}/result 正确解析
            out_dir_str = str(output_dir.resolve()).replace("\\", "/")
            md_path = persist_hr_analysis_batch_item(
                HR_SKILL_ID, report, stem,
                config={"output_dir": out_dir_str, "output_dir_use_absolute": False},
            ) or ""
        pass_match = RE_SUMMARY_PASS.search(report or "") if report else None
        fields = _extract_candidate_fields(report or "") if report else {}
        if pass_match:
            try:
                score = float((pass_match.group(2) or "0").strip())
            except (ValueError, TypeError):
                score = 0.0
            passed_list.append({
                "name": (pass_match.group(1) or "").strip(),
                "score": score,
                "advantage": (pass_match.group(3) or "").strip(),
                "pdf_path": pdf_path,
                "md_path": md_path,
                "education": fields.get("education", "-"),
                "experience": fields.get("experience", "-"),
                "salary": fields.get("salary", "-"),
                "stars": _score_to_stars(score),
            })
        else:
            reject_match = RE_SUMMARY_REJECT.search(report or "") if report else None
            if reject_match:
                try:
                    score = float((reject_match.group(2) or "0").strip())
                except (ValueError, TypeError):
                    score = 0.0
                eliminated_list.append({
                    "name": (reject_match.group(1) or "").strip(),
                    "score": score,
                    "reason": (reject_match.group(3) or "").strip(),
                    "pdf_path": pdf_path,
                    "md_path": md_path,
                    "education": fields.get("education", "-"),
                    "experience": fields.get("experience", "-"),
                    "salary": fields.get("salary", "-"),
                    "stars": _score_to_stars(score),
                })
            elif report and not report.strip().startswith("⚠️"):
                # 兜底：LLM 未输出 SUMMARY 块时，从报告/文件名提取基本信息，确保排行榜有内容
                fallback_name = ""
                for m in RE_REPORT_TITLE.finditer(report):
                    fallback_name = (m.group(1) or "").strip()
                    if fallback_name and len(fallback_name) < 20:
                        break
                if not fallback_name and stem:
                    # 从文件名提取：【职位】姓名_id -> 姓名
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
                eliminated_list.append({
                    "name": fallback_name[:30],
                    "score": score,
                    "reason": "报告格式未包含标准 SUMMARY 块，需人工复核",
                    "pdf_path": pdf_path,
                    "md_path": md_path,
                    "education": fields.get("education", "-"),
                    "experience": fields.get("experience", "-"),
                    "salary": fields.get("salary", "-"),
                    "stars": _score_to_stars(score),
                })
                logger.info("[Scheduler] 透析镜兜底：从报告提取 name=%s score=%.1f（无 SUMMARY 块）", fallback_name, score)

    t.join(timeout=2.0)
    if thread_result.get("error"):
        raise RuntimeError(thread_result["error"])
    return passed_list, eliminated_list


def _write_summary_md(job_dir: Path, passed_list: list, eliminated_list: list, job_folder: str) -> None:
    """生成排行榜 Summary 到 data/{职位}/排行榜_Summary.md"""
    from recruitment_task import _write_summary_md as _write
    _write(job_dir, passed_list, eliminated_list, use_absolute_path=False, job_folder=job_folder)


def job_check_and_analyze(job_config: dict[str, Any]) -> dict[str, Any]:
    """
    动态规则引擎：每 1 分钟检查一次，满足条件时触发 Wasm 分析。

    条件：analyze_threshold（默认 4 份）或 analyze_interval_hours（每 N 小时）
    规则：(未处理数量 >= threshold) OR (距上次分析 >= interval_hours) 且 未处理 > 0
    满足后：分析 → 排行榜 → remove_scheduled_job，无人值守流程结束。
    """
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过规则引擎检查")
        return {"fired": False, "skipped": "recruitment_stopped"}
    job_name = job_config.get("job_name", "")
    job_folder = _sanitize_job_folder(job_name)
    output_dir = PLUGIN_DATA_ROOT / job_folder / "result"
    output_dir.mkdir(parents=True, exist_ok=True)

    analyze_threshold = int(job_config.get("analyze_threshold", 4))
    analyze_interval_hours = float(job_config.get("analyze_interval_hours", 0.05))

    unprocessed = _count_unprocessed_pdfs(job_folder, output_dir)
    state = _load_task_state()
    job_key = job_folder or "default"
    last_analyze_str = state.get(job_key, {}).get("last_analyze_time", "")
    last_analyze_time = None
    if last_analyze_str:
        try:
            last_analyze_time = datetime.fromisoformat(last_analyze_str.replace("Z", "+00:00"))
        except Exception:
            pass

    now = datetime.now(timezone(timedelta(hours=8)))
    hours_since = float("inf") if not last_analyze_time else (now - last_analyze_time).total_seconds() / 3600

    should_fire = (
        (unprocessed >= analyze_threshold) or (hours_since >= analyze_interval_hours)
    ) and unprocessed > 0

    if not should_fire:
        return {
            "fired": False,
            "unprocessed": unprocessed,
            "threshold": analyze_threshold,
            "hours_since": round(hours_since, 1) if hours_since != float("inf") else None,
            "interval_hours": analyze_interval_hours,
        }

    broadcast_log(f"[规则引擎] 职位={job_name} folder={job_folder} 简历池达阈值，正在唤醒透析镜", "WARNING")
    pending_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    try:
        from tools.hr_data_paths import collect_resume_paths_for_analysis

        pdf_paths, _ = collect_resume_paths_for_analysis(
            primary_dir=pending_dir,
            max_files=50,
            extensions=frozenset({".pdf"}),
        )
    except Exception as e:
        logger.warning("[Scheduler] collect_resume_paths_for_analysis 失败，回退仅 pending: %s", e)
        pdf_paths = (
            [p.resolve() for p in pending_dir.rglob("*.pdf") if p.is_file()]
            if pending_dir.exists()
            else []
        )
    if not pdf_paths:
        _reason = "无 PDF 可分析（pending/processed/副本 均未发现 PDF）"
        logger.warning(
            "[Scheduler] job_check_and_analyze 跳过: %s job=%s folder=%s",
            _reason,
            job_name,
            job_folder,
        )
        print(
            f"\n[Scheduler] ⚠️ {_reason}\n  job_name={job_name} job_folder={job_folder}\n",
            flush=True,
        )
        return {"fired": False, "unprocessed": 0, "reason": "无 PDF 可分析"}

    logger.info("[Scheduler] job_check_and_analyze 触发 职位=%s folder=%s pdf_count=%d", job_name, job_folder, len(pdf_paths))
    job_dir = PLUGIN_DATA_ROOT / job_folder
    try:
        passed_list, eliminated_list = _run_wasm_analysis_sync(job_config, pdf_paths, output_dir, job_folder)
        if not passed_list and not eliminated_list:
            _human = (
                "透析镜未分析到任何简历（无录用/淘汰结果）。"
                "已跳过排行榜更新与 Lark 同步；请检查 pending/processed/副本 中 PDF 是否有效。"
            )
            broadcast_log(f"[规则引擎] ⚠️ {_human}", "WARNING")
            logger.warning(
                "[Scheduler] job_check_and_analyze 无分析产出: %s folder=%s pdf_paths=%d（不写入排行榜、不通知多维表）",
                _human,
                job_folder,
                len(pdf_paths),
            )
            print(
                f"\n[Scheduler] ⚠️ {_human}\n"
                f"  job_name={job_name} job_folder={job_folder} pdf_paths={len(pdf_paths)}\n",
                flush=True,
            )
            return {
                "fired": True,
                "skipped_followup": True,
                "reason": "no_resume_analysis_output",
                "unprocessed": unprocessed,
                "passed": 0,
                "eliminated": 0,
            }

        _write_summary_md(job_dir, passed_list, eliminated_list, job_folder)
        broadcast_log("[规则引擎] 🏆 琅琊榜战报生成完毕！", "SUCCESS")
        # 同步到 Lark 多维表（从第一行开始写入，覆盖式更新，完成后通知 HR）
        summary_path = job_dir / "排行榜_Summary.md"
        if summary_path.exists():
            try:
                from l3_node.channels.lark import sync_bitable_from_md
                # 默认从第一行开始写入；一表多职位时可设 LARK_REPLACE_ENTIRE_TABLE=false 保留其他职位
                replace_from_first = os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "true").lower() in ("1", "true", "yes")
                sync_result = sync_bitable_from_md(md_path=str(summary_path), notify_group=True, replace_entire_table=replace_from_first)
                if sync_result.get("success"):
                    logger.info("[Scheduler] Lark 多维表已同步 job=%s count=%d", job_folder, sync_result.get("count", 0))
                else:
                    logger.warning("[Scheduler] Lark 同步失败: %s", sync_result.get("error", ""))
            except Exception as e:
                logger.warning("[Scheduler] Lark 同步异常: %s", e)

        state = _load_task_state()
        if job_key not in state:
            state[job_key] = {}
        state[job_key]["last_analyze_time"] = now.isoformat()
        _save_task_state(state)

        job_name = job_config.get("job_name", "")
        if unprocessed >= analyze_threshold and job_name:
            remove_scheduled_job(job_name)
            broadcast_log(f"[规则引擎] 简历已满{analyze_threshold}份，分析完成，排行榜已生成，无人值守流程结束。", "WARNING")

        return {
            "fired": True,
            "unprocessed": unprocessed,
            "passed": len(passed_list),
            "eliminated": len(eliminated_list),
            "last_analyze_time": now.isoformat(),
        }
    except Exception as e:
        logger.warning("[Scheduler] job_check_and_analyze Wasm 分析失败: %s", e)
        return {"fired": True, "error": str(e), "unprocessed": unprocessed}


# ---------------------------------------------------------------------------
# 调度器 API：增删任务
# ---------------------------------------------------------------------------
def add_scheduled_job(job_config: dict[str, Any]) -> dict[str, Any]:
    """向 scheduler 添加招聘定时任务。

    流程：发布后立即启动推荐牛人（每 15 分钟）；满 3 人打招呼后停，20 秒后启动抓简历（每 1 分钟）；
    满 4 份简历后触发 Agent 讨论简历，生成前 2 名排行榜并结束无人值守。
    """
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    job_name = (job_config.get("job_name") or "").strip()
    if not job_name:
        return {"ok": False, "error": "job_name 不能为空"}

    job_folder = _sanitize_job_folder(job_name)
    job_id_recommend = f"rec_{job_folder}_recommend"
    job_id_harvest = f"rec_{job_folder}_harvest"
    job_id_check = f"rec_{job_folder}_check"

    # 注入/覆盖关键参数
    job_config = dict(job_config)
    job_config["job_folder"] = job_folder
    job_config.setdefault("analyze_threshold", 4)
    job_config.setdefault("greet_target", 3)
    job_config.setdefault("harvest_delay_seconds", 20)
    job_config.setdefault("recommend_interval_minutes", 15)

    # 移除已存在的同岗位任务
    remove_scheduled_job(job_name)
    # 新启动岗位时清除停止标志，允许该岗位的定时任务执行（用户停止后再次发布时需此逻辑）
    set_recruitment_stopped(False)
    _sync_hr_workflow_pointer_for_lark(job_config)

    try:
        _now = datetime.now()
        rec_interval = int(job_config.get("recommend_interval_minutes", 15))

        # 1. 推荐牛人：发布后立即开始，每 15 分钟；满 3 人打招呼后自移除，并在 20 秒后启动抓简历
        scheduler.add_job(
            job_recommend_candidates,
            "interval",
            minutes=rec_interval,
            id=job_id_recommend,
            next_run_time=_now + timedelta(seconds=30),  # 30 秒后首次执行，留时间给 Chrome 准备
            args=[job_config],
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=180,
        )
        # 2. 抓简历：由推荐牛人满 3 人后动态添加，不在此处添加
        # 3. 规则引擎：每 1 分钟检查；pending≥4 份时触发分析→排行榜（前2名）→Lark 同步→结束
        scheduler.add_job(
            job_check_and_analyze,
            "interval",
            minutes=1,
            id=job_id_check,
            next_run_time=_now + timedelta(seconds=45),
            args=[job_config],
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        logger.info(
            "[Scheduler] 已添加岗位任务: %s (推荐每%dmin，满%d人后20s启动抓简历，满%d份简历触发分析)",
            job_folder, rec_interval, job_config.get("greet_target", 3),
            job_config.get("analyze_threshold", 4),
        )
        return {"ok": True, "job_name": job_name, "job_folder": job_folder}
    except Exception as e:
        logger.warning("[Scheduler] add_scheduled_job 失败: %s", e)
        return {"ok": False, "error": str(e)}


def remove_scheduled_job(job_name: str) -> dict[str, Any]:
    """暂停并移除岗位相关的三个定时任务"""
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    job_folder = _sanitize_job_folder((job_name or "").strip())
    if not job_folder:
        return {"ok": False, "error": "job_name 不能为空"}

    ids = [f"rec_{job_folder}_recommend", f"rec_{job_folder}_harvest", f"rec_{job_folder}_check"]
    removed = []
    for jid in ids:
        try:
            scheduler.remove_job(jid)
            removed.append(jid)
        except Exception:
            pass
    logger.info("[Scheduler] 已移除岗位任务: %s removed=%s", job_folder, removed)
    return {"ok": True, "job_name": job_name, "removed": removed}


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
                        "job_id_check": f"rec_{folder}_check",
                        "last_analyze_time": last_time,
                        "next_run": str(j.next_run_time) if j.next_run_time else None,
                    })
    return jobs
