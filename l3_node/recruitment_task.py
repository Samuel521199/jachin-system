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

from l3_node.paths import get_app_root
_PROJ_ROOT = get_app_root()
PLUGIN_DATA_ROOT = _PROJ_ROOT / "skills_repo" / "plugin" / "data"
HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"
HR_SKILL_CONFIG_ID = "hr-analyzer4"
DEFAULT_JD = "云边协同后端架构师：精通 Rust/Go，具备百万级设备接入、高可用分布式系统经验。"


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


def _sanitize_job_folder(job_name: str, max_len: int = 60) -> str:
    """将岗位名转为安全文件夹名（与 local_archiver 一致）"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


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
    job_folder = _sanitize_job_folder(job_name)
    save_dir = PLUGIN_DATA_ROOT / job_folder / "pending"
    save_dir.mkdir(parents=True, exist_ok=True)

    # 从 data/{职位}/jd.json 获取 jd_select 用于 Boss「全部职位」精确匹配（格式：岗位名称 _ 杭州 最低-最高K）
    job_text = job_name
    import sys
    try:
        jd_path = PLUGIN_DATA_ROOT / job_folder / "jd.json"
        if jd_path.exists():
            plugin_tools = _PROJ_ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
            if str(plugin_tools) not in sys.path:
                sys.path.insert(0, str(plugin_tools))
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
            import sys
            sys.path.insert(0, str(_PROJ_ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"))
            from tools.atom_inbox_harvester import atom_inbox_harvester_full_flow
            logger.info("[Recruitment] 收网参数 request_if_no_resume=%s filter_tab=%s", request_resume, filter_tab or "全部")
            harvester_result = atom_inbox_harvester_full_flow(
                job_text=job_text,
                max_items=max_count,
                save_dir=str(save_dir),
                filter_tab=filter_tab or "全部",
                request_if_no_resume=request_resume,
                job_folder=job_folder,
            )
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
        from l3_node.hr_analysis_persist import _resolve_safe_dir
        use_abs = Path(output_dir).expanduser().is_absolute()
        resolved_output = _resolve_safe_dir(str(output_dir).strip(), _PROJ_ROOT, use_absolute_path=use_abs)
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

    # 直接传入收网得到的 PDF 绝对路径，避免 target_dir 与 actual 子目录名不一致导致列举失败
    pdf_paths = harvester_result.get("pdf_paths") or []
    # 兜底：收网返回 downloaded>0 但 pdf_paths 为空时，或 force_reanalyze 时扫 pending 目录，确保透析镜有简历可分析
    if not pdf_paths and downloaded > 0:
        pending_pdfs = list(save_dir.rglob("*.pdf")) if save_dir.exists() else []
        pdf_paths = [p.resolve() for p in pending_pdfs if p.is_file()]
        if pdf_paths:
            logger.info("[Recruitment] pdf_paths 兜底：从 pending 目录补充 %d 份", len(pdf_paths))
    elif force_reanalyze:
        pending_pdfs = list(save_dir.rglob("*.pdf")) if save_dir.exists() else []
        if pending_pdfs:
            pdf_paths = [p.resolve() for p in pending_pdfs if p.is_file()]
            logger.info("[Recruitment] force_reanalyze：使用 pending 全部 %d 份简历", len(pdf_paths))
    paths_str = "|||".join(str(p).replace("\\", "/") for p in pdf_paths if p)
    if not paths_str:
        yield {"step": 3, "msg": "⚠️ 无法获取简历路径，透析镜无法启动。请检查 pending 目录或收网日志。", "status": "error"}
        return

    # 表单未传 JD 时优先用岗位名兜底，避免错误使用 skill_registry 中的云边协同
    jd_final = (jd_content or "").strip() or _job_name_fallback_jd(job_name) or _fetch_jd_from_db() or DEFAULT_JD
    if jd_content and jd_content.strip():
        logger.info("[Recruitment] 岗位 JD 已从表单传入 len=%d preview=%s", len(jd_content), (jd_content[:80] + "…") if len(jd_content) > 80 else jd_content)
        _save_jd_to_db(jd_content)
    else:
        if "云边" in jd_final or "云边协同" in jd_final:
            logger.warning("[Recruitment] 未从表单传入岗位 JD，当前兜底为云边协同。请务必在招聘大盘填写「岗位 JD」，否则分析报告将与岗位不符")
        else:
            logger.info("[Recruitment] 未从表单传入岗位 JD，使用岗位名兜底 len=%d", len(jd_final))
    print(f"\n[Recruitment] 实际传入 Wasm 的岗位 JD (len={len(jd_final)}):\n{jd_final}\n", flush=True)
    input_data: dict[str, Any] = {
        "target_dir": str(save_dir),
        "_hr_files": paths_str,
        "jd_template": jd_final,
        "strictness": strictness or "standard",
        "output_dir": str(resolved_output),
    }
    if focus_keywords and str(focus_keywords).strip():
        input_data["focus_keywords"] = str(focus_keywords).strip()
    if skip_files:
        input_data["skip_files"] = "|||".join(skip_files)

    ndjson_queue: queue.Queue[str] = queue.Queue()
    thread_result: dict[str, Any] = {"done": False, "error": None, "result": None}

    def _run_wasm() -> None:
        try:
            from l3_node.skills import run_tool
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

    passed_list: list[dict[str, Any]] = []
    eliminated_list: list[dict[str, Any]] = []
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
            yield {"step": 3, "msg": "✅ 终极战报已全部生成！", "status": "done", "total": item.get("total", 0)}
            break
        if status == "progress":
            report = item.get("report_content")
            fn = item.get("filename") or ""
            pdf_path = next((str(p) for p in pdf_paths if fn and (str(p).endswith(fn) or Path(p).name == fn)), None)
            md_path: str | None = None
            if report and isinstance(report, str):
                from l3_node.hr_analysis_persist import persist_hr_analysis_batch_item
                from l3_node.skills.loader import _extract_stem_from_hr_report
                stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                if not stem or re.match(r"^resume_\d+$", stem):
                    stem = _extract_stem_from_hr_report(report or "") or stem or "unknown"
                md_path = persist_hr_analysis_batch_item(
                    HR_SKILL_ID,
                    report,
                    stem,
                    config={"output_dir": str(resolved_output), "output_dir_use_absolute": output_dir_use_absolute},
                )
                # 双通道正则拦截：录用 / 淘汰
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
    # 流式处理结束后，生成终极排行榜到 data/{职位}/排行榜_Summary.md，并同步到 Lark 多维表
    if passed_list or eliminated_list:
        summary_dir = PLUGIN_DATA_ROOT / job_folder
        _write_summary_md(summary_dir, passed_list, eliminated_list, output_dir_use_absolute, job_folder=job_folder)
        # 同步到 Lark 多维表（从第一行开始写入，覆盖式更新，完成后通知 HR）
        summary_path = summary_dir / "排行榜_Summary.md"
        if summary_path.exists():
            try:
                plugin_tools = _PROJ_ROOT / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
                if str(plugin_tools) not in sys.path:
                    sys.path.insert(0, str(plugin_tools))
                from tools.atom_lark_bitable_sync import atom_lark_bitable_sync
                # 默认从第一行开始写入；一表多职位时可设 LARK_REPLACE_ENTIRE_TABLE=false 保留其他职位
                replace_from_first = os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "true").lower() in ("1", "true", "yes")
                sync_result = atom_lark_bitable_sync(md_path=str(summary_path), notify_group=True, replace_entire_table=replace_from_first)
                if sync_result.get("success"):
                    logger.info("[Recruitment] Lark 多维表已同步 job=%s count=%d", job_folder, sync_result.get("count", 0))
                else:
                    logger.warning("[Recruitment] Lark 同步失败: %s", sync_result.get("error", ""))
            except Exception as e:
                logger.warning("[Recruitment] Lark 同步异常: %s", e)
    if thread_result.get("error"):
        yield {"step": 3, "msg": f"⚠️ HR 透析镜异常: {thread_result['error']}", "status": "error"}
    elif not passed_list and not eliminated_list:
        # 透析镜未产出任何分析时，展示技能返回内容（如「无法列举简历目录」「LLM 未注册」等）
        r = thread_result.get("result")
        if isinstance(r, str) and r.strip() and ("⚠️" in r or "失败" in r or "无法" in r or "未" in r):
            yield {"step": 3, "msg": f"⚠️ HR 透析镜: {r.strip()[:300]}", "status": "error"}
