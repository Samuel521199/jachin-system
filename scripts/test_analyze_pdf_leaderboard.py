#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单独测试：分析 PDF 简历 → 生成排行榜

仅执行 HR 透析镜 + 排行榜生成，不包含收网（无需 Chrome/Boss）。
适用于已有 pending 简历的职位目录。

用法示例（以「全栈工程师」为例）：
  python scripts/test_analyze_pdf_leaderboard.py
  python scripts/test_analyze_pdf_leaderboard.py --job 全栈工程师
  python scripts/test_analyze_pdf_leaderboard.py --job 全栈工程师 --force

前置条件：
  1. 项目根目录已有 .env（或 core/.env），配置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY
  2. skills_repo/plugin/data/{职位名}/pending/ 下已有 .pdf 简历
  3. skills_repo/plugin/data/{职位名}/jd.json 存在且含 jd_full、job_title

输出：
  - result/ 目录：每份简历一份 *_analysis.md
  - 排行榜_Summary.md：推荐面试区 + 淘汰区 Markdown 表格
"""
import argparse
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 加载 .env
try:
    from dotenv import load_dotenv
    for _p in (ROOT / ".env", ROOT / "skills_repo" / "plugin" / ".env", ROOT / "core" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
            break
except ImportError:
    pass

PLUGIN_DATA = ROOT / "skills_repo" / "plugin" / "data"
DEFAULT_JOB = "全栈工程师"


def _sanitize_job_folder(job_name: str, max_len: int = 60) -> str:
    """与 recruitment_task 一致的文件夹名转换"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


def main() -> int:
    p = argparse.ArgumentParser(description="单独测试：分析 PDF → 生成排行榜")
    p.add_argument("--job", default=DEFAULT_JOB, help=f"岗位名称，默认 {DEFAULT_JOB}")
    p.add_argument("--force", action="store_true", help="强制重新分析（覆盖已有 *_analysis.md）")
    args = p.parse_args()

    job_name = (args.job or DEFAULT_JOB).strip()
    job_folder = _sanitize_job_folder(job_name)
    pending_dir = PLUGIN_DATA / job_folder / "pending"
    output_dir = PLUGIN_DATA / job_folder / "result"
    jd_path = PLUGIN_DATA / job_folder / "jd.json"

    if not pending_dir.exists():
        print(f"❌ 目录不存在: {pending_dir}")
        print("   请确保 data/{职位名}/pending/ 下已有 PDF 简历。")
        return 1

    pdf_paths = [p.resolve() for p in pending_dir.rglob("*.pdf") if p.is_file()]
    if not pdf_paths:
        print(f"❌ 未找到 PDF: {pending_dir}")
        return 1

    if not jd_path.exists():
        print(f"❌ JD 配置不存在: {jd_path}")
        return 1

    print("=" * 60)
    print("分析 PDF + 生成排行榜 测试")
    print("=" * 60)
    print(f"岗位: {job_name}")
    print(f"简历数: {len(pdf_paths)}")
    for i, pp in enumerate(pdf_paths[:5], 1):
        print(f"  [{i}] {pp.name}")
    if len(pdf_paths) > 5:
        print(f"  ... 等共 {len(pdf_paths)} 份")
    print(f"JD: {jd_path}")
    print()

    # 注册 LLM（与 test_hr_analyzer 一致）
    try:
        from core.wasm_runner import register_host_services
        from l3_node.llm_client import LiteLLMEngine, SecurityContext

        ctx = SecurityContext()
        if os.environ.get("DASHSCOPE_API_KEY"):
            ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
        if os.environ.get("OPENAI_API_KEY"):
            ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
        from core.llm_provider import DASHSCOPE_REASONING_MODEL

        model = DASHSCOPE_REASONING_MODEL if ctx.get_key("dashscope") else "gpt-4o-mini"
        engine = LiteLLMEngine(security_context=ctx, model_name=model)
        register_host_services(llm_engine=engine, l2_base_url="http://localhost:18888")
        print("[OK] LLM 引擎已注册")
    except Exception as e:
        print(f"❌ LLM 注册失败: {e}")
        print("   请配置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    # force_reanalyze：清除已有 analysis，让 Wasm 重新分析
    if args.force:
        for old in output_dir.glob("*_analysis.md"):
            try:
                old.unlink()
                print(f"[清除] {old.name}")
            except Exception:
                pass

    job_config = {
        "job_name": job_name,
        "jd_config_path": str(jd_path.resolve()),
        "strictness": "standard",
    }

    try:
        from l3_node.recruitment_scheduler import _run_wasm_analysis_sync, _write_summary_md

        print("\n⏳ 正在唤醒 HR 透析镜...")
        passed_list, eliminated_list, _failed_items, _wasm_meta = _run_wasm_analysis_sync(
            job_config, pdf_paths, output_dir, job_folder
        )

        job_dir = PLUGIN_DATA / job_folder
        _write_summary_md(job_dir, passed_list, eliminated_list, job_folder)

        summary_path = job_dir / "排行榜_Summary.md"
        print("\n" + "=" * 60)
        print("✅ 完成")
        print("=" * 60)
        print(f"推荐面试区: {len(passed_list)} 人")
        print(f"淘汰区: {len(eliminated_list)} 人")
        print(f"分析报告: {output_dir}")
        print(f"排行榜: {summary_path}")
        if summary_path.exists():
            print("\n--- 排行榜预览 ---")
            print(summary_path.read_text(encoding="utf-8")[:800])
            if summary_path.stat().st_size > 800:
                print("\n...")
        return 0

    except Exception as e:
        print(f"\n❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
