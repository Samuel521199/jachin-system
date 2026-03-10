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
import logging
import re
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
L3_VOLUME_ROOT = Path.home() / ".jachin" / "client_volumes"
HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"
RE_SUMMARY_PASS = re.compile(
    r"---SUMMARY_PASS---\s*姓名：(.*?)\s*得分：(.*?)\s*核心优势：(.*?)\s*---SUMMARY_PASS---",
    re.DOTALL,
)
RE_SUMMARY_REJECT = re.compile(
    r"---SUMMARY_REJECT---\s*姓名：(.*?)\s*得分：(.*?)\s*淘汰原因：(.*?)\s*---SUMMARY_REJECT---",
    re.DOTALL,
)

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
# ---------------------------------------------------------------------------
def job_recommend_candidates(job_config: dict[str, Any]) -> dict[str, Any]:
    """调用 atom_greet_recommend_boss 在推荐牛人页面自动打招呼"""
    job_name = job_config.get("job_name", "")
    broadcast_log("[推荐牛人] 正在请求获取 Chrome 浏览器控制权...", "INFO")
    with chrome_lock:
        broadcast_log("[推荐牛人] 🟢 成功获取锁！正在执行打招呼 RPA...", "SUCCESS")
        try:
            cdp_url = job_config.get("cdp_url", "http://127.0.0.1:9222")
            jd_config_path = job_config.get("jd_config_path", "")
            import sys
            plugin_root = _PROJ_ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
            sys.path.insert(0, str(plugin_root))
            from tools.atom_greet_recommend_boss import atom_greet_recommend_boss
            result = atom_greet_recommend_boss(cdp_url=cdp_url, jd_config_path=jd_config_path)
            n = result.get("greeted_count", 0)
            broadcast_log(f"[推荐牛人] ✅ 任务执行成功，已推荐 {n} 人。", "SUCCESS")
            logger.info("[Scheduler] [%s] 推荐牛人完成，释放 Chrome 控制权: %s", job_name or "default", result)
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
def job_harvest_resumes(job_config: dict[str, Any]) -> dict[str, Any]:
    """调用 atom_inbox_harvester_full_flow，将 PDF 存入 pool_{job_folder}"""
    job_name = job_config.get("job_name", "")
    job_folder = _sanitize_job_folder(job_name)
    target_volume = f"pool_{job_folder}"
    save_dir = L3_VOLUME_ROOT / target_volume
    save_dir.mkdir(parents=True, exist_ok=True)

    broadcast_log("[收网抓取] 正在请求获取 Chrome 浏览器控制权...", "INFO")
    with chrome_lock:
        broadcast_log("[收网抓取] 🟢 成功获取锁！正在执行收网抓取 RPA...", "SUCCESS")
        try:
            cdp_url = job_config.get("cdp_url", "http://127.0.0.1:9222")
            max_items = int(job_config.get("max_count", 50))
            filter_tab = job_config.get("filter_tab", "全部") or "全部"
            request_if_no_resume = job_config.get("request_resume", True)
            import sys
            plugin_root = _PROJ_ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
            sys.path.insert(0, str(plugin_root))
            from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow
            result = atom_inbox_harvester_full_flow(
                cdp_url=cdp_url,
                job_text=job_name,
                download_to_pending=True,
                max_items=max_items,
                save_dir=str(save_dir),
                filter_tab=filter_tab,
                request_if_no_resume=request_if_no_resume,
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
    """统计未处理 PDF 数量：pool 下 PDF 数（含子目录）- output_dir 下 *_analysis.md 数"""
    pool_dir = L3_VOLUME_ROOT / f"pool_{job_folder}"
    if not pool_dir.exists():
        return 0
    # 收网保存到 pool_X/X/*.pdf，需递归查找
    pdf_count = len(list(pool_dir.rglob("*.pdf")))
    analysis_count = len(list(output_dir.glob("*_analysis.md")))
    return max(0, pdf_count - analysis_count)


def _run_wasm_analysis_sync(
    job_config: dict[str, Any],
    pdf_paths: list[Path],
    output_dir: Path,
    job_folder: str,
) -> tuple[list[dict], list[dict]]:
    """同步执行 Wasm 分析，返回 passed_list, eliminated_list（复用 recruitment_task 流式解析逻辑）"""
    import queue
    from l3_node.recruitment_task import (
        _job_name_fallback_jd,
        _fetch_jd_from_db,
        _save_jd_to_db,
    )
    from l3_node.skills import run_tool
    from l3_node.skills.loader import _extract_stem_from_hr_report
    from l3_node.hr_analysis_persist import persist_hr_analysis_batch_item

    jd_content = (job_config.get("jd_content") or "").strip()
    job_name = job_config.get("job_name", "")
    jd_final = jd_content or _job_name_fallback_jd(job_name) or _fetch_jd_from_db() or "岗位：请根据岗位名称评估候选人匹配度。"
    if jd_content:
        _save_jd_to_db(jd_content)

    target_volume = f"pool_{job_folder}"
    paths_str = "|||".join(str(p).replace("\\", "/") for p in pdf_paths if p)
    input_data: dict[str, Any] = {
        "target_dir": f"{target_volume}/{job_folder}",
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
            md_path = persist_hr_analysis_batch_item(
                HR_SKILL_ID, report, stem,
                config={"output_dir": str(output_dir), "output_dir_use_absolute": False},
            ) or ""
        pass_match = RE_SUMMARY_PASS.search(report or "") if report else None
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
                })

    t.join(timeout=2.0)
    if thread_result.get("error"):
        raise RuntimeError(thread_result["error"])
    return passed_list, eliminated_list


def _write_summary_md(output_dir: Path, passed_list: list, eliminated_list: list, job_folder: str) -> None:
    """生成排行榜 Summary"""
    from l3_node.recruitment_task import _write_summary_md as _write
    _write(output_dir, passed_list, eliminated_list, use_absolute_path=False, job_folder=job_folder)


def job_check_and_analyze(job_config: dict[str, Any]) -> dict[str, Any]:
    """
    动态规则引擎：每 5 分钟检查一次，满足条件时触发 Wasm 分析。

    条件：analyze_threshold（满 N 份）或 analyze_interval_hours（每 N 小时）
    规则：(未处理数量 >= threshold) OR (距上次分析 >= interval_hours) 且 未处理 > 0
    """
    job_name = job_config.get("job_name", "")
    job_folder = _sanitize_job_folder(job_name)
    output_dir_raw = job_config.get("output_dir", "") or f"data/hr_analysis/{job_folder}"
    from l3_node.hr_analysis_persist import _resolve_safe_dir
    resolved = _resolve_safe_dir(output_dir_raw.strip(), _PROJ_ROOT, use_absolute_path=False)
    output_dir = resolved or (_PROJ_ROOT / "data" / "hr_analysis" / job_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    analyze_threshold = int(job_config.get("analyze_threshold", 2))
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

    broadcast_log("[规则引擎] 检测到简历池达到阈值，正在唤醒 Wasm 透析镜！", "WARNING")
    pool_dir = L3_VOLUME_ROOT / f"pool_{job_folder}"
    # 收网保存到 pool_X/X/*.pdf，需递归查找
    pdf_paths = [p.resolve() for p in pool_dir.rglob("*.pdf") if p.is_file()] if pool_dir.exists() else []
    if not pdf_paths:
        return {"fired": False, "unprocessed": 0, "reason": "无 PDF 可分析"}

    logger.info("[Scheduler] job_check_and_analyze 触发分析 job=%s pdf_count=%d", job_folder, len(pdf_paths))
    try:
        passed_list, eliminated_list = _run_wasm_analysis_sync(job_config, pdf_paths, output_dir, job_folder)
        _write_summary_md(output_dir, passed_list, eliminated_list, job_folder)
        broadcast_log("[规则引擎] 🏆 琅琊榜战报生成完毕！", "SUCCESS")

        state = _load_task_state()
        if job_key not in state:
            state[job_key] = {}
        state[job_key]["last_analyze_time"] = now.isoformat()
        _save_task_state(state)

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
    """向 scheduler 添加三个定时任务"""
    if not _APSCHEDULER_AVAILABLE:
        return {"ok": False, "error": "apscheduler 未安装"}

    job_name = (job_config.get("job_name") or "").strip()
    if not job_name:
        return {"ok": False, "error": "job_name 不能为空"}

    job_folder = _sanitize_job_folder(job_name)
    job_id_recommend = f"rec_{job_folder}_recommend"
    job_id_harvest = f"rec_{job_folder}_harvest"
    job_id_check = f"rec_{job_folder}_check"

    # 移除已存在的同岗位任务
    remove_scheduled_job(job_name)

    try:
        # 极速测试模式：recommend 1分钟、harvest 2分钟、check 1分钟
        scheduler.add_job(
            job_recommend_candidates,
            "interval",
            minutes=1,
            id=job_id_recommend,
            args=[job_config],
            replace_existing=True,
        )
        scheduler.add_job(
            job_harvest_resumes,
            "interval",
            minutes=2,
            id=job_id_harvest,
            args=[job_config],
            replace_existing=True,
        )
        scheduler.add_job(
            job_check_and_analyze,
            "interval",
            minutes=1,
            id=job_id_check,
            args=[job_config],
            replace_existing=True,
        )
        logger.info("[Scheduler] 已添加岗位任务: %s (recommend/harvest/check)", job_folder)
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
