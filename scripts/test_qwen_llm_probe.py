#!/usr/bin/env python3
"""
探测 DashScope compatible-mode 能否调用 `LLM_COMPLEX_MODEL`（默认与 .env 中复杂任务一致，如 qwen3-max）。

与 `test_kalaroko_default_scenarios_e2e.py` 共用 `_generate_llm_summary`，逻辑与密钥、模型、URL 完全一致。

用法（仓库根目录）::

  python scripts/test_qwen_llm_probe.py

退出码：0 调用成功且拿到非错误 Markdown；1 调用失败或返回错误段落；2 未配置密钥跳过；3 参数/导入异常。
"""
from __future__ import annotations

import asyncio
import importlib.util
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


def _load_e2e_module():
    path = ROOT / "scripts" / "test_kalaroko_default_scenarios_e2e.py"
    spec = importlib.util.spec_from_file_location("kalaroko_e2e_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _async_main() -> int:
    try:
        mod = _load_e2e_module()
    except Exception as e:
        print(f"[probe] 导入 E2E 模块失败: {e!r}", flush=True)
        return 3

    # 两轮最小占位数据，与真实多轮分析路径一致（len>1）
    dummy_history = [
        {
            "page_ttfb": 100,
            "page_load": 200,
            "page_success": True,
            "tongits_king_ttfb": 300,
            "tongits_king_load": 400,
            "tongits_king_success": True,
        },
        {
            "page_ttfb": 110,
            "page_load": 210,
            "page_success": True,
            "tongits_king_ttfb": 310,
            "tongits_king_load": 410,
            "tongits_king_success": True,
        },
    ]

    print(
        "[probe] 调用 test_kalaroko_default_scenarios_e2e._generate_llm_summary …",
        flush=True,
    )
    text = await mod._generate_llm_summary(dummy_history)

    print("\n" + "=" * 60)
    print("[probe] 返回全文:")
    print(text)
    print("=" * 60 + "\n")

    if text.startswith("> ⚠️"):
        print("[probe] 结果: 跳过（未配置密钥）", flush=True)
        return 2
    if text.startswith("> ❌"):
        print("[probe] 结果: 失败", flush=True)
        return 1
    print("[probe] 结果: 成功（拿到模型回复）", flush=True)
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
