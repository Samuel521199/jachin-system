#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GameQA · 控制台背后的「点火器」：本进程内创建 L3 LiteLLM 引擎，调用 ``run_agent`` 读取 Skill MD，
并按白名单调度 ``mcp:tool_*``（与 ``l3_node/gameqa_http.py`` 的 ``run-skill`` 语义一致）。

不依赖桌面 UI、也不依赖已运行的 L3 HTTP/WebSocket（除非你希望与另一 L3 共用 browser，则需自行协调 CDP）。

用法（仓库根）::

  python scripts/run_gameqa_skill.py
  python scripts/run_gameqa_skill.py --skill gameqa_shadow_apprentice.md --url https://www.kalaroko.com/
  python scripts/run_gameqa_skill.py --skill gameqa_auto_test.md --rules-path "D:\\rules\\my.md"

前置：
  - ``.env`` 中配置 ``DASHSCOPE_API_KEY`` 或 ``OPENAI_API_KEY``（与 ``python -m l3_node --ws-only`` 相同）
  - ``pip install`` 与 Playwright 等依赖已按主仓要求就绪

  - 每次运行会在 ``%USERPROFILE%\.jachin\jachin_debug\健康skill\gameqa_skill_debug.log`` 写入**覆盖式**
    诊断日志（stdio MCP / Playwright click 等），stdout 会打印该路径。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from pathlib import Path

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass

DEFAULT_URL = os.environ.get("K11_BROWSER_CONTEXT_URL", "https://www.kalaroko.com/").strip() or "https://www.kalaroko.com/"


async def _async_main(args: argparse.Namespace) -> int:
    from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import (
        append as _skill_dbg,
        attach_mcp_diagnostic_handlers,
        init_run_gameqa_skill_log,
    )

    _dbg_path = init_run_gameqa_skill_log(
        {
            "argv": " ".join(sys.argv),
            "cwd": os.getcwd(),
            "skill_arg": getattr(args, "skill", ""),
            "url_arg": getattr(args, "url", ""),
            "rules_path_arg": getattr(args, "rules_path", ""),
            "max_iterations": getattr(args, "max_iterations", ""),
        }
    )
    attach_mcp_diagnostic_handlers()
    print(f"[run_gameqa_skill] 诊断日志（每次运行覆盖）: {_dbg_path}", flush=True)

    from l3_node.gameqa_http import _allowlist_for_skill, _resolve_skill_path

    sp = _resolve_skill_path(args.skill.strip())
    if not sp:
        print(f"[run_gameqa_skill] Skill 未找到: {args.skill!r}", file=sys.stderr)
        print(
            "  提示：使用 ``l3_node/skills/gameqa/*.md`` 文件名，或传入 .md 的绝对路径。",
            file=sys.stderr,
        )
        return 2

    try:
        skill_text = sp.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[run_gameqa_skill] 读取 Skill 失败: {e}", file=sys.stderr)
        return 2

    url = (args.url or "").strip()
    if not url:
        print("[run_gameqa_skill] --url 不能为空", file=sys.stderr)
        return 2

    rules_path = (args.rules_path or "").strip()
    allow = _allowlist_for_skill(sp)
    mi = max(1, min(int(args.max_iterations), 48))

    user_input = (
        f"[GameQA · run-skill 宿主上下文 · CLI]\n"
        f"target_url: {url}\n"
        f"rules_path: {rules_path}\n"
        f"skill_file: {sp}\n\n"
        f"--- SKILL BEGIN ---\n{skill_text}\n--- SKILL END ---\n\n"
        f"请严格按 SKILL 中的 Persona、工具白名单与 SOP 执行（ReAct）。\n"
    )

    _skill_dbg(
        "[stdio MCP] 即将 await start_l3_stdio_mcp_host() —— 此处会拉起 npx/uvx 等子进程；"
        "失败时常见日志: Connection closed / McpError（与 GameQA 本地五件套无关）。"
    )
    try:
        from l3_node.mcp_stdio_bootstrap import start_l3_stdio_mcp_host

        await start_l3_stdio_mcp_host()
        _skill_dbg("[stdio MCP] start_l3_stdio_mcp_host() 已返回（本次未抛异常）。详情见上方 FileHandler 记录的 core.mcp_client。")
    except Exception as e:
        _skill_dbg(f"[stdio MCP] start_l3_stdio_mcp_host() 抛异常: {e!r}")
        _skill_dbg(traceback.format_exc())

    try:
        from l3_node.__main__ import _create_engine_standalone

        engine = _create_engine_standalone()
    except Exception as e:
        try:
            from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append as _skill_dbg

            _skill_dbg(f"[engine] _create_engine_standalone 失败: {e!r}")
            _skill_dbg(traceback.format_exc())
        except Exception:
            pass
        print(
            f"[run_gameqa_skill] 引擎初始化失败: {e}\n"
            "  请检查 .env 中 DASHSCOPE_API_KEY / OPENAI_API_KEY 等（与 python -m l3_node --ws-only 一致）。",
            file=sys.stderr,
        )
        return 2

    from l3_node.agent_core import run_agent
    from l3_client.local_mcps.gameqa_mcp.session_service import get_gameqa_service

    _skill_dbg(
        "[run_agent] 即将进入 run_agent —— ReAct 过程中会再次 assemble/invoke MCP；"
        "若 Agent 调用 mcp:tool_execute_action，诊断文件将出现 [CLICK] / Registry 包裹日志。"
    )

    async def _on_chunk(s: str) -> None:
        frag = (s or "").replace("\r", " ").replace("\n", " ")
        if not frag.strip():
            return
        if len(frag) > 3600:
            frag = frag[:3600] + "…"
        try:
            await get_gameqa_service().emit_log(f"[gameqa][agent][cli] {frag}")
        except Exception:
            pass
        if not args.quiet:
            print(f"[agent] {frag}", flush=True)

    try:
        ans = await run_agent(
            user_input,
            engine,
            max_iterations=mi,
            _allowed_skills_override=allow,
            on_chunk=_on_chunk,
            implicit_attribution={"channel": "gameqa_cli", "source": "run_gameqa_skill.py"},
        )
    except BaseException as e:
        _skill_dbg(f"[run_agent] 未捕获异常穿出: {e!r}")
        _skill_dbg(traceback.format_exc())
        raise
    _skill_dbg("[run_agent] 已正常返回（详见 Final Answer）。")
    print("\n--- Final Answer ---\n", (ans or "").strip())
    # 减轻 Windows 上 asyncio/Playwright 子进程在事件循环关闭时 __del__ 报的噪声
    await asyncio.sleep(1.5)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="GameQA：CLI 触发 L3 Agent 执行 Skill（等价控制台 run-skill 核心）")
    ap.add_argument(
        "--skill",
        default="gameqa_auto_test.md",
        help="Skill 文件名（位于 l3_node/skills/gameqa/）或 .md 绝对路径",
    )
    ap.add_argument("--url", default=DEFAULT_URL, help="目标站点 URL")
    ap.add_argument("--rules-path", default="", help="规则 MD 路径（可空）")
    ap.add_argument("--max-iterations", type=int, default=32, help="Agent ReAct 上限（1～48）")
    ap.add_argument("--quiet", action="store_true", help="不在 stdout 打印流式 chunk（仍写 GameQA 日志队列）")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[run_gameqa_skill] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
