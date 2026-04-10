#!/usr/bin/env python3
"""
独立验证 **Office PowerPoint MCP**（PyPI: office-powerpoint-mcp-server，`python -m ppt_mcp_server`）。

不经过 L3 / 大模型：直接 stdio 连子进程，列工具并可选跑演示链路后落盘。

说明：PyPI 包 `office-powerpoint-mcp-server` 里 `apply_professional_design(operation=theme)` 调用的
`apply_professional_theme` 在源码中是 **占位实现**（只 return success，不改 XML），所以界面常仍是白底。
默认演示改用 `add_slide(..., background_type=professional_gradient)`，会插入渐变底图，肉眼可见。

前置（须与下面 --python 使用同一解释器）:
  pip install office-powerpoint-mcp-server==2.0.7
  （或与 skills_repo/plugin/com.jachin.mcp.office_powerpoint/requirements.txt 一致）

用法（仓库根）:
  python scripts/test_office_powerpoint_mcp_stdio.py --list-only
  python scripts/test_office_powerpoint_mcp_stdio.py --out D:\\zzz\\jachin\\mcp_smoke.pptx
  # cmd.exe 用 %USERPROFILE%，不要用 PowerShell 的 $env:USERPROFILE（脚本会尽量把 $env:USERPROFILE 替换成真实路径）
  python scripts/test_office_powerpoint_mcp_stdio.py --out %USERPROFILE%\\Desktop\\smoke.pptx

环境:
  PPT_TEMPLATE_PATH  可选，与 L3 plugin.json 一致（模板搜索路径）
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
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

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent
except ImportError:
    print("缺少 mcp SDK，请: pip install mcp>=1.0.0", file=sys.stderr)
    sys.exit(1)


def _normalize_windows_out_path(p: Path) -> Path:
    """cmd.exe 不会展开 PowerShell 的 $env:USERPROFILE；同时支持 %USERPROFILE%。"""
    s = str(p).strip()
    if not s:
        return p
    if "$env:USERPROFILE" in s:
        home = os.environ.get("USERPROFILE", "")
        if not home:
            raise SystemExit(
                "[FAIL] 路径里含 $env:USERPROFILE，但当前环境无 USERPROFILE。\n"
                "在 cmd 请用: --out %USERPROFILE%\\Desktop\\xxx.pptx\n"
                "或写完整路径，如: --out D:\\\\Users\\\\你的名字\\\\Desktop\\\\xxx.pptx"
            )
        s = s.replace("$env:USERPROFILE", home)
    s = os.path.expandvars(s)
    return Path(s)


def _print_exception_chain(exc: BaseException) -> None:
    import traceback

    if isinstance(exc, BaseExceptionGroup):
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        for i, sub in enumerate(exc.exceptions, 1):
            print(f"  --- 子异常 {i}/{len(exc.exceptions)} ---", file=sys.stderr)
            _print_exception_chain(sub)
        return
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


def _text_from_result(result) -> str:
    parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, TextContent):
            parts.append(block.text or "")
        elif hasattr(block, "text"):
            parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(parts).strip()


def _ppt_module_available(python_exe: str) -> bool:
    if os.path.normcase(os.path.abspath(python_exe)) == os.path.normcase(
        os.path.abspath(sys.executable)
    ):
        return importlib.util.find_spec("ppt_mcp_server") is not None
    import subprocess

    r = subprocess.run(
        [python_exe, "-c", "import ppt_mcp_server"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.returncode == 0


async def _run_session(
    *,
    python_exe: str,
    list_only: bool,
    minimal: bool,
    out_path: Path | None,
    pres_id: str,
) -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    params = StdioServerParameters(
        command=python_exe,
        args=["-m", "ppt_mcp_server"],
        env=env,
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = sorted(t.name for t in listed.tools)
                print(f"[OK] 已连接 ppt_mcp_server，工具数={len(names)}")
                if list_only:
                    print("--- 工具列表（name）---")
                    for n in names:
                        print(f"  - {n}")
                    print("\n--- 前 15 个工具的 description 摘要 ---")
                    for t in listed.tools[:15]:
                        d = (t.description or "").strip().split("\n")[0][:120]
                        print(f"  {t.name}: {d}")
                    if len(listed.tools) > 15:
                        print(f"  ... 另有 {len(listed.tools) - 15} 个，略")
                    return 0

                async def call(name: str, arguments: dict) -> dict:
                    print(f"\n>> call_tool {name} {json.dumps(arguments, ensure_ascii=False)[:200]}...")
                    out = await session.call_tool(name, arguments)
                    text = _text_from_result(out)
                    try:
                        obj = json.loads(text) if text.strip().startswith("{") else {"raw": text}
                    except json.JSONDecodeError:
                        obj = {"raw": text}
                    print(f"<< {json.dumps(obj, ensure_ascii=False)[:500]}{'…' if len(text) > 500 else ''}")
                    return obj if isinstance(obj, dict) else {"raw": text}

                r0 = await call("create_presentation", {"id": pres_id})
                if r0.get("error"):
                    print("[FAIL] create_presentation", file=sys.stderr)
                    return 1

                if minimal:
                    r1 = await call(
                        "add_slide",
                        {
                            "layout_index": 0,
                            "title": "MCP 冒烟测试",
                            "presentation_id": pres_id,
                        },
                    )
                    if r1.get("error"):
                        print("[FAIL] add_slide", file=sys.stderr)
                        return 1
                    r2 = await call(
                        "apply_professional_design",
                        {
                            "operation": "theme",
                            "color_scheme": "modern_blue",
                            "presentation_id": pres_id,
                            "apply_to_existing": True,
                        },
                    )
                    if r2.get("error"):
                        print("[FAIL] apply_professional_design(theme)", file=sys.stderr)
                        return 1
                else:
                    print(
                        "\n[i] 完整演示：2 页 + 渐变背景（上游 theme 为占位，此处不依赖 theme 出效果）"
                    )
                    r1 = await call(
                        "add_slide",
                        {
                            "layout_index": 0,
                            "title": "Jachin · Office PowerPoint MCP",
                            "presentation_id": pres_id,
                            "background_type": "professional_gradient",
                            "color_scheme": "modern_blue",
                            "gradient_direction": "diagonal",
                        },
                    )
                    if r1.get("error"):
                        print("[FAIL] add_slide (封面)", file=sys.stderr)
                        return 1
                    r1b = await call(
                        "add_slide",
                        {
                            "layout_index": 1,
                            "title": "本稿在验证什么",
                            "presentation_id": pres_id,
                            "background_type": "professional_gradient",
                            "color_scheme": "elegant_green",
                            "gradient_direction": "horizontal",
                        },
                    )
                    if r1b.get("error"):
                        print("[FAIL] add_slide (内容页)", file=sys.stderr)
                        return 1
                    rb = await call(
                        "add_bullet_points",
                        {
                            "slide_index": 1,
                            "placeholder_idx": 1,
                            "bullet_points": [
                                "MCP 子进程内建稿，再 save 到本地 pptx",
                                "apply_professional_design(theme) 在上游包内多为占位，界面可能仍像「默认主题」",
                                "add_slide + professional_gradient 会插入渐变图，适合肉眼确认「美化链路」",
                            ],
                            "presentation_id": pres_id,
                        },
                    )
                    if rb.get("error"):
                        print("[FAIL] add_bullet_points", file=sys.stderr)
                        return 1

                if not out_path:
                    print("\n[OK] 未指定 --out，跳过 save_presentation")
                    return 0

                out_path = _normalize_windows_out_path(out_path)
                out_path = out_path.expanduser().resolve()
                if "$env:" in str(out_path):
                    print(
                        "[FAIL] 输出路径仍含未展开的 $env:…，请勿在 cmd 里使用 PowerShell 变量写法。\n"
                        "  cmd 示例: --out %USERPROFILE%\\Desktop\\jachin_mcp_smoke.pptx",
                        file=sys.stderr,
                    )
                    return 1
                try:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                except OSError as oe:
                    print(f"[FAIL] 无法创建输出目录 {out_path.parent}: {oe}", file=sys.stderr)
                    return 1
                r3 = await call(
                    "save_presentation",
                    {
                        "file_path": str(out_path),
                        "presentation_id": pres_id,
                    },
                )
                if r3.get("error"):
                    print("[FAIL] save_presentation", file=sys.stderr)
                    return 1
                if out_path.is_file():
                    print(f"\n[OK] 已写入文件: {out_path}（{out_path.stat().st_size} bytes）")
                else:
                    print(f"\n[WARN] save 返回成功但文件不存在: {out_path}", file=sys.stderr)
                return 0
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        _print_exception_chain(e)
        return 1
    except BaseException as e:
        # ExceptionGroup（3.11+）等继承 BaseException 而非 Exception
        _print_exception_chain(e)
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="独立测试 Office PowerPoint MCP（stdio）")
    ap.add_argument(
        "--python",
        default=sys.executable,
        help="用于执行 -m ppt_mcp_server 的 Python（默认当前解释器）",
    )
    ap.add_argument("--list-only", action="store_true", help="仅列工具，不调用业务")
    ap.add_argument(
        "--minimal",
        action="store_true",
        help="仅 1 页 + theme 调用（最快；theme 在上游多为无视觉效果）",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="冒烟测试保存路径，如 D:\\\\zzz\\\\jachin\\\\mcp_smoke.pptx；省略则只测内存不写盘",
    )
    ap.add_argument(
        "--presentation-id",
        default="jachin_mcp_stdio_smoke",
        help="内存中的 presentation_id",
    )
    args = ap.parse_args()
    if not _ppt_module_available(args.python):
        print(
            "[FAIL] 指定 Python 未安装 ppt_mcp_server：\n"
            f"  {args.python} -m pip install office-powerpoint-mcp-server==2.0.7",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(
        _run_session(
            python_exe=args.python,
            list_only=args.list_only,
            minimal=args.minimal,
            out_path=args.out,
            pres_id=str(args.presentation_id).strip() or "jachin_mcp_stdio_smoke",
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
