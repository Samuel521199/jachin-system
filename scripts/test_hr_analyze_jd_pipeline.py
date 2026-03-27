#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 HR 透析链路：pending 简历 + 同目录 jd.json → result/*_analysis.md，并校验 JD 传入与同名覆盖。

默认职位根（可改）：
  Windows: C:\\Users\\Legion\\.jachin\\workspace\\hr_recruitment\\Python 工程师

用法：
  python scripts/test_hr_analyze_jd_pipeline.py
  python scripts/test_hr_analyze_jd_pipeline.py --job-root "D:\\path\\to\\Python 工程师"
  python scripts/test_hr_analyze_jd_pipeline.py --dry-run
  python scripts/test_hr_analyze_jd_pipeline.py --runs 2

依赖：项目根 .env / core/.env 中 DASHSCOPE_API_KEY 或 OPENAI_API_KEY；JACHIN_DEV_HR_FIRST=1 推荐。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    for _p in (ROOT / ".env", ROOT / "skills_repo" / "plugin" / ".env", ROOT / "core" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
            break
except ImportError:
    pass

_DEFAULT_JOB_ROOT = Path.home() / ".jachin" / "workspace" / "hr_recruitment" / "Python 工程师"


def _load_jd_bundle(jd_path: Path) -> tuple[str, str, str]:
    """
    返回 (jd_template, job_title, target_role)。
    jd_template 优先 jd_full；否则由 job_title 等拼出最小正文，避免空串传入 Wasm。
    """
    raw = jd_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("jd.json 根须为对象")

    job_title = str(data.get("job_title") or data.get("title") or "").strip()
    jd_full = str(data.get("jd_full") or "").strip()
    extra_bits = []
    for k in ("education", "experience", "salary", "location"):
        v = data.get(k)
        if v:
            extra_bits.append(f"{k}: {v}")
    if not jd_full:
        jd_full = (f"岗位：{job_title}\n\n" if job_title else "") + "\n".join(extra_bits)
    if not jd_full.strip():
        raise ValueError("jd.json 中 jd_full 为空且无法从 job_title 等字段拼出 JD，请补全 jd_full")

    target_role = str(data.get("analyzer_target_role") or data.get("target_role") or "").strip()
    if not target_role:
        t = job_title.lower()
        if "python" in t or "python" in jd_full.lower():
            target_role = "python_engineer"
        elif "前端" in job_title or "frontend" in t:
            target_role = "frontend_engineer"
        else:
            target_role = "backend_engineer"

    return jd_full, job_title, target_role


def _register_llm() -> None:
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
    register_host_services(llm_engine=engine, l2_base_url=os.environ.get("L2_BASE_URL", "http://localhost:18888"))


def _import_hr_analyze():
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root

    plug = _get_hr_recruitment_plugin_root()
    if not plug:
        dev = ROOT / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
        if dev.is_dir() and (dev / "tools" / "hr_analyze_resume.py").exists():
            plug = dev
    if not plug:
        raise RuntimeError("找不到 HR 插件（com.jachin.hr.recruitment），请设置 JACHIN_DEV_HR_FIRST=1 或 JACHIN_HR_RECRUITMENT_ROOT")
    p = str(plug.resolve())
    if p not in sys.path:
        sys.path.insert(0, p)
    from tools.hr_analyze_resume import hr_analyze_resume

    return hr_analyze_resume


def main() -> int:
    ap = argparse.ArgumentParser(description="测试 pending+jd.json→result 透析链路与 JD 传入")
    ap.add_argument(
        "--job-root",
        type=Path,
        default=_DEFAULT_JOB_ROOT,
        help="职位根目录（含 pending、result、jd.json）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只校验路径与 JD，不调用模型")
    ap.add_argument("--runs", type=int, default=1, help="连续执行轮数；2 用于验证覆盖写入")
    ap.add_argument("--max-files", type=int, default=50, help="传入分析的最大简历数（与 collect 一致上限）")
    ap.add_argument(
        "--expect-substr",
        default="",
        help="可选：分析报告正文中应出现的子串（默认用 job_title 或 Python）",
    )
    ap.add_argument(
        "--forbid-substr",
        default="",
        help="可选：若报告正文中出现该子串则判失败（用于排查误用其它岗位 JD，如 云边架构师）",
    )
    args = ap.parse_args()

    job_root: Path = args.job_root.expanduser().resolve()
    pending = job_root / "pending"
    result_dir = job_root / "result"
    jd_path = job_root / "jd.json"

    print("=" * 60)
    print("HR 透析 JD 链路测试")
    print("=" * 60)
    print(f"职位根: {job_root}")
    print(f"pending: {pending}")
    print(f"result:  {result_dir}")
    print(f"jd.json: {jd_path}")
    print()

    if not job_root.is_dir():
        print(f"❌ 职位根目录不存在: {job_root}")
        return 1
    if not pending.is_dir():
        print(f"❌ pending 不存在: {pending}")
        return 1
    if not jd_path.is_file():
        print(f"❌ jd.json 不存在: {jd_path}")
        return 1

    try:
        jd_template, job_title, target_role = _load_jd_bundle(jd_path)
    except Exception as e:
        print(f"❌ 读取 jd.json 失败: {e}")
        return 1

    print("[JD 预览] job_title=%r target_role=%r" % (job_title, target_role))
    print("[JD 预览] jd_template 长度=%d 字" % len(jd_template))
    print(jd_template[:400].replace("\r", "") + ("…" if len(jd_template) > 400 else ""))
    print()

    pdfs = sorted(p for p in pending.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    others = sorted(
        p for p in pending.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".docx", ".txt"}
    )
    files = (pdfs + others)[: max(1, args.max_files)]
    if not files:
        print("❌ pending 下未找到 .pdf/.md/.docx/.txt")
        return 1
    print("待分析文件 (%d):" % len(files))
    for p in files[:8]:
        print("  -", p.name)
    if len(files) > 8:
        print("  ...")
    print()

    expect = (args.expect_substr or "").strip() or (job_title if job_title else "Python")
    if args.dry_run:
        print("✅ --dry-run：路径与 JD 校验通过，未调用 Wasm。")
        print("   期望报告含片段（供正式跑时 assert）:", repr(expect[:80]))
        return 0

    try:
        _register_llm()
        print("[OK] LLM 已注册")
    except Exception as e:
        print(f"❌ LLM 注册失败: {e}")
        return 1

    hr_analyze_resume = _import_hr_analyze()
    result_dir.mkdir(parents=True, exist_ok=True)

    pending_str = str(pending.resolve())
    out_str = str(result_dir.resolve())

    mtime_after_run1: dict[str, float] = {}

    for run_idx in range(1, args.runs + 1):
        print("-" * 60)
        print("第 %d/%d 轮分析 …" % (run_idx, args.runs))
        t0 = time.time()
        obs = hr_analyze_resume(
            target_dir=pending_str,
            jd_template=jd_template,
            target_role=target_role,
            strictness="standard",
            output_dir=out_str,
        )
        elapsed = time.time() - t0
        print("耗时 %.1fs，返回长度 %d" % (elapsed, len(obs or "")))
        if obs and (obs.strip().startswith("错误") or obs.strip().startswith("[Wasm")):
            print("❌ 分析返回异常预览:\n", (obs or "")[:1200])
            return 1

        analysis_mds = sorted(result_dir.glob("*_analysis.md"))
        if not analysis_mds:
            print("❌ result 下未生成 *_analysis.md")
            print("Observation 预览:\n", (obs or "")[:2000])
            return 1
        print("已生成 %d 个分析报告:" % len(analysis_mds))
        for m in analysis_mds[:10]:
            print(" ", m.name, m.stat().st_size, "bytes")
        # 内容校验：至少一份报告应体现当前 JD/岗位关键词（避免误用云边等无关模板）
        joined = ""
        for m in analysis_mds[:20]:
            try:
                joined += m.read_text(encoding="utf-8", errors="replace")[:8000]
            except OSError:
                pass
        if expect and expect not in joined:
            print("⚠️ 警告：未在已写入的 analysis md 中找到期望片段 %r（可能是模型措辞不同，请人工打开 result 检查）" % (expect[:60]))
        else:
            print("✅ 报告内容已包含期望片段:", repr(expect[:60]))
        fb = (args.forbid_substr or "").strip()
        if fb and fb in joined:
            print("❌ 报告中出现了禁用片段 %r（疑似未使用本岗位 JD 或模型跑偏）" % (fb[:80]))
            return 1

        if run_idx == 1 and args.runs >= 2:
            mtime_after_run1 = {}
            for m in result_dir.glob("*_analysis.md"):
                try:
                    mtime_after_run1[m.name] = m.stat().st_mtime
                except OSError:
                    pass
            print("[覆盖测试] 第 1 轮结束，已记录 %d 个 *_analysis.md 的 mtime" % len(mtime_after_run1))

        if run_idx < args.runs:
            time.sleep(1.1)

    # 覆盖：第 2 轮应对第 1 轮同名文件重写（mtime 变大）
    if args.runs >= 2 and mtime_after_run1:
        print("-" * 60)
        print("覆盖检查：第 2 轮应对第 1 轮的 {stem}_analysis.md 覆盖写入。")
        updated = 0
        for name, t1 in mtime_after_run1.items():
            p = result_dir / name
            try:
                if p.exists() and p.stat().st_mtime > t1 + 0.5:
                    updated += 1
                    print("  已更新:", name)
            except OSError:
                pass
        if updated == 0:
            print("⚠️ 警告：第二轮未检测到 mtime 变化（可能 Wasm 未重新落盘或文件名不一致）")

    print("=" * 60)
    print("✅ 测试完成：JD 已从 jd.json 加载并传入 hr_analyze_resume；输出目录:", result_dir)
    print("说明：若报告仍像「别的岗位」，请检查 jd_full 是否完整、是否与 Boss 选岗一致。")
    print("文档: docs/HR_ANALYZE_PIPELINE_TEST.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
