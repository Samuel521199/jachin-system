#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock V8 测试舱 - 模拟 OmniSensoryBus 与 Pipeline
在脱离 jachin-system 主项目时，本地跑通全链路：
  输入 → Track B 配置 → Track A 原子工具 → Track C 虫群引擎 → 脱敏 Hook → 输出
"""
import asyncio
import io
import sys

# Windows 控制台 UTF-8
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass
import json
import sys
import uuid
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKSPACE = Path.home() / ".jachin" / "workspace"
HR_RULES = WORKSPACE / "hr_rules" / "java_engineer.md"


async def _load_hr_rules() -> str:
    """模拟 Track B：读取 HR 规则"""
    if HR_RULES.exists():
        return HR_RULES.read_text(encoding="utf-8")
    # 回退：使用模板
    template = ROOT / "1-config-template" / "hr_rules" / "java_engineer.md"
    if template.exists():
        return template.read_text(encoding="utf-8")
    return "（未配置筛选标准，使用默认规则）"


async def _desensitize(context: dict) -> None:
    """模拟 Hook：脱敏"""
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("hook", ROOT / "5-privacy-hook" / "hook_desensitize.py")
    if spec and spec.loader:
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)

        async def noop():
            pass

        await mod.before_llm_think_hook(context, noop)


def _run_swarm_engine(resume_text: str, hr_criteria: str) -> dict:
    """调用 Track C（Python 模式，非 Wasm）"""
    sys.path.insert(0, str(ROOT / "3-track-c-swarm-wasm" / "src"))
    try:
        from main import hr_swarm_engine
        return hr_swarm_engine(resume_text=resume_text, hr_criteria=hr_criteria)
    except Exception as e:
        return {"decision": "淘汰", "error": str(e)}


async def run_screening(
    resume_source: str = "local",
    resume_path: str = "",
    job_title: str = "",
) -> dict:
    """
    运行完整筛选流程
    resume_source: "local" | "boss"
    resume_path: 本地 PDF 路径（source=local 时）
    job_title: 岗位名（source=boss 时）
    """
    run_id = str(uuid.uuid4())[:8]
    session_id = str(uuid.uuid4())[:8]
    print(f"[RunID: {run_id}] [Session: {session_id}] 开始筛选...")

    # Step 1: 加载 HR 规则
    hr_criteria = await _load_hr_rules()
    print(f"[RunID: {run_id}] [OK] 已加载 HR 规则")

    # Step 2: 获取简历文本
    sys.path.insert(0, str(ROOT / "com.jachin.hr.recruitment"))
    resume_text = ""
    if resume_source == "local" and resume_path:
        try:
            import pdfplumber
            with pdfplumber.open(resume_path) as f:
                resume_text = "\n\n".join(p.extract_text() or "" for p in f.pages)
        except ImportError:
            try:
                from PyPDF2 import PdfReader
                r = PdfReader(resume_path)
                resume_text = "\n\n".join(p.extract_text() or "" for p in r.pages)
            except ImportError:
                return {"error": "未安装 pdfplumber 或 PyPDF2", "run_id": run_id}
        except Exception as e:
            return {"error": str(e), "run_id": run_id}
        if not resume_text:
            return {"error": "PDF 解析无内容", "run_id": run_id}
    elif resume_source == "boss" and job_title:
        return {"error": "Boss 雷达已移除，请使用 atom_greet_recommend_boss 或本地简历", "run_id": run_id}
    else:
        # 演示：使用模拟文本
        resume_text = """
张三 | 13800138000 | zhangsan@example.com
学历：本科 某大学 计算机 2018-2022
经验：Java 开发 3年，SpringCloud 微服务
技能：Java, Spring, Redis, MySQL
"""
    print(f"[RunID: {run_id}] [OK] 已获取简历 ({len(resume_text)} 字符)")

    # Step 3: 脱敏（模拟 before_llm_think）
    context = {"prompt": resume_text, "run_id": run_id}
    await _desensitize(context)
    resume_text_safe = context.get("prompt", resume_text)

    # Step 4: Track C 虫群引擎
    result = _run_swarm_engine(resume_text_safe, hr_criteria)
    result["run_id"] = run_id
    result["session_id"] = session_id
    print(f"[RunID: {run_id}] [OK] 虫群引擎完成: {result.get('decision', result.get('error'))}")
    return result


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["local", "boss", "demo"], default="demo")
    p.add_argument("--path", default="", help="本地 PDF 路径")
    p.add_argument("--job", default="Java工程师", help="岗位名（boss 时）")
    p.add_argument("--output", "-o", default="", help="结果输出文件")
    args = p.parse_args()

    if args.source == "local" and args.path:
        resume_source, resume_path, job_title = "local", args.path, ""
    elif args.source == "boss":
        resume_source, resume_path, job_title = "boss", "", args.job
    else:
        # demo: 优先 data 下的 PDF，否则用模拟文本
        resume_source, resume_path, job_title = "local", "", ""
        d = ROOT / "data"
        if d.exists():
            pdfs = list(d.glob("*.pdf"))
            if pdfs:
                resume_path = str(pdfs[0])
                resume_source = "local"

    out = asyncio.run(run_screening(resume_source, resume_path, job_title))
    s = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(s, encoding="utf-8")
        print(f"结果已保存: {args.output}")
    else:
        print("\n===== 结果 =====")
        print(s)


if __name__ == "__main__":
    main()
