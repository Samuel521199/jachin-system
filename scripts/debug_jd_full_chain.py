#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
岗位 JD 传递全链路测试脚本

追踪：前端/API → recruitment_task → loader → Wasm stdin → mcp_read_file
使用图片中的岗位 JD 内容，定位断点。引用系统核心代码。

用法:
  python scripts/debug_jd_full_chain.py
  python scripts/debug_jd_full_chain.py --jd "自定义JD内容"
  python scripts/debug_jd_full_chain.py --analysis "c:\\analisy\\xxx_analysis.md"  # 检查已有报告中的 JD
"""
import argparse
import json
import sys
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 图片中的岗位 JD（与 RecruitmentDashboard 表单一致）
IMAGE_JD = """2. 具备扎实的计算机基础,熟悉MySQL、Redis等数据库与中间件。
3. 有高并发、分布式系统开发经验者优先。
4. 具备良好的团队协作和沟通能力,对技术有热情。"""


def _simulate_rust_extract(json_str: str, key: str) -> str | None:
    """模拟 hr-analyzer4 extract_json_str_unescaped 的解析逻辑。"""
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
    p = argparse.ArgumentParser(description="岗位 JD 传递全链路测试")
    p.add_argument("--jd", default="", help="岗位 JD 内容，默认使用图片中的 JD")
    p.add_argument("--empty", action="store_true", help="模拟 jd_content 为空，验证回退到云边协同")
    p.add_argument("--analysis", default="", help="已有分析报告路径，检查其中使用的 JD")
    args = p.parse_args()

    jd_content = "" if args.empty else (args.jd or IMAGE_JD).strip()
    print("=" * 70)
    print("岗位 JD 传递全链路测试（引用 l3_node.recruitment_task / loader / wasm_runner）")
    print("=" * 70)
    print(f"\n测试 JD 长度: {len(jd_content)}")
    print(f"预览: {jd_content[:80]}…" if len(jd_content) > 80 else f"预览: {jd_content}")

    # ========== [1] 模拟 HTTP 请求体（与 api.ts / http_server 一致）==========
    print("\n" + "=" * 70)
    print("[1] 模拟 HTTP 请求体（api.ts → http_server）")
    print("-" * 70)
    body = {
        "job_name": "Java_杭州 10-15K",
        "max_count": 5,
        "filter_tab": "全部",
        "request_resume": True,
        "jd_content": jd_content,
        "focus_keywords": "4. 具备良好的团队协作和沟通能力,对技术有热情。",
        "strictness": "standard",
    }
    jd_from_body = (body.get("jd_content") or "").strip()
    print(f"body.jd_content len: {len(jd_from_body)}")
    if not jd_from_body:
        print("⚠️ 断点1: HTTP body 中 jd_content 为空！前端可能未正确传入。")
    else:
        print(f"✓ body.jd_content 有值 preview={jd_from_body[:60]}…")

    # ========== [2] recruitment_task 层 ==========
    print("\n" + "=" * 70)
    print("[2] recruitment_task 层（jd_final 来源）")
    print("-" * 70)
    from l3_node.recruitment_task import _fetch_jd_from_db, DEFAULT_JD

    jd_final = (jd_from_body or "").strip() or _fetch_jd_from_db() or DEFAULT_JD
    print(f"jd_final 来源: {'表单传入' if jd_from_body else '数据库兜底' if _fetch_jd_from_db() else 'DEFAULT_JD'}")
    print(f"jd_final len: {len(jd_final)}")
    print(f"jd_final preview: {jd_final[:80]}…" if len(jd_final) > 80 else f"jd_final: {jd_final}")
    if "云边协同" in jd_final and jd_from_body:
        print("⚠️ 异常: 表单有 JD 但 jd_final 仍是云边协同，请检查 recruitment_task 逻辑")
    elif "云边协同" in jd_final and not jd_from_body:
        print("⚠️ 断点2: jd_content 为空，回退到 DEFAULT_JD（云边协同）")

    # ========== [3] loader 层：build_hr_stdin_for_debug ==========
    print("\n" + "=" * 70)
    print("[3] loader 层：build_hr_stdin_for_debug（与 _invoke_wasm 一致）")
    print("-" * 70)
    from l3_node.skills.loader import build_hr_stdin_for_debug

    sample_pdf = str(Path.home() / ".jachin" / "client_volumes" / "pool_test" / "resume.pdf")
    input_data = {
        "target_dir": "pool_Java_杭州_10-15K",
        "_hr_files": sample_pdf,
        "jd_template": jd_final,
        "strictness": "standard",
        "output_dir": "data/hr_analysis",
        "capability": "execute",
    }
    stdin_str, debug = build_hr_stdin_for_debug(input_data)
    print(f"jd_src: {debug.get('jd_src')}")
    print(f"jd_path: {debug.get('jd_path', 'N/A')}")
    print(f"has_jd: {debug.get('has_jd')}")
    print(f"caller_jd_len: {debug.get('caller_jd_len')}")
    print(f"jd_preview: {(debug.get('jd_preview') or '')[:80]}…")
    if not debug.get("has_jd"):
        print("⚠️ 断点3: loader 未识别到 JD，has_jd=False")

    # ========== [4] Wasm 解析层：从 stdin 提取 jd_path ==========
    print("\n" + "=" * 70)
    print("[4] Wasm 解析层：extract jd_path / jd_template")
    print("-" * 70)
    nl = stdin_str.find("\n")
    rest = stdin_str[nl + 1 :].strip() if nl >= 0 else stdin_str
    jd_path_extracted = _simulate_rust_extract(rest, "jd_path")
    jd_template_extracted = _simulate_rust_extract(rest, "jd_template")
    print(f"extract jd_path: {repr((jd_path_extracted or '')[:80])}…" if jd_path_extracted and len(jd_path_extracted) > 80 else f"extract jd_path: {repr(jd_path_extracted)}")
    print(f"extract jd_template: {repr((jd_template_extracted or '')[:60])}…" if jd_template_extracted and len(jd_template_extracted) > 60 else f"extract jd_template: {repr(jd_template_extracted)}")
    if not jd_path_extracted and not jd_template_extracted:
        print("⚠️ 断点4: Wasm 无法从 stdin 提取 jd_path 或 jd_template")

    # ========== [5] mcp_read_file 模拟：jd_path 文件可读性 ==========
    print("\n" + "=" * 70)
    print("[5] mcp_read_file 模拟（wasm_runner 逻辑）")
    print("-" * 70)
    if jd_path_extracted:
        for raw_path in [jd_path_extracted, jd_path_extracted.replace("/", "\\"), jd_path_extracted.replace("\\", "/")]:
            p = Path(raw_path)
            try:
                resolved = p.resolve()
                if resolved.exists() and resolved.is_file():
                    content = resolved.read_text(encoding="utf-8", errors="replace")
                    print(f"✓ 文件可读 path={resolved}")
                    print(f"  内容 len={len(content)} preview={content[:80]}…")
                    if "云边" in content and jd_content and "云边" not in jd_content:
                        print("⚠️ 断点5: 临时文件内容为云边协同，与传入 JD 不符（可能为旧缓存）")
                    break
            except Exception as e:
                continue
        else:
            print(f"✗ 文件不可读 path={jd_path_extracted}")
            print("  Wasm mcp_read_file 将返回 -1，回退到 jd_template 或 DEFAULT_ROLE（云边协同）")
    else:
        print("无 jd_path，Wasm 将使用 jd_template 或 DEFAULT_ROLE")

    # ========== [6] 检查已有分析报告 ==========
    if args.analysis:
        print("\n" + "=" * 70)
        print("[6] 检查已有分析报告中的 JD 使用情况")
        print("-" * 70)
        ap = Path(args.analysis)
        if ap.exists():
            text = ap.read_text(encoding="utf-8", errors="replace")
            if "云边" in text or "云边协同" in text:
                print("⚠️ 报告中包含「云边」相关表述，说明使用了 DEFAULT_JD 而非表单 JD")
            if "MySQL" in text or "Redis" in text or "高并发" in text:
                print("✓ 报告中包含图片 JD 关键词（MySQL/Redis/高并发），说明 JD 传递成功")
            # 简单统计
            print(f"报告 len={len(text)} 云边出现次数={text.count('云边')} MySQL={text.count('MySQL')}")
        else:
            print(f"文件不存在: {ap}")

    # ========== 结论 ==========
    print("\n" + "=" * 70)
    print("结论")
    print("-" * 70)
    issues = []
    if not jd_from_body:
        issues.append("HTTP body jd_content 为空 → 检查前端 RecruitmentDashboard jdTextareaRef/ config.jdContent")
    if "云边协同" in jd_final and jd_from_body:
        issues.append("jd_final 仍为云边协同 → 检查 recruitment_task jd_content 传参")
    if not debug.get("has_jd"):
        issues.append("loader has_jd=False → 检查 build_hr_stdin 的 caller_jd 来源")
    if not jd_path_extracted and not jd_template_extracted:
        issues.append("Wasm 无法提取 jd_path/jd_template → 检查 stdin JSON 格式或 Rust extract 逻辑")
    if jd_path_extracted and not Path(jd_path_extracted.replace("/", "\\")).exists():
        issues.append("jd_path 临时文件不存在 → 检查 loader 写入或 Windows 路径格式")

    if issues:
        print("发现以下可能断点：")
        for i, s in enumerate(issues, 1):
            print(f"  {i}. {s}")
    else:
        print("✓ 全链路检查通过，JD 应能正确传递。若报告仍为云边，请检查 L3 是否已重启加载最新代码。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
