#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
岗位 JD 全流程测试：从传入到分析报告验证

用法:
  python scripts/test_jd_full_flow.py --resume "D:/path/to/resume.pdf" --jd "资深Golang语言开发：精通Go..."
  python scripts/test_jd_full_flow.py --resume "D:/path/to/resume.pdf" --jd "资深Golang语言开发：精通Go..." --trace

追踪节点：
  1. 输入 jd_template
  2. loader 构建 stdin
  3. Wasm 解析层 extract
  4. LLM 收到的 job_desc
  5. 最终报告中的【岗位要求】/岗位关键词
"""
from __future__ import annotations

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

# 测试用 JD（资深Golang）
TEST_JD = """资深Golang语言开发 _ 杭州 25-40K

岗位要求：
1. 精通 Go 语言，3年以上 Go 开发经验
2. 熟悉微服务架构、gRPC、消息队列
3. 有分布式系统、高并发系统设计经验
4. 熟悉 MySQL、Redis 等存储
5. 良好的代码规范与团队协作能力"""


def _simulate_rust_extract(json_str: str, key: str) -> str | None:
    """模拟 hr-analyzer4 extract_json_str_unescaped"""
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


def main() -> int:
    p = argparse.ArgumentParser(description="岗位 JD 全流程测试")
    p.add_argument("--resume", required=True, help="简历 PDF 绝对路径")
    p.add_argument("--jd", default=TEST_JD, help="岗位 JD 内容")
    p.add_argument("--trace", action="store_true", help="打印各节点追踪")
    p.add_argument("--output", default="", help="分析报告输出路径（默认 data/hr_analysis/jd_test）")
    args = p.parse_args()

    resume_path = Path(args.resume).resolve()
    if not resume_path.exists():
        print(f"✗ 简历文件不存在: {resume_path}", flush=True)
        return 1

    jd_content = (args.jd or TEST_JD).strip()
    if "云边" in jd_content or "云边协同" in jd_content:
        print("⚠️ 警告: 传入的 JD 含「云边」，将验证是否被正确使用", flush=True)

    print("=" * 70, flush=True)
    print("岗位 JD 全流程测试", flush=True)
    print("=" * 70, flush=True)
    print(f"简历: {resume_path}", flush=True)
    print(f"JD 长度: {len(jd_content)}", flush=True)
    print(f"JD 预览: {jd_content[:100]}…" if len(jd_content) > 100 else f"JD: {jd_content}", flush=True)

    # [1] 构建 input_data（与 recruitment_task 一致）
    # 单文件时 _hr_files 用 "path|||" 触发 Rust 的 ||| 解析，否则会走 target_dir 列举
    output_dir = args.output or str(ROOT / "data" / "hr_analysis" / "jd_test")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    resume_str = str(resume_path).replace("\\", "/")
    input_data = {
        "target_dir": "jd_test",
        "_hr_files": resume_str + "|||" if "|||" not in resume_str else resume_str,
        "jd_template": jd_content,
        "strictness": "standard",
        "output_dir": output_dir,
    }

    if args.trace:
        print("\n[节点1] input_data jd_template len:", len(input_data.get("jd_template", "")), flush=True)
        print("[节点1] jd_template 前80字:", (input_data.get("jd_template", "") or "")[:80], flush=True)

    # [2] loader 构建 stdin
    from l3_node.primitives.tools.loader import build_hr_stdin_for_debug

    stdin_str, debug = build_hr_stdin_for_debug(input_data)
    if args.trace:
        print("\n[节点2] loader debug:", debug, flush=True)
        print("[节点2] has_jd:", debug.get("has_jd"), "caller_jd_len:", debug.get("caller_jd_len"), flush=True)

    if not debug.get("has_jd"):
        print("\n✗ 断点: loader has_jd=False，JD 未正确传入", flush=True)
        return 1

    # [3] 模拟 Wasm 解析
    nl = stdin_str.find("\n")
    rest = stdin_str[nl + 1 :].strip() if nl >= 0 else stdin_str
    jd_extracted = _simulate_rust_extract(rest, "jd_template")
    if args.trace:
        print("\n[节点3] Wasm extract jd_template:", "OK" if jd_extracted else "FAIL", flush=True)
        if jd_extracted:
            print("[节点3] 提取内容前60字:", jd_extracted[:60], flush=True)

    if not jd_extracted:
        print("\n✗ 断点: Wasm 无法从 stdin 提取 jd_template", flush=True)
        print("stdin JSON 片段:", rest[:500] if len(rest) > 500 else rest, flush=True)
        return 1

    if "云边" in jd_extracted and "云边" not in jd_content:
        print("\n✗ 异常: 提取的 jd_template 含「云边」但传入 JD 不含，说明被 cfg/兜底覆盖", flush=True)
        return 1

    # [4] 执行 Wasm（需 LLM）
    print("\n[节点4] 调用 run_tool 执行 HR 透析镜（需 LLM 引擎，请确保 L3 已启动）...", flush=True)
    try:
        from core.wasm_runner import _host_services
        if not _host_services.get("llm_engine"):
            print("⚠️ LLM 引擎未注册，请先启动 L3 并完成配对", flush=True)
    except Exception:
        pass
    from l3_node.primitives import run_tool

    inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
    result = run_tool("jpp:com.jachin.hr.analyzer4", inp)
    if not result or "⚠️" in (result or "")[:200]:
        print(f"✗ Wasm 执行失败或异常: {result[:300] if result else 'None'}", flush=True)
        return 1

    # [5] 检查 ndjson 流中的 report_content
    from core.wasm_runner import get_last_ndjson_lines

    ndjson_lines = get_last_ndjson_lines()
    report_content = ""
    for line in reversed(ndjson_lines):
        line = (line or "").strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if item.get("status") == "progress" and item.get("report_content"):
                report_content = item.get("report_content", "") or ""
                break
        except json.JSONDecodeError:
            continue

    if not report_content:
        report_content = result or ""  # 非流式时 result 即报告

    # [6] 验证报告中的 JD
    print("\n[节点5] 验证分析报告中的岗位 JD...", flush=True)
    jd_keywords = ["Golang", "Go 语言", "微服务", "gRPC", "分布式"]
    found = [k for k in jd_keywords if k in report_content]
    yunbian_found = "云边" in report_content or "云边协同" in report_content

    if yunbian_found and "云边" not in jd_content:
        print("✗ 报告含「云边」相关表述，说明使用了默认 JD 而非传入 JD", flush=True)
        return 1

    if found:
        print(f"✓ 报告包含传入 JD 关键词: {found}", flush=True)
    else:
        print("△ 报告未明显包含 JD 关键词（可能 LLM 改写表述）", flush=True)

    # 保存报告
    out_path = Path(output_dir) / "jd_test_single_analysis.md"
    out_path.write_text(report_content, encoding="utf-8")
    print(f"\n✓ 分析报告已保存: {out_path}", flush=True)
    print(f"  报告长度: {len(report_content)}", flush=True)
    if "【岗位要求】" in report_content:
        idx = report_content.find("【岗位要求】")
        snippet = report_content[idx : idx + 200]
        print(f"  【岗位要求】片段: {snippet[:150]}…", flush=True)
    print("=" * 70, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
