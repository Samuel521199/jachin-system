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

_PROJ_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DATA_ROOT = _PROJ_ROOT / "skills_repo" / "plugin" / "data"
_PLUGIN_TOOLS = _PROJ_ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"
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

# 任务状态持久化：记录每个岗位的最后分析时间
DATA_HR = _PROJ_ROOT / "data" / "hr_analysis"
TASK_STATE_FILE = DATA_HR / "scheduler_state.json"

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
    DATA_HR.mkdir(parents=True, exist_ok=True)
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


def _sanitize_job_folder(job_name: str, max_len: int = 60) -> str:
    """将岗位名转为安全文件夹名"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


# ---------------------------------------------------------------------------
# Job 1: 定时推荐牛人（打招呼 MCP）
# 每 15 分钟执行，每轮成功打招呼 3 人即结束本轮，20 秒后启动抓简历
# ---------------------------------------------------------------------------
def job_recommend_candidates(job_config: dict[str, Any]) -> dict[str, Any]:
    """调用 atom_greet_recommend_boss 在推荐牛人页面自动打招呼。满 3 人即停，20 秒后启动抓简历。"""
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过推荐牛人任务")
        return {"success": False, "greeted_count": 0, "skipped": "recruitment_stopped"}
    job_name = job_config.get("job_name", "")
    job_folder = _sanitize_job_folder(job_name)
    greet_target = int(job_config.get("greet_target", 3))
    harvest_delay_seconds = int(job_config.get("harvest_delay_seconds", 20))

    jd_config_path = job_config.get("jd_config_path", "")
    broadcast_log(f"[推荐牛人] 职位={job_name} jd={jd_config_path or '(未配置)'}", "INFO")
    with chrome_lock:
        broadcast_log("[推荐牛人] 🟢 成功获取锁！正在执行打招呼 RPA...", "SUCCESS")
        try:
            cdp_url = job_config.get("cdp_url", "http://127.0.0.1:9222")
            if str(_PLUGIN_TOOLS) not in sys.path:
                sys.path.insert(0, str(_PLUGIN_TOOLS))
            from tools.atom_greet_recommend_boss import atom_greet_recommend_boss
            result = atom_greet_recommend_boss(cdp_url=cdp_url, jd_config_path=jd_config_path)
            n = result.get("greeted_count", 0)
            broadcast_log(f"[推荐牛人] ✅ 任务执行成功，已推荐 {n} 人。", "SUCCESS")
            logger.info("[Scheduler] [%s] 推荐牛人完成: %s", job_name or "default", result)

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
            return result
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
        if str(_PLUGIN_TOOLS) not in sys.path:
            sys.path.insert(0, str(_PLUGIN_TOOLS))
        from tools.atom_post_job_boss import load_jd_config, get_jd_select
        jd_data = load_jd_config(jd_path, job_name)
        return get_jd_select(jd_data) or job_name
    except Exception:
        return job_name


def job_harvest_resumes(job_config: dict[str, Any]) -> dict[str, Any]:
    """调用 atom_inbox_harvester_full_flow，从上往下依次遍历整个列表，直至简历满或处理完毕。
    在「全部职位」中选择 jd_select 对应岗位，仅抓取该岗位简历。"""
    if is_recruitment_stopped():
        logger.info("[Scheduler] 招聘已停止，跳过收网抓取任务")
        return {"success": False, "downloaded": 0, "skipped": "recruitment_stopped"}
    job_name = job_config.get("job_name", "")
    job_text = _job_text_for_harvest(job_config)
    job_folder = _sanitize_job_folder(job_name)
    save_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    save_dir.mkdir(parents=True, exist_ok=True)
    output_dir = PLUGIN_DATA_ROOT / job_folder / "result"
    output_dir.mkdir(parents=True, exist_ok=True)
    analyze_threshold = int(job_config.get("analyze_threshold", 4))

    broadcast_log(f"[收网抓取] 职位={job_name} folder={job_folder} job_select={job_text}", "INFO")
    with chrome_lock:
        broadcast_log(f"[收网抓取] 🟢 成功获取锁！目标职位: {job_text}", "SUCCESS")
        try:
            cdp_url = job_config.get("cdp_url", "http://127.0.0.1:9222")
            max_items = int(job_config.get("max_count", 50))
            filter_tab = job_config.get("filter_tab", "全部") or "全部"
            request_if_no_resume = job_config.get("request_resume", True)
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
            # stop_when_downloaded: 当本次下载使总未处理数达阈值时提前结束
            stop_when = max(0, analyze_threshold - unprocessed_before)
            if str(_PLUGIN_TOOLS) not in sys.path:
                sys.path.insert(0, str(_PLUGIN_TOOLS))
            from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow
            result = atom_inbox_harvester_full_flow(
                cdp_url=cdp_url,
                job_text=job_text,
                download_to_pending=True,
                max_items=max_items,
                save_dir=str(save_dir),
                job_folder=job_folder,
                filter_tab=filter_tab,
                request_if_no_resume=request_if_no_resume,
                use_all_positions=bool(job_config.get("use_all_positions", True)),
                max_ops_per_run=0,
                stop_when_downloaded=stop_when if stop_when > 0 else 0,
            )
            n = result.get("downloaded", 0)
            broadcast_log(f"[收网抓取] ✅ 任务执行成功，已下载 {n} 份简历。", "SUCCESS")
            logger.info("[Scheduler] [%s] 收网抓取完成，释放 Chrome 控制权: %s", job_name or "default", result)
            return result
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
    """统计未处理 PDF 数量：data/{职位}/pending 下 PDF 数 - output_dir 下 *_analysis.md 数"""
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
        if str(_PLUGIN_TOOLS) not in sys.path:
            sys.path.insert(0, str(_PLUGIN_TOOLS))
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
    from l3_node.recruitment_task import (
        _job_name_fallback_jd,
        _fetch_jd_from_db,
        _save_jd_to_db,
    )
    from l3_node.skills import run_tool
    from l3_node.skills.loader import _extract_stem_from_hr_report
    from l3_node.hr_analysis_persist import persist_hr_analysis_batch_item

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
            # 使用 resolve() 与正斜杠，确保 skills_repo/plugin/data/{职位}/result 正确解析
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
    from l3_node.recruitment_task import _write_summary_md as _write
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
    pdf_paths = [p.resolve() for p in pending_dir.rglob("*.pdf") if p.is_file()] if pending_dir.exists() else []
    if not pdf_paths:
        return {"fired": False, "unprocessed": 0, "reason": "无 PDF 可分析"}

    logger.info("[Scheduler] job_check_and_analyze 触发 职位=%s folder=%s pdf_count=%d", job_name, job_folder, len(pdf_paths))
    job_dir = PLUGIN_DATA_ROOT / job_folder
    try:
        passed_list, eliminated_list = _run_wasm_analysis_sync(job_config, pdf_paths, output_dir, job_folder)
        _write_summary_md(job_dir, passed_list, eliminated_list, job_folder)
        broadcast_log("[规则引擎] 🏆 琅琊榜战报生成完毕！", "SUCCESS")
        # 同步到 Lark 多维表（从第一行开始写入，覆盖式更新，完成后通知 HR）
        summary_path = job_dir / "排行榜_Summary.md"
        if summary_path.exists():
            try:
                if str(_PLUGIN_TOOLS) not in sys.path:
                    sys.path.insert(0, str(_PLUGIN_TOOLS))
                from tools.atom_lark_bitable_sync import atom_lark_bitable_sync
                # 默认从第一行开始写入；一表多职位时可设 LARK_REPLACE_ENTIRE_TABLE=false 保留其他职位
                replace_from_first = os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "true").lower() in ("1", "true", "yes")
                sync_result = atom_lark_bitable_sync(md_path=str(summary_path), notify_group=True, replace_entire_table=replace_from_first)
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
    job_config.setdefault("analyze_threshold", 4)
    job_config.setdefault("greet_target", 3)
    job_config.setdefault("harvest_delay_seconds", 20)
    job_config.setdefault("recommend_interval_minutes", 15)

    # 移除已存在的同岗位任务
    remove_scheduled_job(job_name)
    # 新启动岗位时清除停止标志，允许该岗位的定时任务执行（用户停止后再次发布时需此逻辑）
    set_recruitment_stopped(False)

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
