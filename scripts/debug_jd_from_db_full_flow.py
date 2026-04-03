#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从数据库读取最新岗位 JD，调用完整 HR 透析镜流程，每个关键节点打印完整 JD 便于排查。

用法:
  python scripts/debug_jd_from_db_full_flow.py [pdf_path1] [pdf_path2] ...
  无参数时使用 ~/.jachin/client_volumes 下最近收网的 PDF

依赖：L3 已启动且 LLM 可用（或设置环境变量跳过实际 Wasm 调用做 dry-run）
"""
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


def _print_jd(label: str, jd: str, path: str = "") -> None:
    """统一打印岗位 JD"""
    sep = "=" * 70
    print(f"\n{sep}\n[{label}]", flush=True)
    if path:
        print(f"path: {path}", flush=True)
    print(f"len: {len(jd)}", flush=True)
    print(f"内容:\n{jd}\n{sep}\n", flush=True)


def main() -> int:
    pdf_paths = [p for p in sys.argv[1:] if p] if len(sys.argv) > 1 else []
    if not pdf_paths:
        vol = Path.home() / ".jachin" / "client_volumes"
        for d in sorted(vol.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:5]:
            if d.is_dir():
                for f in d.rglob("*.pdf"):
                    pdf_paths.append(str(f.resolve()))
                    if len(pdf_paths) >= 3:
                        break
            if len(pdf_paths) >= 3:
                break
    if not pdf_paths:
        print("未找到 PDF，请指定或先收网", flush=True)
        return 1

    paths_str = "|||".join(str(p).replace("\\", "/") for p in pdf_paths)
    print(f"使用 PDF: {pdf_paths[:3]}", flush=True)

    # [1] 数据库读取
    from l3_node.recruitment_task import _fetch_jd_from_db, DEFAULT_JD, HR_SKILL_ID
    from core.skill_registry import get_skill_config

    jd_db = _fetch_jd_from_db()
    cfg = get_skill_config(HR_SKILL_ID)
    jd_cfg = (cfg.get("JD_template") or cfg.get("jd_template") or "").strip()
    jd_from_db = jd_db or jd_cfg or DEFAULT_JD
    _print_jd("节点1: 数据库 skill_registry", jd_from_db or "(空)")
    _print_jd("节点1b: _fetch_jd_from_db()", jd_db or "(空)")
    _print_jd("节点1c: get_skill_config JD_template", jd_cfg or "(空)")

    # [2] recruitment_task 层 jd_final
    jd_final = jd_from_db.strip() or DEFAULT_JD
    _print_jd("节点2: recruitment_task jd_final", jd_final)

    # [3] loader build_hr_stdin_for_debug
    from l3_node.primitives.tools.loader import build_hr_stdin_for_debug

    input_data = {
        "target_dir": "pool_前端开发工程师_杭州_15-26K",
        "_hr_files": paths_str,
        "jd_template": jd_final,
        "strictness": "standard",
        "output_dir": "data/hr_analysis",
        "capability": "execute",
    }
    stdin_str, debug = build_hr_stdin_for_debug(input_data)
    _print_jd("节点3: loader build_hr_stdin_for_debug 输出", input_data.get("jd_template", "") or debug.get("jd_preview", ""))
    print(f"节点3: jd_src={debug.get('jd_src')} jd_path={debug.get('jd_path')} has_jd={debug.get('has_jd')}", flush=True)

    # [4] 实际调用 run_tool（含 loader 真实逻辑）
    if os.environ.get("DEBUG_JD_DRY_RUN") == "1":
        print("\n[DRY-RUN] 跳过 Wasm 调用，设置 DEBUG_JD_DRY_RUN=0 可执行完整流程", flush=True)
        return 0

    print("\n[节点4] 调用 run_tool... (观察 stderr 中 [Loader] [mcp_read_file] 的岗位 JD 输出)", flush=True)
    from l3_node.primitives import run_tool
    import queue
    ndjson_queue = queue.Queue()
    try:
        inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
        r = run_tool("jpp:com.jachin.hr.analyzer4", inp, allowed_skills=None, ndjson_queue=ndjson_queue)
        print(f"\n[节点4] run_tool 返回 len={len(str(r)) if r else 0}", flush=True)
        # 消费 ndjson 中的 debug 消息
        while True:
            try:
                line = ndjson_queue.get(timeout=0.5)
            except Exception:
                break
            item = json.loads(line) if line else {}
            if item.get("status") == "debug":
                jd_len = item.get("jd_len", 0)
                jd_preview = item.get("jd_preview", "")
                _print_jd("节点5: Wasm 实际收到的 job_desc", jd_preview or f"(jd_len={jd_len})")
                if jd_len == 0:
                    print("⚠️ Wasm jd_len=0，岗位 JD 未正确传入！", flush=True)
            if item.get("status") == "done":
                break
    except Exception as e:
        print(f"run_tool 异常: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return 1

    print("\n完成。若 Wasm 收到的 job_desc 仍为云边协同，请检查 mcp_read_file 是否成功读取临时文件。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
