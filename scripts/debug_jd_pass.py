#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
岗位 JD 传递调试脚本

追踪从 recruitment_task -> loader -> Wasm 的 JD 传递链路，引用核心代码排查错误。
用法: python scripts/debug_jd_pass.py [jd_content]
  无参数时使用数据库兜底或 DEFAULT_JD
"""
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


def _simulate_rust_extract(json_str: str, key: str) -> str | None:
    """模拟 hr-analyzer4 extract_json_str_unescaped 的解析逻辑。"""
    # Rust 使用 pat_no_space = '"key":"' 和 pat_with_space = '"key": "'，val_start 指向值首字符
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
    jd_from_cli = sys.argv[1] if len(sys.argv) > 1 else ""
    print("=" * 70)
    print("岗位 JD 传递调试（引用 l3_node.recruitment_task / loader 核心代码）")
    print("=" * 70)

    # [1] recruitment_task 层：jd_final 来源
    from l3_node.recruitment_task import _fetch_jd_from_db, DEFAULT_JD

    jd_final = (jd_from_cli or "").strip() or _fetch_jd_from_db() or DEFAULT_JD
    print("\n[1] recruitment_task 层：jd_final")
    print("-" * 70)
    print(f"来源: {'CLI 参数' if jd_from_cli else '数据库兜底' if _fetch_jd_from_db() else 'DEFAULT_JD'}")
    print(f"len: {len(jd_final)}")
    print(f"preview: {jd_final[:100]}…" if len(jd_final) > 100 else f"preview: {jd_final}")
    if "云边协同" in jd_final and not jd_from_cli:
        print("⚠️ 若为 DEFAULT_JD（云边协同），说明表单/数据库未正确传入 JD")
    print()

    # [2] loader 层：构建 stdin
    from l3_node.primitives.tools.loader import build_hr_stdin_for_debug

    # 模拟 recruitment_task 的 input_data
    sample_pdf = str(Path.home() / ".jachin" / "client_volumes" / "pool_test" / "resume.pdf")
    input_data = {
        "target_dir": "auto_pool_test",
        "_hr_files": sample_pdf,
        "jd_template": jd_final,
        "strictness": "standard",
        "output_dir": "data/hr_analysis",
        "capability": "execute",
    }
    stdin_str, debug = build_hr_stdin_for_debug(input_data)
    print("[2] loader 层：build_hr_stdin_for_debug")
    print("-" * 70)
    print(f"jd_src: {debug.get('jd_src')}")
    print(f"jd_path: {debug.get('jd_path', 'N/A')}")
    print(f"has_jd: {debug.get('has_jd')}")
    print(f"caller_jd_len: {debug.get('caller_jd_len')}")
    print(f"jd_preview: {debug.get('jd_preview', '')[:80]}…")
    print(f"stdin_str len: {len(stdin_str)}")
    print()

    # [3] Wasm 解析层：从 stdin 提取 jd_path / jd_template
    nl = stdin_str.find("\n")
    if nl >= 0:
        first_line = stdin_str[:nl].strip()
        rest = stdin_str[nl + 1 :].strip()
    else:
        first_line = ""
        rest = stdin_str
    print("[3] Wasm 解析层：extract_json_str_unescaped 模拟")
    print("-" * 70)
    jd_path_extracted = _simulate_rust_extract(rest, "jd_path")
    jd_template_extracted = _simulate_rust_extract(rest, "jd_template")
    print(f"首行 (_hr_files): {first_line[:80]}…" if len(first_line) > 80 else f"首行: {first_line}")
    print(f"JSON 部分 len: {len(rest)}")
    print(f'extract "jd_path": {repr(jd_path_extracted[:60])}…' if jd_path_extracted and len(jd_path_extracted) > 60 else f'extract "jd_path": {repr(jd_path_extracted)}')
    print(f'extract "jd_template": {repr((jd_template_extracted or "")[:60])}…' if jd_template_extracted and len(jd_template_extracted) > 60 else f'extract "jd_template": {repr(jd_template_extracted)}')
    print()

    # [4] mcp_read_file 模拟：jd_path 文件是否可读
    print("[4] mcp_read_file 模拟：jd_path 文件可读性")
    print("-" * 70)
    if jd_path_extracted:
        p = Path(jd_path_extracted)
        if p.exists() and p.is_file():
            content = p.read_text(encoding="utf-8", errors="replace")
            print(f"✓ 文件存在 path={p}")
            print(f"  内容 len={len(content)} preview={content[:80]}…")
        else:
            # 尝试 Windows 路径变体
            for alt in (Path(jd_path_extracted.replace("/", "\\")), Path(jd_path_extracted.replace("\\", "/"))):
                if alt.exists() and alt.is_file():
                    content = alt.read_text(encoding="utf-8", errors="replace")
                    print(f"✓ 文件存在（路径变体）path={alt}")
                    print(f"  内容 len={len(content)} preview={content[:80]}…")
                    break
            else:
                print(f"✗ 文件不存在 path={jd_path_extracted}")
                print("  Wasm mcp_read_file 将返回 -1，回退到 jd_template 或 DEFAULT_ROLE")
    else:
        print("无 jd_path，Wasm 将使用 jd_template 或 DEFAULT_ROLE")
    print()

    # [5] 结论
    print("=" * 70)
    print("[5] 结论")
    print("-" * 70)
    if debug.get("has_jd") and (jd_path_extracted or jd_template_extracted):
        if jd_path_extracted and Path(jd_path_extracted.replace("/", "\\")).exists():
            print("✓ 岗位 JD 传递链路正常：jd_path 可被 Wasm 读取")
        elif jd_template_extracted:
            print("△ 岗位 JD 通过 jd_template 传递（长文本/特殊字符可能导致 JSON 解析失败）")
        else:
            print("✗ jd_path 文件不可读，Wasm 将回退 DEFAULT_ROLE（云边协同后端架构师）")
    else:
        print("✗ 岗位 JD 为空，Wasm 将使用 DEFAULT_ROLE")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
