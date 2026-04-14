#!/usr/bin/env python3
"""
Smoke test: 同一张本地 PNG，用 LiteLLM 调用 DashScope，对比
  - dashscope/qwen3.5-plus（文本为主，可能不消费图片）
  - dashscope/qwen-vl-max（多模态，应能描述图片）

用法（仓库根目录，需已安装 litellm 与 DASHSCOPE_API_KEY）:
  python scripts/test_dashscope_vision_smoke.py
  python scripts/test_dashscope_vision_smoke.py --model dashscope/qwen-vl-max
  python scripts/test_dashscope_vision_smoke.py --image path/to/other.png

说明见: docs/tests/DASHSCOPE_VISION_SMOKE.md
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def png_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = "image/png"
    if path.suffix.lower() in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    return f"data:{mime};base64,{b64}"


async def run_one(model: str, data_url: str, prompt: str) -> str:
    import litellm

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    resp = await litellm.acompletion(
        model=model,
        messages=messages,
        max_tokens=1024,
        temperature=0.2,
    )
    choice = resp.choices[0]
    msg = choice.message
    content = getattr(msg, "content", None) or ""
    return content.strip()


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="DashScope 视觉冒烟：单图 + LiteLLM")
    parser.add_argument(
        "--image",
        type=Path,
        default=_repo_root() / ".playwright-mcp" / "page-2026-04-10T01-59-28-779Z.png",
        help="本地图片路径（默认仓库内 Playwright 截图）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="单个模型 id，如 dashscope/qwen-vl-max；默认跑两个对比模型",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="请用中文简要描述这张截图里可见的界面元素与文字（若有）。不要说你没收到图片。",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_root() / ".env")
        load_dotenv(_repo_root() / "dist_jachin_desktop" / ".env")
    except Exception:
        pass

    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("错误: 未设置环境变量 DASHSCOPE_API_KEY", file=sys.stderr)
        return 2

    img = args.image.resolve()
    if not img.is_file():
        print(f"错误: 图片不存在: {img}", file=sys.stderr)
        return 2

    data_url = png_to_data_url(img)
    print(f"图片: {img} ({img.stat().st_size} bytes)", flush=True)
    print(f"data URL 长度: {len(data_url)} 字符\n", flush=True)

    models = (
        [args.model.strip()]
        if args.model.strip()
        else [
            "dashscope/qwen3.5-plus",
            "dashscope/qwen-vl-max",
        ]
    )

    for m in models:
        print("=" * 60, flush=True)
        print(f"模型: {m}", flush=True)
        print("=" * 60, flush=True)
        try:
            text = await run_one(m, data_url, args.prompt)
            print(text or "(空响应)", flush=True)
        except Exception as e:
            print(f"[异常] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        print(flush=True)

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
