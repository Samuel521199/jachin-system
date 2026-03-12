#!/usr/bin/env python3
"""
cron_thinker 调度入口 - 日常巡逻 + 双触发终局审判

由 Layer 2 的 cron_thinker 生物钟驱动，或通过系统 cron/Task Scheduler 定时执行。
建议周期：每 30 分钟执行一次日常巡逻；终局审判由双触发引擎控制。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

# 加载 .env（供 LARK_APP_ID 等）
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

WORKSPACE = Path.home() / ".jachin" / "workspace"
# 待审 PDF 与已审 PDF：按职位存于 data/{职位}/pending、data/{职位}/processed
DATA_ROOT = ROOT / "data"
STATUS_FILE = WORKSPACE / "recruitment_status.json"


def _load_status() -> dict:
    default = {
        "job_title": "Java开发",
        "status": "hunting",
        "batch_limit": 50,
        "cron_trigger_time": "08:30",
        "unprocessed_pdfs": 0,
        "total_processed": 0,
        "hr_criteria": "",
        "scanned_online_count": 0,
        "greeted_count": 0,
        "last_milestone_notified": 0,
        "last_progress_notify_time": "",
    }
    if not STATUS_FILE.exists():
        return default
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return {**default, **data}
    except Exception:
        return default


def _save_status(s: dict) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_pending() -> int:
    if not DATA_ROOT.exists():
        return 0
    return sum(len(list((DATA_ROOT / d / "pending").rglob("*.pdf"))) for d in DATA_ROOT.iterdir() if (DATA_ROOT / d / "pending").is_dir())


def _should_trigger_final_judgment(status: dict) -> bool:
    """双触发引擎：满载溢出 或 每日早报时间"""
    unprocessed = status.get("unprocessed_pdfs", 0)
    batch_limit = status.get("batch_limit", 50)
    if unprocessed >= batch_limit:
        return True
    # 时间触发需与系统时间比对，此处简化：由 --force-judge 或外部传入
    return False


def _job_slug(job_title: str) -> str:
    import re
    s = re.sub(r"[^\w\u4e00-\u9fff]", "_", (job_title or ""))
    return s[:50] if s else "default"


def _maybe_send_milestone_notify(status: dict) -> None:
    """里程碑式主动推送（atom_lark_notifier 已移除，此函数保留为空实现）"""
    pass


async def run_daily_patrol(job_title: str) -> dict:
    """意图一：日常巡逻 - 收网归档（遍历沟通页下载已发简历 PDF）"""
    from tools.boss_harvest_orchestrator import harvest_resume_full_flow
    from tools.recruitment_status import refresh_unprocessed_count, update_status, load_status

    status = _load_status()
    job_title = job_title or status.get("job_title", "Java开发")

    result = {"harvested": 0, "archived": 0, "error": ""}

    harvest_out = harvest_resume_full_flow(job_text=job_title, max_items=50)
    if not harvest_out.get("success"):
        result["error"] = result.get("error") or harvest_out.get("error", "")
        return result

    pdfs = harvest_out.get("pdf_paths", harvest_out.get("downloaded_pdfs", [])) or []
    n = len(pdfs)
    if pdfs and isinstance(pdfs[0], str):
        result["archived"] = n
        result["harvested"] = n
    elif pdfs:
        result["archived"] = len([p for p in pdfs if isinstance(p, dict) and p.get("local_path")])
        result["harvested"] = n

    refresh_unprocessed_count()
    update_status(job_title=job_title)

    # 6. 里程碑推送
    new_status = _load_status()
    _maybe_send_milestone_notify(new_status)

    return result


async def run_final_judgment(lark_sheet: str = "") -> dict:
    """意图二：终局审判 - data/{职位}/pending PDFs -> Wasm 虫群 -> data/{职位}/processed"""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    pdfs = []
    for d in DATA_ROOT.iterdir():
        if d.is_dir():
            pend = d / "pending"
            if pend.is_dir():
                pdfs.extend(pend.rglob("*.pdf"))
    if not pdfs:
        return {"success": True, "processed": 0, "passed": 0, "message": "无待审 PDF"}

    sys.path.insert(0, str(ROOT / "3-track-c-swarm-wasm" / "src"))
    status = _load_status()
    job_title = status.get("job_title", "Java开发")
    hr_criteria = status.get("hr_criteria")
    if not hr_criteria:
        slug = _job_slug(job_title)
        for name in [f"{slug}.md", "java_engineer.md"]:
            hr_rules = WORKSPACE / "hr_rules" / name
            if hr_rules.exists():
                hr_criteria = hr_rules.read_text(encoding="utf-8")
                break
    if not hr_criteria:
        hr_criteria = "学历本科，经验3年"

    def _extract_pdf_text(path) -> str:
        try:
            from core.pdf_extractor import extract_pdf_text
            return extract_pdf_text(path) or ""
        except ImportError:
            try:
                import pdfplumber
                with pdfplumber.open(path) as f:
                    return "\n\n".join(p.extract_text() or "" for p in f.pages)
            except ImportError:
                try:
                    from PyPDF2 import PdfReader
                    r = PdfReader(path)
                    return "\n\n".join(p.extract_text() or "" for p in r.pages)
                except ImportError:
                    return ""
            except Exception:
                return ""
        except Exception:
            return ""

    results = []
    for pdf in pdfs:
        text = _extract_pdf_text(str(pdf))
        if not text:
            continue
        # 脱敏
        import re
        text = re.sub(r"1[3-9]\d{9}", "[HIDDEN_PHONE]", text)
        text = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[HIDDEN_EMAIL]", text)
        # 虫群评审
        from main import hr_swarm_engine
        r = hr_swarm_engine(resume_text=text, hr_criteria=hr_criteria)
        r["pdf_name"] = pdf.name
        results.append(r)
        job_folder = pdf.parent.parent.name if "pending" in pdf.parts else "未分类"
        processed_dir = DATA_ROOT / job_folder / "processed"
        processed_dir.mkdir(parents=True, exist_ok=True)
        pdf.rename(processed_dir / pdf.name)

    passed = [x for x in results if x.get("decision") == "建议面试"]
    if lark_sheet and passed:
        try:
            from src.skills.lark_hr import sync_interview_track
            await sync_interview_track(sheet_token=lark_sheet, passed_candidates=passed)
        except Exception:
            pass

    status = _load_status()
    status["unprocessed_pdfs"] = 0
    status["total_processed"] = status.get("total_processed", 0) + len(results)
    _save_status(status)

    return {
        "success": True,
        "processed": len(results),
        "passed": len(passed),
        "results": results,
    }


def main():
    ap = argparse.ArgumentParser(description="HR 招聘 cron_thinker 调度")
    ap.add_argument("--patrol", action="store_true", help="执行日常巡逻（雷达+收网）")
    ap.add_argument("--judge", action="store_true", help="执行终局审判（Wasm 评审）")
    ap.add_argument("--force-judge", action="store_true", help="强制终局审判（无视触发条件）")
    ap.add_argument("--job", default="Java开发", help="岗位名称")
    ap.add_argument("--lark-sheet", default="", help="Lark 多维表 sheet_token")
    args = ap.parse_args()

    if args.patrol:
        r = asyncio.run(run_daily_patrol(args.job))
        print(json.dumps(r, ensure_ascii=False, indent=2))

    if args.judge or args.force_judge:
        if not args.force_judge:
            status = _load_status()
            if not _should_trigger_final_judgment(status):
                print(json.dumps({"skipped": True, "reason": "未达触发条件"}, ensure_ascii=False))
                return
        r = asyncio.run(run_final_judgment(args.lark_sheet))
        print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
