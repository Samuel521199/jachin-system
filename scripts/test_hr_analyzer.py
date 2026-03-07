#!/usr/bin/env python3
"""
测试 HR 简历透视镜 Wasm 技能

前置：L2 已启动 (python -m core.main)，local-hr-fs MCP 已挂载
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 加载项目根 .env（DASHSCOPE_API_KEY 等）
try:
    from dotenv import load_dotenv
    for _p in (ROOT / ".env", ROOT / "core" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
            break
except ImportError:
    pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def main() -> int:
    from core.wasm_runner import run_wasm_plugin, register_host_services

    # 模拟 L3 环境：注册 host 服务（需有 LLM Key）
    try:
        from l3_node.llm_client import LiteLLMEngine, SecurityContext
        import os
        ctx = SecurityContext()
        if os.environ.get("DASHSCOPE_API_KEY"):
            ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
        if os.environ.get("OPENAI_API_KEY"):
            ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
        # 有 dashscope 用通义千问，否则用 gpt-4o-mini
        model = "dashscope/qwen3.5-flash" if ctx.get_key("dashscope") else "gpt-4o-mini"
        engine = LiteLLMEngine(security_context=ctx, model_name=model)
        register_host_services(llm_engine=engine, l2_base_url="http://localhost:18888")
    except Exception as e:
        print(f"[WARN] 未注册 LLM 引擎: {e}，MCP 可测但 LLM 会失败")

    wasm_path = ROOT / "l3_node" / "skills" / "wasm_plugins" / "hr-analyzer" / "main.wasm"
    if not wasm_path.exists():
        print(f"[FAIL] Wasm 不存在: {wasm_path}")
        return 1

    resume_path = str((ROOT / "data" / "hr_resumes" / "zhangsan_resume.md").resolve())
    target_role = "backend_engineer"
    jd_file = ROOT / "config" / "hr_jds" / f"{target_role}.md"
    stdin = {
        "target_role": target_role,
        "resume_filename": "zhangsan_resume.md",
        "resume_path": resume_path,
    }
    if jd_file.exists():
        stdin["jd_path"] = str(jd_file.resolve())  # Wasm 通过 MCP 读取，避免 JSON 转义

    print("=" * 60)
    print("HR 简历透视镜 Wasm 测试")
    print("=" * 60)
    print(f"输入: {stdin}")
    print()

    result = run_wasm_plugin(str(wasm_path), stdin_json=stdin)
    if result is None:
        print("[FAIL] Wasm 返回 None")
        return 1
    print("[OK] 返回长度:", len(result))
    print()
    print("--- 输出预览 (前 1500 字符) ---")
    print(result[:1500])
    if len(result) > 1500:
        print("...")
    print("=" * 60)

    # 保存到 data/hr_analysis/
    from datetime import datetime
    out_dir = ROOT / "data" / "hr_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    resume_name = stdin.get("resume_filename", "zhangsan_resume.md").replace(".md", "")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"{resume_name}_analysis_{ts}.md"
    out_file.write_text(result, encoding="utf-8")
    print(f"[SAVED] {out_file}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
