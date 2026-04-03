"""
L3 招聘全链路聚合调度引擎

一键式全链路：收网 → HR 透析镜，对用户隐藏 MCP/Wasm 底层概念。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

# 双通道正则：录用 / 淘汰（兼容全角/半角冒号）
RE_SUMMARY_PASS = re.compile(
    r"---SUMMARY_PASS---\s*姓名[：:]\s*(.*?)\s*得分[：:]\s*(.*?)\s*核心优势[：:]\s*(.*?)\s*---SUMMARY_PASS---",
    re.DOTALL,
)
RE_SUMMARY_REJECT = re.compile(
    r"---SUMMARY_REJECT---\s*姓名[：:]\s*(.*?)\s*得分[：:]\s*(.*?)\s*淘汰原因[：:]\s*(.*?)\s*---SUMMARY_REJECT---",
    re.DOTALL,
)

logger = logging.getLogger("l3_node")

from recruitment_scheduler import PLUGIN_DATA_ROOT
from hr_analysis_persist import get_resolve_base

_RESOLVE_BASE = get_resolve_base()
HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"
HR_SKILL_CONFIG_ID = "hr-analyzer4"
# 仅当无岗位名、无数据库、无表单 JD 时的最后兜底；禁止再使用「云边协同」等测试样例，以免透析镜误判
DEFAULT_JD = (
    "请根据候选人简历与当前沟通中的目标岗位，评估匹配度与风险；"
    "若未提供具体 JD，请依据简历中的求职意向与技能栈给出录用/淘汰建议，并在报告中说明「未提供正式 JD」。"
)


def _fetch_jd_from_db() -> str:
    """从 skill_registry 数据库读取上次保存的岗位 JD，供无 JD 时兜底。"""
    try:
        from core.skill_registry import get_skill_config
        cfg = get_skill_config(HR_SKILL_ID)
        jd = (cfg.get("JD_template") or cfg.get("jd_template") or "").strip()
        if jd:
            logger.info("[Recruitment] 岗位 JD 已从数据库兜底 len=%d", len(jd))
            return jd
    except Exception as e:
        logger.debug("[Recruitment] 本地 skill_registry 读取失败: %s", e)
    try:
        import httpx
        cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
        l2_url = "http://localhost:18888"
        sub_account_id = ""
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                l2_url = (data.get("l2_base_url") or l2_url).rstrip("/")
                sub_account_id = data.get("sub_account_id") or ""
            except Exception:
                pass
        url = f"{l2_url}/api/v2/skills/{HR_SKILL_CONFIG_ID}/config"
        headers = {}
        if sub_account_id:
            headers["X-Sub-Account-Id"] = sub_account_id
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.get(url, headers=headers or None)
            if r.is_success:
                resp = r.json()
                cfg = resp.get("config") or {}
                jd = (cfg.get("JD_template") or cfg.get("jd_template") or "").strip()
                if jd:
                    logger.info("[Recruitment] 岗位 JD 已从 L2 数据库兜底 len=%d", len(jd))
                    return jd
    except Exception as e:
        logger.debug("[Recruitment] L2 拉取 JD 兜底失败: %s", e)
    return ""


def _save_jd_to_db(jd_content: str) -> None:
    """将岗位 JD 保存到 skill_registry，供下次无 JD 时兜底。"""
    if not jd_content or not jd_content.strip():
        return
    try:
        from core.skill_registry import update_skill_config
        update_skill_config(HR_SKILL_ID, {"JD_template": jd_content.strip()})
        logger.info("[Recruitment] 岗位 JD 已保存至数据库 len=%d", len(jd_content))
        return
    except Exception as e:
        logger.debug("[Recruitment] 本地 skill_registry 保存失败: %s", e)
    try:
        import httpx
        cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
        l2_url = "http://localhost:18888"
        sub_account_id = ""
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                l2_url = (data.get("l2_base_url") or l2_url).rstrip("/")
                sub_account_id = data.get("sub_account_id") or ""
            except Exception:
                pass
        url = f"{l2_url}/api/v2/skills/{HR_SKILL_CONFIG_ID}/config"
        headers = {"Content-Type": "application/json"}
        if sub_account_id:
            headers["X-Sub-Account-Id"] = sub_account_id
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.put(url, json={"JD_template": jd_content.strip()}, headers=headers)
            if r.is_success:
                logger.info("[Recruitment] 岗位 JD 已保存至 L2 数据库 len=%d", len(jd_content))
    except Exception as e:
        logger.debug("[Recruitment] L2 保存 JD 失败: %s", e)


def _job_name_fallback_jd(job_name: str) -> str:
    """当表单未传 JD 时，用岗位名生成兜底 JD，避免错误使用云边协同"""
    jn = (job_name or "").strip()
    if not jn:
        return ""
    return f"岗位：{jn}\n\n请根据岗位名称评估候选人匹配度，重点考察与岗位相关的技能与经验。"


def _to_file_link(path: str, label: str = "查看") -> str:
    """将绝对路径转为 Markdown 兼容的 file:// 链接"""
    if not path or not path.strip():
        return "-"
    p = path.strip().replace(os.sep, "/")
    return f"[{label}](file:///{p})"


def _extract_candidate_fields(report: str) -> dict:
    """从 HR 分析报告中提取学历、经验、薪资等字段"""
    r = (report or "").strip()
    out = {"education": "-", "experience": "-", "salary": "-"}
    if not r:
        return out
    for pat in [r"学历[：:]\s*([^\n]+)", r"(本科|硕士|博士|大专|专科|研究生)[^\n]*"]:
        m = re.search(pat, r)
        if m:
            out["education"] = (m.group(1) or "").strip()[:20]
            break
    for pat in [r"经验[：:]\s*([^\n]+)", r"(\d+[-~]?\d*)\s*年", r"工作[^\n]*?(\d+[-~]?\d*)\s*年"]:
        m = re.search(pat, r)
        if m:
            out["experience"] = (m.group(1) or "").strip()[:30]
            break
    for pat in [r"薪资[：:]\s*([^\n]+)", r"期望[^\n]*?(\d+[-~]?\d*)[kK]", r"(\d+[-~]\d+)[kK]"]:
        m = re.search(pat, r)
        if m:
            out["salary"] = (m.group(1) or "").strip()[:20]
            break
    return out


def _score_to_stars(score: float) -> str:
    """将得分转为星级"""
    n = max(0, min(5, round(score)))
    return "★" * n + "☆" * (5 - n)


def _hr_max_collect_files() -> int:
    try:
        return max(1, min(500, int(os.environ.get("HR_ANALYZER_MAX_FILES", "200"))))
    except ValueError:
        return 200


def _append_verdict_from_recruitment_report(
    report: str,
    fn: str,
    pdf_path: str,
    md_path: str,
    passed_list: list[dict[str, Any]],
    eliminated_list: list[dict[str, Any]],
) -> None:
    """与 NDJSON progress 消费逻辑一致：从单份报告 Markdown 解析录用/淘汰。"""
    if not report or not isinstance(report, str):
        return
    fields = _extract_candidate_fields(report)
    pass_match = RE_SUMMARY_PASS.search(report)
    if pass_match:
        try:
            score = float((pass_match.group(2) or "0").strip())
        except (ValueError, TypeError):
            score = 0.0
        passed_list.append({
            "name": (pass_match.group(1) or "").strip(),
            "score": score,
            "advantage": (pass_match.group(3) or "").strip(),
            "pdf_path": pdf_path or "",
            "md_path": md_path or "",
            "education": fields.get("education", "-"),
            "experience": fields.get("experience", "-"),
            "salary": fields.get("salary", "-"),
            "stars": _score_to_stars(score),
        })
        return
    reject_match = RE_SUMMARY_REJECT.search(report)
    if reject_match:
        try:
            score = float((reject_match.group(2) or "0").strip())
        except (ValueError, TypeError):
            score = 0.0
        eliminated_list.append({
            "name": (reject_match.group(1) or "").strip(),
            "score": score,
            "reason": (reject_match.group(3) or "").strip(),
            "pdf_path": pdf_path or "",
            "md_path": md_path or "",
            "education": fields.get("education", "-"),
            "experience": fields.get("experience", "-"),
            "salary": fields.get("salary", "-"),
            "stars": _score_to_stars(score),
        })


# 符合要求的前 N 名进入推荐面试区（达标后停止该岗位招聘）
TOP_N_PASSED = 2


def _write_summary_md(
    output_dir: Path,
    passed_list: list[dict[str, Any]],
    eliminated_list: list[dict[str, Any]],
    use_absolute_path: bool = False,
    job_folder: str = "",
) -> None:
    """生成终极排行榜：每个职位永远只有一份 {岗位名}_排行榜_Summary.md，只输出符合要求的前 N 名，每次筛选输出覆盖旧文件。"""
    if not passed_list and not eliminated_list:
        _skip = "跳过写入排行榜_Summary.md：透析镜未产出任何录用/淘汰记录"
        logger.warning("[Recruitment] %s job_folder=%s", _skip, job_folder or output_dir.name)
        print(f"\n[Recruitment] ⚠️ {_skip} output_dir={output_dir}\n", flush=True)
        return
    passed_sorted = sorted(passed_list, key=lambda x: x.get("score", 0), reverse=True)[:TOP_N_PASSED]
    eliminated_sorted = sorted(eliminated_list, key=lambda x: x.get("score", 0), reverse=True)

    def _cell(s: str, max_len: int = 40) -> str:
        v = (s or "-").replace("|", "｜").replace("\n", " ").strip()
        return (v[:max_len] + "…") if len(v) > max_len else v

    job_title = f" [{job_folder}]" if job_folder else ""
    lines = [
        f"# 🏆 AI 招聘决断大盘 - 总榜单{job_title}",
        "",
        "## 🌟 推荐面试区 (得分 >= 3.0)",
        "",
        "| 排名 | 求职者姓名 | 学历 | 经验 | 薪资要求 | 打分 | 推荐理由 | 推荐星级 | 原简历 / Agent分析 |",
        "|------|------------|------|------|----------|------|----------|----------|---------------------|",
    ]
    for i, row in enumerate(passed_sorted, 1):
        md_link = _to_file_link(row.get("md_path", ""), "分析")
        pdf_link = _to_file_link(row.get("pdf_path", ""), "原简历")
        links = f"{pdf_link} {md_link}" if pdf_link != "-" else md_link
        adv = _cell(row.get("advantage", ""), 60)
        lines.append(
            f"| {i} | {_cell(row.get('name','-'),20)} | {_cell(row.get('education','-'),12)} | {_cell(row.get('experience','-'),12)} | "
            f"{_cell(row.get('salary','-'),12)} | {row.get('score', 0):.1f} | {adv} | {row.get('stars','-')} | {links} |"
        )

    lines.extend([
        "",
        "## ❌ 淘汰区 (得分 < 3.0)",
        "",
        "| 排名 | 求职者姓名 | 学历 | 经验 | 薪资要求 | 打分 | 淘汰原因 | 推荐星级 | 原简历 / Agent分析 |",
        "|------|------------|------|------|----------|------|----------|----------|---------------------|",
    ])
    for i, row in enumerate(eliminated_sorted, 1):
        md_link = _to_file_link(row.get("md_path", ""), "分析")
        pdf_link = _to_file_link(row.get("pdf_path", ""), "原简历")
        links = f"{pdf_link} {md_link}" if pdf_link != "-" else md_link
        reason = _cell(row.get("reason", ""), 60)
        lines.append(
            f"| {i} | {_cell(row.get('name','-'),20)} | {_cell(row.get('education','-'),12)} | {_cell(row.get('experience','-'),12)} | "
            f"{_cell(row.get('salary','-'),12)} | {row.get('score', 0):.1f} | {reason} | {row.get('stars','-')} | {links} |"
        )

    # 文件名：排行榜_Summary.md（固定名，每次覆盖，每个职位专属一份）
    summary_path = output_dir / "排行榜_Summary.md"
    try:
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[Recruitment] 终极排行榜已写入 %s passed=%d eliminated=%d", summary_path, len(passed_sorted), len(eliminated_sorted))
    except Exception as e:
        logger.warning("[Recruitment] 写入排行榜失败: %s", e)


async def run_recruitment_task_stream(
    job_name: str,
    max_count: int = 20,
    filter_tab: str = "全部",
    request_resume: bool = True,
    output_dir: str = "",
    force_reanalyze: bool = False,
    jd_content: str = "",
    focus_keywords: str = "",
    strictness: str = "standard",
) -> AsyncIterator[dict[str, Any]]:
    """
    全链路招聘任务：收网 → HR 透析镜，SSE 流式进度。
    """
    import sys

    from tools.hr_data_paths import get_job_jd_path

    jd_path = get_job_jd_path(job_name)
    if not jd_path.exists():
        yield {
            "step": 0,
            "msg": f"未找到岗位 jd.json（{jd_path}）。请先按「职位+地区+薪资」发帖并生成 jd，或保证磁盘上仅有一条匹配该岗位名的记录。",
            "error": "missing_jd",
        }
        return
    job_folder = jd_path.parent.name
    save_dir = jd_path.parent / "pending"
    save_dir.mkdir(parents=True, exist_ok=True)

    # 从 hr_recruitment/{{目录键}}/jd.json 获取 jd_select 用于 Boss「全部职位」精确匹配
    job_text = job_name
    try:
        from recruitment_scheduler import _get_hr_recruitment_plugin_root

        if jd_path.exists():
            _hr = _get_hr_recruitment_plugin_root()
            if _hr and str(_hr) not in sys.path:
                sys.path.insert(0, str(_hr))
            from tools.atom_post_job_boss import load_jd_config, get_jd_select

            jd_data = load_jd_config(str(jd_path), job_name)
            job_text = get_jd_select(jd_data) or job_text
    except Exception:
        pass

    # Step 1
    yield {"step": 1, "msg": "🚀 正在启动自动化收网程序..."}

    harvester_result: dict[str, Any] = {}
    harvester_done = threading.Event()

    def _run_harvester() -> None:
        nonlocal harvester_result
        try:
            from recruitment_scheduler import _run_hr_recruitment_dag_tick

            logger.info("[Recruitment] 经 HR DAG 收网 tick request_if_no_resume=%s filter_tab=%s", request_resume, filter_tab or "全部")
            jc = {
                "job_name": job_name,
                "job_folder": job_folder,
                "jd_config_path": str(jd_path) if jd_path.exists() else "",
                "max_count": max_count,
                "analyze_threshold": 10**6,
                "filter_tab": filter_tab or "全部",
                "request_resume": request_resume,
                "use_all_positions": False,
                "_dag_harvest_always_run": True,
                "skip_hr_plan_init_node": True,
                "skip_hr_progress_restore": True,
            }
            harvester_result = _run_hr_recruitment_dag_tick(jc, tick_mode="harvest")
            harvester_result["downloaded"] = int(harvester_result.get("downloaded", 0) or 0)
        except Exception as e:
            harvester_result = {"success": False, "error": str(e)}
        finally:
            harvester_done.set()

    t = threading.Thread(target=_run_harvester, daemon=True)
    t.start()
    for _ in range(1200):  # 600s = 1200 * 0.5
        if harvester_done.is_set():
            break
        await asyncio.sleep(0.5)

    if not harvester_result.get("success"):
        yield {"step": 1, "msg": f"⚠️ 收网失败: {harvester_result.get('error', '未知错误')}"}
        return

    downloaded = harvester_result.get("downloaded", 0)
    requested = harvester_result.get("requested_count", 0)
    yield {"step": 2, "msg": f"📥 收网完毕！求简历 {requested} 份，成功落盘 {downloaded} 份简历。"}

    if downloaded == 0:
        yield {"step": 3, "msg": "暂无简历可分析，跳过 HR 透析镜。", "status": "done", "total": 0}
        return

    # 分析报告输出到 data/{职位}/result/
    if output_dir and str(output_dir).strip():
        from hr_analysis_persist import _resolve_safe_dir
        use_abs = Path(output_dir).expanduser().is_absolute()
        resolved_output = _resolve_safe_dir(str(output_dir).strip(), _RESOLVE_BASE, use_absolute_path=use_abs)
        output_dir_use_absolute = bool(resolved_output and use_abs)
    else:
        resolved_output = PLUGIN_DATA_ROOT / job_folder / "result"
        output_dir_use_absolute = False
    if not resolved_output:
        resolved_output = PLUGIN_DATA_ROOT / job_folder / "result"
    resolved_output.mkdir(parents=True, exist_ok=True)

    # 生成 skip_files 黑名单：force_reanalyze=False 时，遍历已有 *_analysis.md 推导 PDF stem
    skip_files: list[str] = []
    if not force_reanalyze:
        for p in resolved_output.glob("*_analysis.md"):
            stem = p.stem
            if stem.endswith("_analysis"):
                base = stem[: -len("_analysis")]
                if base:
                    skip_files.append(base)
        logger.info("[Recruitment] skip_files 黑名单: %s", skip_files)

    yield {"step": 2, "msg": "正在唤醒 HR 透析镜..."}

    try:
        from recruitment_scheduler import _get_hr_recruitment_plugin_root

        _hrp = _get_hr_recruitment_plugin_root()
        if _hrp and str(_hrp) not in sys.path:
            sys.path.insert(0, str(_hrp))
        from tools.hr_data_paths import collect_resume_paths_for_analysis
    except ImportError as e:
        logger.warning("[Recruitment] 无法加载 collect_resume_paths_for_analysis: %s", e)
        collect_resume_paths_for_analysis = None  # type: ignore[misc, assignment]

    # 直接传入 PDF 绝对路径；pending 无文件时在同职位 processed/副本 中兜底
    pdf_paths = [Path(p).resolve() for p in (harvester_result.get("pdf_paths") or []) if p]
    _pdf_ext = frozenset({".pdf"})
    if collect_resume_paths_for_analysis:
        if force_reanalyze:
            pdf_paths, _ = collect_resume_paths_for_analysis(
                primary_dir=save_dir, max_files=_hr_max_collect_files(), extensions=_pdf_ext
            )
            if pdf_paths:
                logger.info(
                    "[Recruitment] force_reanalyze：pending/processed/副本 合并 %d 份简历",
                    len(pdf_paths),
                )
        elif not pdf_paths:
            pdf_paths, _ = collect_resume_paths_for_analysis(
                primary_dir=save_dir, max_files=_hr_max_collect_files(), extensions=_pdf_ext
            )
            if pdf_paths:
                logger.info(
                    "[Recruitment] pdf_paths 兜底：收网列表为空，已从 pending/processed/副本 合并 %d 份",
                    len(pdf_paths),
                )
    if not pdf_paths:
        pending_pdfs = list(save_dir.rglob("*.pdf")) if save_dir.exists() else []
        pdf_paths = [p.resolve() for p in pending_pdfs if p.is_file()]
        if pdf_paths:
            logger.info("[Recruitment] pdf_paths 兜底：仅从 pending rglob %d 份", len(pdf_paths))
    skip_set = set(skip_files)
    pdf_paths = [p for p in pdf_paths if p.stem not in skip_set]
    if not pdf_paths:
        yield {
            "step": 3,
            "msg": "📋 无待透析简历（均已存在分析报告或目录为空）。",
            "status": "done",
            "total": 0,
        }
        return
    paths_str = "|||".join(str(p).replace("\\", "/") for p in pdf_paths if p)
    if not paths_str:
        _msg = (
            "⚠️ 无法获取简历路径，透析镜无法启动。"
            "请检查 pending / processed / 副本 目录或收网日志。"
        )
        logger.warning("[Recruitment] %s job_folder=%s save_dir=%s", _msg, job_folder, save_dir)
        print(f"\n[Recruitment] {_msg}\n  job_folder={job_folder}\n  save_dir={save_dir}\n", flush=True)
        yield {"step": 3, "msg": _msg, "status": "error"}
        return

    # 表单未传 JD 时优先用岗位名兜底，避免错误使用 skill_registry 中的云边协同
    jd_final = (jd_content or "").strip() or _job_name_fallback_jd(job_name) or _fetch_jd_from_db() or DEFAULT_JD
    if jd_content and jd_content.strip():
        logger.info("[Recruitment] 岗位 JD 已从表单传入 len=%d preview=%s", len(jd_content), (jd_content[:80] + "…") if len(jd_content) > 80 else jd_content)
        _save_jd_to_db(jd_content)
    else:
        if "云边" in jd_final or "云边协同" in jd_final:
            logger.warning(
                "[Recruitment] 未从表单传入岗位 JD，且兜底文本含「云边」类关键词（可能来自历史 DB 缓存）。"
                "请在招聘大盘填写正式 JD；透析镜 Wasm 会尽量按 job_name 纠偏"
            )
        else:
            logger.info("[Recruitment] 未从表单传入岗位 JD，使用岗位名兜底 len=%d", len(jd_final))
    print(f"\n[Recruitment] 实际传入 Wasm 的岗位 JD (len={len(jd_final)}):\n{jd_final}\n", flush=True)
    input_data: dict[str, Any] = {
        "target_dir": str(save_dir),
        "jd_template": jd_final,
        "job_name": (job_name or "").strip(),
        "strictness": strictness or "standard",
        "output_dir": str(resolved_output),
    }
    if focus_keywords and str(focus_keywords).strip():
        input_data["focus_keywords"] = str(focus_keywords).strip()

    force_batch = os.environ.get("HR_ANALYZER_BATCH_WASM", "").strip().lower() in ("1", "true", "yes")
    passed_list: list[dict[str, Any]] = []
    eliminated_list: list[dict[str, Any]] = []
    thread_result: dict[str, Any] = {"done": True, "error": None, "result": None}

    if len(pdf_paths) == 1 or force_batch:
        input_data["_hr_files"] = paths_str
        ndjson_queue: queue.Queue[str] = queue.Queue()
        thread_result = {"done": False, "error": None, "result": None}

        def _run_wasm() -> None:
            try:
                from l3_node.primitives import run_tool
                inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
                r = run_tool(HR_SKILL_ID, inp, allowed_skills=None, ndjson_queue=ndjson_queue)
                thread_result["result"] = r
            except Exception as e:
                thread_result["error"] = str(e)
            finally:
                thread_result["done"] = True
                ndjson_queue.put(json.dumps({"status": "thread_done"}))

        tw = threading.Thread(target=_run_wasm, daemon=True)
        tw.start()

        seen_done = False
        while not seen_done:
            try:
                line = ndjson_queue.get(timeout=0.3)
            except queue.Empty:
                if thread_result["done"]:
                    break
                await asyncio.sleep(0.05)
                continue
            line = (line or "").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if status == "thread_done":
                break
            if status == "debug":
                jd_len = item.get("jd_len", 0)
                jd_preview = item.get("jd_preview", "")
                logger.info("[Recruitment] Wasm 收到岗位 JD jd_len=%d jd_preview=%s", jd_len, (jd_preview or "")[:80])
                if jd_len == 0:
                    logger.warning("[Recruitment] Wasm 侧 jd_len=0，岗位 JD 可能未正确传入，请检查 jd_path/jd_template")
                continue
            if status == "done":
                seen_done = True
                break
            if status == "progress":
                report = item.get("report_content")
                fn = item.get("filename") or ""
                pdf_path = next((str(p) for p in pdf_paths if fn and (str(p).endswith(fn) or Path(p).name == fn)), None)
                md_path: str | None = None
                if report and isinstance(report, str):
                    from hr_analysis_persist import persist_hr_analysis_batch_item
                    from l3_node.primitives.tools.loader import _extract_stem_from_hr_report
                    stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                    if not stem or re.match(r"^resume_\d+$", stem):
                        stem = _extract_stem_from_hr_report(report or "") or stem or "unknown"
                    md_path = persist_hr_analysis_batch_item(
                        HR_SKILL_ID,
                        report,
                        stem,
                        config={"output_dir": str(resolved_output), "output_dir_use_absolute": output_dir_use_absolute},
                    )
                    pass_match = RE_SUMMARY_PASS.search(report)
                    fields = _extract_candidate_fields(report)
                    if pass_match:
                        try:
                            score = float((pass_match.group(2) or "0").strip())
                        except (ValueError, TypeError):
                            score = 0.0
                        passed_list.append({
                            "name": (pass_match.group(1) or "").strip(),
                            "score": score,
                            "advantage": (pass_match.group(3) or "").strip(),
                            "pdf_path": pdf_path or "",
                            "md_path": md_path or "",
                            "education": fields.get("education", "-"),
                            "experience": fields.get("experience", "-"),
                            "salary": fields.get("salary", "-"),
                            "stars": _score_to_stars(score),
                        })
                    else:
                        reject_match = RE_SUMMARY_REJECT.search(report)
                        if reject_match:
                            try:
                                score = float((reject_match.group(2) or "0").strip())
                            except (ValueError, TypeError):
                                score = 0.0
                            eliminated_list.append({
                                "name": (reject_match.group(1) or "").strip(),
                                "score": score,
                                "reason": (reject_match.group(3) or "").strip(),
                                "pdf_path": pdf_path or "",
                                "md_path": md_path or "",
                                "education": fields.get("education", "-"),
                                "experience": fields.get("experience", "-"),
                                "salary": fields.get("salary", "-"),
                                "stars": _score_to_stars(score),
                            })
                yield {
                    "step": 3,
                    "msg": f"📋 分析进度 {item.get('current', 0)}/{item.get('total', 0)}: {item.get('filename', '')}",
                    "status": "progress",
                    "filename": item.get("filename"),
                    "current": item.get("current"),
                    "total": item.get("total"),
                }

        tw.join(timeout=2.0)
    else:
        import time as _time

        ntot = len(pdf_paths)
        print(
            f"\n[Recruitment] 透析镜逐份模式：共 {ntot} 份 PDF，每份独立 Wasm+落盘（沙箱压力小、日志清晰）。"
            f" 若要坚持单次批量请设 HR_ANALYZER_BATCH_WASM=1\n",
            flush=True,
        )
        for idx, p in enumerate(pdf_paths, 1):
            yield {
                "step": 3,
                "msg": f"📋 开始分析 {idx}/{ntot}: {p.name}",
                "status": "progress",
                "filename": p.name,
                "current": idx,
                "total": ntot,
            }
            one = {**input_data, "_hr_files": str(p).replace("\\", "/")}
            inp_js = json.dumps({**one, "capability": "execute"}, ensure_ascii=False)

            def _run_one() -> str:
                from l3_node.primitives import run_tool
                return run_tool(HR_SKILL_ID, inp_js, allowed_skills=None, ndjson_queue=None) or ""

            t0 = _time.perf_counter()
            try:
                r = await asyncio.to_thread(_run_one)
            except Exception as e:
                err = f"⚠️ 第 {idx}/{ntot} 份透析失败: {p.name} — {e}"
                logger.exception("[Recruitment] %s", err)
                print(f"\n[Recruitment] {err}\n", flush=True)
                yield {"step": 3, "msg": err, "status": "error"}
                return
            dt = _time.perf_counter() - t0
            md_file = resolved_output / f"{p.stem}_analysis.md"
            md_path_str = str(md_file.resolve()) if md_file.is_file() else ""
            report = md_file.read_text(encoding="utf-8", errors="replace") if md_file.is_file() else ""
            if not report.strip() and r:
                logger.warning(
                    "[Recruitment] 第 %d/%d 份未找到 %s，run_tool 返回预览: %s",
                    idx,
                    ntot,
                    md_file.name,
                    (r or "")[:200],
                )
            _append_verdict_from_recruitment_report(report, p.name, str(p), md_path_str, passed_list, eliminated_list)
            sz = md_file.stat().st_size if md_file.is_file() else 0
            print(
                f"[Recruitment] 透析镜 ({idx}/{ntot}) 完成 {p.name} 耗时 {dt:.1f}s 落盘 {md_file.name} ({sz} bytes)",
                flush=True,
            )
            yield {
                "step": 3,
                "msg": f"✅ 已完成 {idx}/{ntot}: {p.name}（{dt:.0f}s）",
                "status": "progress",
                "filename": p.name,
                "current": idx,
                "total": ntot,
            }
        thread_result["result"] = f"sequential_ok n={ntot}"
    # 仅在有录用/淘汰记录时写排行榜并同步 Lark；否则视为「未找到可分析简历」并结束，避免空榜与误通知
    if thread_result.get("error"):
        err = thread_result["error"]
        _em = f"⚠️ HR 透析镜异常: {err}"
        logger.error("[Recruitment] %s job_folder=%s", _em, job_folder)
        print(f"\n[Recruitment] {_em}\n  job_folder={job_folder}\n", flush=True)
        yield {"step": 3, "msg": _em, "status": "error"}
    elif not passed_list and not eliminated_list:
        r = thread_result.get("result")
        detail = ""
        if isinstance(r, str) and r.strip():
            detail = f" {r.strip()[:280]}"
        _nm = (
            "⚠️ 未找到可分析的简历：透析镜未返回任何录用/淘汰结果。"
            "请确认 pending / processed / 副本 中有有效 PDF，且未被 skip。"
            "本流程已结束，未更新排行榜与 Lark。"
            f"{detail}"
        )
        logger.warning("[Recruitment] %s job_folder=%s", _nm, job_folder)
        print(f"\n[Recruitment] {_nm}\n  job_folder={job_folder}\n", flush=True)
        yield {"step": 3, "msg": _nm, "status": "error"}
    else:
        summary_dir = PLUGIN_DATA_ROOT / job_folder
        _write_summary_md(summary_dir, passed_list, eliminated_list, output_dir_use_absolute, job_folder=job_folder)
        summary_path = summary_dir / "排行榜_Summary.md"
        if summary_path.exists():
            try:
                from l3_node.channels.lark import sync_bitable_from_md
                replace_from_first = os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "true").lower() in ("1", "true", "yes")
                sync_result = sync_bitable_from_md(
                    md_path=str(summary_path), notify_group=True, replace_entire_table=replace_from_first
                )
                if sync_result.get("success"):
                    logger.info("[Recruitment] Lark 多维表已同步 job=%s count=%d", job_folder, sync_result.get("count", 0))
                elif sync_result.get("skipped"):
                    logger.info("[Recruitment] Lark 多维表同步已跳过（未配置应用凭证）")
                else:
                    logger.warning("[Recruitment] Lark 同步失败: %s", sync_result.get("error", ""))
            except Exception as e:
                logger.warning("[Recruitment] Lark 同步异常: %s", e)
        yield {
            "step": 3,
            "msg": "✅ 终极战报已全部生成！已更新排行榜并同步 Lark。",
            "status": "done",
            "total": len(passed_list) + len(eliminated_list),
        }
