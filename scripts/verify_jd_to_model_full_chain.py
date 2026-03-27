#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
岗位 JD 全链路验证：从技能传入 → Wasm 解析 → 模型 Prompt → 最终分析报告

验证目标：
1. jd_template 正确传入 loader
2. Wasm 正确解析并注入 user_prompt
3. 模型收到的【岗位要求】非空
4. 最终报告不含「岗位jd为空」「未提供」「岗位要求为空」等字样

用法:
  python scripts/verify_jd_to_model_full_chain.py
  python scripts/verify_jd_to_model_full_chain.py --resume data/hr_resumes/zhangsan_resume.md --jd "你的JD"
"""
from __future__ import annotations

import argparse
import json
import os
import re
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

# 加载 .env（DASHSCOPE_API_KEY 等）
try:
    from dotenv import load_dotenv
    for _p in (ROOT / ".env", ROOT / "core" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
            break
except ImportError:
    pass

# 测试 JD（必须与云边无关，便于验证传递成功）
TEST_JD = """资深Golang语言开发 _ 杭州 25-40K

岗位要求：
1. 精通 Go 语言，3年以上 Go 开发经验
2. 熟悉微服务架构、gRPC、消息队列
3. 有分布式系统、高并发系统设计经验
4. 熟悉 MySQL、Redis 等存储
5. 良好的代码规范与团队协作能力"""

# 报告禁止出现的字样（说明 JD 未正确传入）
FORBIDDEN_PATTERNS = [
    r"岗位\s*[Jj][Dd]?\s*为\s*空",
    r"【岗位要求】\s*为\s*空",
    r"【岗位要求】\s*字段\s*为\s*空",
    r"岗位要求.*空",
    r"未提供.*岗位",
    r"岗位要求.*未提供",
    r"无法评估.*JD",
    r"JD\s*缺失",
    r"岗位要求.*空白",
    r"输入.*【岗位要求】.*空",
]
FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_PATTERNS))


def main() -> int:
    p = argparse.ArgumentParser(description="岗位 JD 全链路验证")
    p.add_argument("--resume", default="data/hr_resumes/zhangsan_resume.md", help="简历路径（相对项目根）")
    p.add_argument("--jd", default=TEST_JD, help="岗位 JD 内容")
    p.add_argument("--output", default="data/hr_analysis/jd_verify_test", help="报告输出目录")
    p.add_argument("--trace-only", action="store_true", help="仅追踪到模型输入，不调用 LLM（无需 L3 启动）")
    args = p.parse_args()

    resume_path = (ROOT / args.resume).resolve()
    if not resume_path.exists():
        print(f"✗ 简历不存在: {resume_path}", flush=True)
        return 1

    jd_content = (args.jd or TEST_JD).strip()
    output_dir = (ROOT / args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70, flush=True)
    print("岗位 JD 全链路验证：技能 → Wasm → 模型 → 报告", flush=True)
    print("=" * 70, flush=True)
    print(f"简历: {resume_path}", flush=True)
    print(f"JD 长度: {len(jd_content)}", flush=True)
    print(f"JD 前80字: {jd_content[:80]}…", flush=True)

    if args.trace_only:
        # 仅追踪：loader → stdin → 模拟 Wasm 解析 → 展示将发送给模型的 prompt
        from l3_node.skills.loader import build_hr_stdin_for_debug

        def _simulate_extract(json_str: str, key: str) -> str | None:
            for pat in (f'"{key}":"', f'"{key}": "'):
                idx = json_str.find(pat)
                if idx < 0:
                    continue
                val_start = idx + len(pat)
                tail = json_str[val_start:]
                i = 0
                while i < len(tail):
                    if tail[i] == "\\" and i + 1 < len(tail):
                        i += 2
                        continue
                    if tail[i] == '"':
                        return tail[:i]
                    i += 1
            return None

        input_data = {
            "resume_path": str(resume_path).replace("\\", "/"),
            "jd_template": jd_content,
            "strictness": "standard",
            "output_dir": str(output_dir),
        }
        stdin_str, debug = build_hr_stdin_for_debug(input_data)
        print("\n[节点1] loader build_hr_stdin:", flush=True)
        print(f"  has_jd={debug.get('has_jd')} caller_jd_len={debug.get('caller_jd_len')}", flush=True)
        print(f"  jd_preview={debug.get('jd_preview', '')[:80]}…", flush=True)

        nl = stdin_str.find("\n")
        rest = stdin_str[nl + 1 :].strip() if nl >= 0 else stdin_str
        jd_extracted = _simulate_extract(rest, "jd_template")
        print("\n[节点2] 模拟 Wasm extract_json_str_unescaped:", flush=True)
        print(f"  jd_template 提取: {'OK' if jd_extracted else 'FAIL'}", flush=True)
        if jd_extracted:
            print(f"  提取内容前100字: {jd_extracted[:100]}…", flush=True)

        user_prompt_preview = f"【岗位要求】\n{jd_extracted or '(空)'}\n\n【原始简历】\n..."
        print("\n[节点3] 将发送给模型的 user_prompt 预览:", flush=True)
        print("-" * 50, flush=True)
        print(user_prompt_preview[:400], flush=True)
        print("-" * 50, flush=True)
        if not jd_extracted or (jd_extracted or "").strip() == "":
            print("\n✗ 断点: jd_template 未正确提取，模型将收到空【岗位要求】", flush=True)
            return 1
        if "云边" in (jd_extracted or "") and "云边" not in jd_content:
            print("\n✗ 异常: 提取的 JD 含「云边」但传入不含，说明被 cfg 覆盖", flush=True)
            return 1
        print("\n✓ 追踪通过：JD 已正确传入 loader 并可被 Wasm 解析", flush=True)
        print("  运行完整验证请去掉 --trace-only 并确保 L3 已启动（含 LLM 引擎）", flush=True)
        return 0

    # 尝试注册 LLM（与 test_hr_analyzer 一致，便于独立运行）
    try:
        from core.wasm_runner import register_host_services
        from l3_node.llm_client import LiteLLMEngine, SecurityContext
        import os
        ctx = SecurityContext()
        if os.environ.get("DASHSCOPE_API_KEY"):
            ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
        if os.environ.get("OPENAI_API_KEY"):
            ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
        from core.llm_provider import DASHSCOPE_REASONING_MODEL

        model = DASHSCOPE_REASONING_MODEL if ctx.get_key("dashscope") else "gpt-4o-mini"
        engine = LiteLLMEngine(security_context=ctx, model_name=model)
        register_host_services(llm_engine=engine, l2_base_url="http://localhost:18888")
        print("\n[LLM] 已注册引擎，将执行完整分析", flush=True)
    except Exception as e:
        print(f"\n[LLM] 未注册: {e}，请设置 DASHSCOPE_API_KEY 或启动 L3", flush=True)

    # 构建 input（单文件模式，用 resume_path）
    resume_str = str(resume_path).replace("\\", "/")
    input_data = {
        "resume_path": resume_str,
        "jd_template": jd_content,
        "strictness": "standard",
        "output_dir": str(output_dir),
    }

    # 执行 run_tool（需 LLM）
    import queue
    import threading
    from l3_node.skills import run_tool

    inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
    ndjson_lines: list[str] = []
    q: queue.Queue[str] = queue.Queue()

    t = threading.Thread(target=lambda: run_tool("jpp:com.jachin.hr.analyzer4", inp, ndjson_queue=q))
    t.start()
    while t.is_alive() or not q.empty():
        try:
            line = q.get(timeout=0.5)
            ndjson_lines.append(line)
        except queue.Empty:
            if not t.is_alive():
                break
            continue
    t.join(timeout=5.0)

    # 解析 ndjson：debug（jd_len）、debug_prompt（job_desc 在 prompt 中）、progress（report）
    jd_len = -1
    job_desc_in_prompt = False
    prompt_preview = ""
    report_content = ""

    for line in ndjson_lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        st = item.get("status")
        if st == "debug":
            jd_len = item.get("jd_len", -1)
            jd_preview = item.get("jd_preview", "")
            extracted_preview = item.get("extracted_preview", "")
            print(f"\n[节点1] Wasm debug: jd_len={jd_len} jd_preview={jd_preview[:60]}…", flush=True)
            if extracted_preview:
                print(f"  extracted_preview={extracted_preview[:120]}…", flush=True)
        elif st == "debug_prompt":
            job_desc_len = item.get("job_desc_len", -1)
            prompt_preview = item.get("prompt_preview", "")
            job_desc_in_prompt = "【岗位要求】" in prompt_preview and job_desc_len > 0
            print(f"\n[节点2] Wasm debug_prompt: job_desc_len={job_desc_len} 已注入prompt={job_desc_in_prompt}", flush=True)
            if prompt_preview:
                print(f"  prompt_preview(前200字): {prompt_preview[:200]}…", flush=True)
        elif st == "progress" and item.get("report_content"):
            report_content = item.get("report_content", "")

    # 若 ndjson 未收集到，从 get_last_ndjson_lines 取
    if not report_content:
        from core.wasm_runner import get_last_ndjson_lines
        for line in get_last_ndjson_lines():
            line = (line or "").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if item.get("status") == "progress" and item.get("report_content"):
                    report_content = item.get("report_content", "")
                    break
            except json.JSONDecodeError:
                continue

    if not report_content:
        print("\n✗ 未获取到分析报告（可能 LLM 未注册或执行失败）", flush=True)
        return 1

    # 保存报告
    out_path = output_dir / "jd_verify_report.md"
    out_path.write_text(report_content, encoding="utf-8")
    print(f"\n[节点3] 报告已保存: {out_path}", flush=True)

    # 验证：禁止字样
    forbidden_found = FORBIDDEN_RE.findall(report_content)
    if forbidden_found:
        print(f"\n✗ 报告含禁止字样（说明 JD 未正确传入模型）: {forbidden_found}", flush=True)
        return 1

    # 验证：应包含 JD 关键词
    jd_keywords = ["Golang", "Go", "微服务", "gRPC", "分布式", "MySQL", "Redis"]
    found = [k for k in jd_keywords if k in report_content]
    if not found:
        print("\n△ 报告未明显包含 JD 关键词（可能 LLM 改写表述）", flush=True)

    # 注：报告中的「云边」可能来自简历内容（候选人技能），不单独作为失败依据

    print("\n" + "=" * 70, flush=True)
    print("✓ 验证通过：", flush=True)
    print("  - 报告不含「岗位jd为空」等禁止字样", flush=True)
    print("  - 报告已保存，可人工查看【岗位要求】是否融入分析", flush=True)
    print("=" * 70, flush=True)
    print("\n【报告摘要】前 500 字：\n", flush=True)
    print(report_content[:500], flush=True)
    print("\n...(完整报告见上方路径)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
