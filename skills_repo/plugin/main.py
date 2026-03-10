#!/usr/bin/env python3
"""
HR 招聘插件 - 独立运行入口
完整流程：搜索简历 → 筛选（多 Agent 辩论）→ 输出通过名单 → 面试流程
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# 确保可导入 src
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.orchestrator import run_full_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="HR 招聘插件 - 搜索 → 筛选(多Agent) → 输出")
    sub = parser.add_subparsers(dest="cmd", help="命令")

    # run: 运行完整流程
    run_parser = sub.add_parser("run", help="运行完整招聘流程")
    run_parser.add_argument("--job", "-j", default="Python工程师", help="岗位名称")
    run_parser.add_argument("--job-desc", "-d", default="", help="岗位描述")
    run_parser.add_argument("--department", default="", help="部门")
    run_parser.add_argument(
        "--source", "-s",
        choices=["demo", "boss", "local"],
        default="demo",
        help="简历来源: demo(模拟), boss(Boss直聘), local(本地文件)",
    )
    run_parser.add_argument("--files", "-f", nargs="*", help="本地简历文件路径（source=local 时）")
    run_parser.add_argument("--max", "-n", type=int, default=5, help="最大简历数")
    run_parser.add_argument("--lark", action="store_true", help="同步到 Lark 面试流程")
    run_parser.add_argument("--lark-sheet", default="", help="Lark 多维表 sheet_token")
    run_parser.add_argument("--output", "-o", default="", help="结果输出文件（JSON）")

    # check: 检查 Boss Cookie
    check_parser = sub.add_parser("check", help="检查 Boss 直聘 Cookie 状态")
    check_parser.add_argument("--output", "-o", default="", help="输出文件")

    args = parser.parse_args()

    if args.cmd == "check":
        import asyncio
        from src.skills.retriever import check_cookie_status
        out = asyncio.run(check_cookie_status())
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if getattr(args, "output", ""):
            Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    if args.cmd == "run":
        import asyncio

        resume_files = getattr(args, "files", None) or []
        if args.source == "local" and not resume_files:
            logger.error("source=local 时请通过 --files 指定简历文件")
            sys.exit(1)

        logger.info("开始运行 HR 招聘流程: 搜索 → 筛选(多Agent) → 输出")
        out = asyncio.run(run_full_pipeline(
            job_title=args.job,
            job_desc=args.job_desc or "（未提供）",
            department=args.department,
            resume_source=args.source,
            resume_files=resume_files if args.source == "local" else None,
            max_resumes=args.max,
            sync_to_lark=args.lark,
            lark_sheet_token=args.lark_sheet,
        ))

        if not out.get("success"):
            logger.error("流程失败: %s", out.get("error", "未知错误"))
            sys.exit(1)

        summary = out.get("summary", {})
        logger.info("流程完成: 共 %d 份, 通过 %d 份, 淘汰 %d 份",
                    summary.get("total", 0),
                    summary.get("passed", 0),
                    summary.get("rejected", 0))

        # 打印通过名单
        passed = out.get("passed_briefs", [])
        if passed:
            print("\n===== 通过名单 =====")
            for i, p in enumerate(passed, 1):
                print(f"\n[{i}] verdict: {p.get('verdict')}")
                print(f"    brief: {p.get('brief', '')[:150]}...")
                if p.get("agent_a"):
                    print(f"    Agent A: {p['agent_a'][:80]}...")
                if p.get("agent_b"):
                    print(f"    Agent B: {p['agent_b'][:80]}...")

        result_str = json.dumps(out, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(result_str, encoding="utf-8")
            logger.info("结果已保存到 %s", args.output)
        else:
            print("\n===== 完整结果 (JSON) =====")
            print(result_str[:3000] + ("..." if len(result_str) > 3000 else ""))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
