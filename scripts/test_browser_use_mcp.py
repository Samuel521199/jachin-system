#!/usr/bin/env python3
"""
Browser-Use MCP：以 Agent 为「手脚」执行 K11 / www.kalaroko.com 等平台 P0 冒烟用例，并输出测试结果。

默认站点：https://www.kalaroko.com/

前置：
  pip install uv mcp python-dotenv（可选）
  OPENAI_API_KEY 或 ANTHROPIC_API_KEY；若仅配置阿里百炼（DashScope），脚本会按 JACHIN_ACTIVE_REGION 映射到 OPENAI_* + BROWSER_USE_LLM_MODEL
  Chrome：默认连接本机已启动的远程调试实例（见 --cdp-http）；若需由 browser-use 自行拉起浏览器，请加 --spawn-browser

用法：
  python scripts/test_browser_use_mcp.py                    # 连接 http://127.0.0.1:9222 上已打开的 Chrome，跑全部 P0
  python scripts/test_browser_use_mcp.py --spawn-browser      # 改回由 MCP 自行启动浏览器
  python scripts/test_browser_use_mcp.py --cdp-http http://127.0.0.1:9223
  python scripts/test_browser_use_mcp.py --cases homepage,category_tabs
  python scripts/test_browser_use_mcp.py --list-tools-only
  python scripts/test_browser_use_mcp.py --agent-task "自定义单条任务"   # 兼容旧行为
  python scripts/test_browser_use_mcp.py --json-report report.json
  python scripts/test_browser_use_mcp.py --timeout-per-case 420

每条用例会在指令末尾要求 Agent 输出一行：VERDICT: PASS|FAIL|BLOCKED，脚本据此汇总；
若未匹配到该行，标记为 UNKNOWN 并以退出码 1 提示人工复核原始输出。

关于输出里的「Errors encountered」：多为 browser-use 单步事件（如 NavigateToUrl）在 bubus 层触发的超时/中断记录，
整轮仍可能 Success: True；与最终 VERDICT 不完全等价。默认已把 TIMEOUT_NavigateToUrlEvent 放宽到 60s（可用 --nav-event-timeout）。
耗时：每条 P0 会多轮调用大模型 + CDP 操作，分钟级属常态。
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(Path.home() / ".jachin" / ".env")
except ImportError:
    pass


def _dashscope_openai_compatible_key_and_base() -> tuple[str | None, str | None]:
    """
    与 core.brain.llm.dashscope_regional 对齐的选 Key / Base（不 import core.brain.llm，避免拉起 QwenAdapter 等告警）。
    """
    _cn = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    _sea = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    r = (os.environ.get("JACHIN_ACTIVE_REGION") or "").strip().upper() or "CN"

    if r == "SEA":
        key = (os.environ.get("DASHSCOPE_API_KEY_SEA") or "").strip() or None
        base = (os.environ.get("DASHSCOPE_API_BASE_SEA") or "").strip() or None
        default_base = _sea
    else:
        key = (os.environ.get("DASHSCOPE_API_KEY_CN") or "").strip() or None
        base = (os.environ.get("DASHSCOPE_API_BASE_CN") or "").strip() or None
        default_base = _cn

    if not key:
        key = (
            (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
            or (os.environ.get("QWEN_API_KEY") or "").strip()
            or (os.environ.get("QWEN_AI_API_KEY") or "").strip()
        ) or None
    if not base:
        base = (os.environ.get("DASHSCOPE_API_BASE") or "").strip() or None
    if not base:
        base = default_base
    return key, base


def _hydrate_llm_env_for_browser_use() -> None:
    """
    browser-use MCP 默认走 ChatOpenAI + OPENAI_API_KEY；仅配 DashScope 时提前失败。
    在已加载 dotenv 后，将仓库约定的百炼凭证映射到 OpenAI 兼容 env（不打印密钥）。
    """
    if (os.environ.get("OPENAI_API_KEY") or "").strip():
        return
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return

    key, base = _dashscope_openai_compatible_key_and_base()
    if not key:
        return

    os.environ["OPENAI_API_KEY"] = key
    if base and not (os.environ.get("OPENAI_BASE_URL") or "").strip():
        os.environ["OPENAI_BASE_URL"] = base.rstrip("/")
    if not (os.environ.get("BROWSER_USE_LLM_MODEL") or "").strip():
        fallback = (os.environ.get("LLM_MODEL") or "qwen3.5-plus").strip() or "qwen3.5-plus"
        os.environ["BROWSER_USE_LLM_MODEL"] = fallback
    print(
        "[INFO] 已从 DashScope/百炼环境变量映射 OPENAI_API_KEY，"
        "并补齐 OPENAI_BASE_URL / BROWSER_USE_LLM_MODEL（若先前未设置），供 browser-use MCP 使用。"
    )


_hydrate_llm_env_for_browser_use()

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent
except ImportError:
    print("缺少 mcp SDK，请安装: pip install mcp>=1.0.0")
    sys.exit(1)

DEFAULT_URL = "https://www.kalaroko.com/"
DEFAULT_CDP_HTTP = "http://127.0.0.1:9222"
# browser-use 默认 TIMEOUT_NavigateToUrlEvent=30，SPA 切 Tab 时易误超时并写入 errors 列表
DEFAULT_NAV_EVENT_TIMEOUT_SEC = 60.0

_VERDICT_RE = re.compile(
    r"^\s*VERDICT\s*:\s*(PASS|FAIL|BLOCKED|SKIP|UNKNOWN)\s*$",
    re.I | re.MULTILINE,
)


@dataclass
class P0Case:
    """与《K11 平台冒烟》P0 条目对齐的 Agent 任务定义。"""

    key: str
    name: str
    requirement: str
    instruction: str


def _p0_cases(base_url: str) -> list[P0Case]:
    """用自然语言驱动 browser-use Agent；base_url 写入每条任务避免跑偏。"""
    u = base_url.strip()
    footer = (
        f"\n\n【站点】仅针对 {u} 。\n"
        "【输出要求】最后一行必须且仅能是一行结论，格式严格为：VERDICT: PASS 或 VERDICT: FAIL 或 VERDICT: BLOCKED 或 VERDICT: SKIP。\n"
        "- PASS：满足验收描述且无阻断主流程的问题。\n"
        "- FAIL：明确不符合验收（白屏、崩溃、Tab 无法切换、点击无响应等）。\n"
        "- BLOCKED：环境/登录/Captcha/权限导致无法完成验证。\n"
        "- SKIP：因时间或步骤过多未跑完，但已说明原因与已覆盖范围。\n"
        "在此前用中文分点简要写：操作步骤摘要、观察到的现象、若打开开发者工具则 Console 严重报错摘要（读不到 Console 请写明「Console 未直接读取」）。"
    )

    return [
        P0Case(
            key="homepage_load",
            name="首页加载",
            requirement="首页首屏正常展示，无白屏、无崩溃；环境可访问",
            instruction=(
                f"打开 {u} ，等待首屏稳定渲染。"
                "检查：是否长时间白屏、整页无内容、浏览器或页面明显崩溃提示、首屏是否有可见可交互内容（卡片/按钮/导航）。"
                "若存在明显加载失败或空白，记录现象。"
            )
            + footer,
        ),
        P0Case(
            key="category_tabs",
            name="分类切换",
            requirement="All / 1 vs 1 / Party / Live 可正常点击切换，不可因切换导致不可发布级阻断",
            instruction=(
                f"在 {u} 首页或主导航区域，找到分类或 Tab：All、1 vs 1（或 1v1）、Party、Live（名称可能大小写或空格略有不同，请模糊匹配）。"
                "依次点击切换（若某项不存在则说明并继续其余项）。"
                "每次切换后确认：内容区是否有更新、是否卡死、是否出现错误页；切换链路是否可用于主流程。"
            )
            + footer,
        ),
        P0Case(
            key="key_card_click",
            name="关键卡片点击",
            requirement="至少 1 个核心游戏卡片点击后可进入下一层页面/流程；或记录影响主流程的 P1 问题",
            instruction=(
                f"在 {u} 首屏或推荐位，识别「核心游戏」或主推荐卡片（非页脚次要链接）。"
                "点击其中至少一张卡片，观察是否进入游戏详情、大厅、或启动游戏的下一层流程。"
                "若进入 404、无限加载、或明显阻断主流程的弹窗/错误，记为问题并说明是否属 P1 级。"
                "若页面结构无法识别卡片，说明 DOM 观察结果。"
            )
            + footer,
        ),
        P0Case(
            key="games_playable",
            name="各游戏正常运行",
            requirement="尽量验证各游戏可开局并完成一局；诚实报告未完成项",
            instruction=(
                f"在 {u} 上，列出当前可见的主要游戏入口（可滚动加载更多，但控制总步数）。"
                "对至少 2～3 个不同游戏尝试：进入 → 开始一局 → 玩到可判断「能玩」为止（完成整局若过长可在一局进行中确认无致命错误即停止并说明）。"
                "若某游戏无法开局，记录名称与错误表现。"
                "若游戏数量很多，明确写出「已抽样哪些、未覆盖哪些」，不要用 PASS 冒充全量通过。"
            )
            + footer,
        ),
        P0Case(
            key="release_regression",
            name="本次更新点验证",
            requirement="本次上线涉及功能尽量验证；关注 Console 报错",
            instruction=(
                f"在 {u} 浏览与「本次上线」相关的主要路径（首页、分类、游戏入口、关键弹窗）。"
                "若你的自动化能力可以打开开发者工具并读取 Console，请汇总严重 error（忽略无害第三方）；"
                "若无法读取 Console，必须写明「Console 未直接读取」，并改为根据页面内错误提示、网络失败提示、明显功能缺失做判断。"
                "不要编造不存在的报错内容。"
            )
            + footer,
        ),
    ]


def _text_from_result(result: Any) -> str:
    parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, TextContent):
            parts.append(block.text or "")
        elif hasattr(block, "text"):
            parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(parts).strip()


def _parse_verdict(text: str) -> str:
    m = _VERDICT_RE.search(text or "")
    if m:
        return m.group(1).upper()
    return "UNKNOWN"


def _pick_tool(names: set[str], *candidates: str) -> str | None:
    for c in candidates:
        if c in names:
            return c
    lower = {n.lower(): n for n in names}
    for c in candidates:
        key = c.lower()
        if key in lower:
            return lower[key]
    for n in sorted(names):
        for c in candidates:
            if c.lower() in n.lower():
                return n
    return None


def _task_argument_key(tools_list: Any, agent_tool: str) -> str:
    """根据 tools/list 的 inputSchema 选择 Agent 任务参数字段名。"""
    for t in tools_list.tools:
        if t.name != agent_tool:
            continue
        schema = getattr(t, "inputSchema", None)
        if isinstance(schema, dict):
            props = schema.get("properties")
            if isinstance(props, dict):
                for k in (
                    "task",
                    "goal",
                    "instruction",
                    "prompt",
                    "query",
                    "message",
                    "user_request",
                ):
                    if k in props:
                        return k
        break
    return "task"


def _cdp_http_candidates(user_url: str) -> list[str]:
    """同一端口下尝试 127.0.0.1 与 localhost（部分环境只对其中之一监听）。"""
    base = (user_url or "").strip().rstrip("/")
    if not base:
        return []
    try:
        p = urlparse(base)
    except Exception:
        return [base]
    if p.scheme not in ("http", "https") or not p.hostname or p.port is None:
        return [base]
    hosts: list[str] = [p.hostname]
    hlow = p.hostname.lower()
    if hlow == "127.0.0.1":
        hosts.append("localhost")
    elif hlow == "localhost":
        hosts.append("127.0.0.1")
    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        netloc = f"{h}:{p.port}"
        u = urlunparse((p.scheme, netloc, "", "", "", "")).rstrip("/")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _cdp_http_probe(
    http_url: str, *, timeout_sec: float = 3.0
) -> tuple[bool, str, str | None]:
    """
    探测 DevTools HTTP 是否可用。返回 (是否成功, 失败时的简要原因, 实际连上的 base URL)。
    使用无代理 Opener：系统 HTTP_PROXY 常把 127.0.0.1 交给代理，导致本机调试端口「假死」。
    """
    last_err = ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for base in _cdp_http_candidates(http_url):
        ver = f"{base}/json/version"
        try:
            with opener.open(ver, timeout=timeout_sec) as r:
                if r.status == 200:
                    return True, "", base
                last_err = f"HTTP {r.status} @ {ver}"
        except urllib.error.HTTPError as e:
            last_err = f"{type(e).__name__} {e.code} @ {ver}: {e.reason}"
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = f"{type(e).__name__} @ {ver}: {e}"
    return False, last_err or "未知错误", None


def _write_temp_browser_use_config_for_cdp(cdp_http: str) -> Path:
    """
    browser-use MCP 从 config.json 的 browser_profile 读取 cdp_url；
    传入 http://host:port 时，BrowserSession.connect 会拉取 webSocketDebuggerUrl。
    """
    pid, lid, aid = str(uuid4()), str(uuid4()), str(uuid4())
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cfg = {
        "browser_profile": {
            pid: {
                "id": pid,
                "default": True,
                "created_at": ts,
                "headless": False,
                "is_local": False,
                "cdp_url": cdp_http.strip().rstrip("/"),
                "keep_alive": True,
            }
        },
        "llm": {
            lid: {
                "id": lid,
                "default": True,
                "created_at": ts,
                "model": "gpt-4.1-mini",
                "api_key": None,
            }
        },
        "agent": {
            aid: {
                "id": aid,
                "default": True,
                "created_at": ts,
            }
        },
    }
    fd, path = tempfile.mkstemp(prefix="jachin-browser-use-", suffix="-config.json", text=True)
    os.close(fd)
    p = Path(path)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


async def _call_agent(
    session: ClientSession,
    *,
    tools_list: Any,
    agent_tool: str,
    task_body: str,
    timeout_sec: float,
) -> tuple[str, str | None]:
    """返回 (raw_text, error_message)。"""
    key = _task_argument_key(tools_list, agent_tool)
    try:
        out = await asyncio.wait_for(
            session.call_tool(agent_tool, {key: task_body}),
            timeout=timeout_sec,
        )
        return _text_from_result(out), None
    except asyncio.TimeoutError:
        return "", "TIMEOUT"
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


async def run_suite(
    *,
    url: str,
    list_tools_only: bool,
    single_agent_task: str | None,
    include_screenshot: bool,
    mcp_command: str,
    case_keys: set[str] | None,
    timeout_per_case: float,
    json_report: Path | None,
    spawn_browser: bool,
    cdp_http: str,
    skip_cdp_check: bool,
    nav_event_timeout_sec: float,
) -> int:
    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "[FAIL] 未设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY。\n"
            "       可在仓库根 .env 或 ~/.jachin/.env 中配置。"
        )
        return 1

    exe = mcp_command.strip() or "uvx"
    if shutil.which(exe) is None and not Path(exe).is_file():
        print(f"[FAIL] 找不到可执行文件: {exe!r}，请 pip install uv 或指定 --mcp-command 绝对路径")
        return 1

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # 见 browser_use.browser.events.NavigateToUrlEvent → TIMEOUT_NavigateToUrlEvent
    if nav_event_timeout_sec > 0:
        env.setdefault("TIMEOUT_NavigateToUrlEvent", str(float(nav_event_timeout_sec)))

    config_path: Path | None = None
    # 仅 list_tools 时不注入 CDP（无需浏览器）；跑用例时必须能连上调试端口
    if not spawn_browser and not list_tools_only:
        cdp_u = (cdp_http or "").strip() or DEFAULT_CDP_HTTP
        if not skip_cdp_check:
            ok, diag, effective = _cdp_http_probe(cdp_u)
            if not ok:
                print(
                    f"[FAIL] 无法访问 Chrome DevTools（已尝试绕过 HTTP 代理，并在 127.0.0.1 / localhost 间回退）。\n"
                    f"       探测: {cdp_u.rstrip('/')}/json/version\n"
                    f"       原因: {diag}\n"
                    "       常见情况：① Chrome 未带 --remote-debugging-port 启动（仅普通双击图标不会开 9222）；\n"
                    "       ② 端口不是 9222，请改 --cdp-http；③ 公司代理干扰时可设环境变量 NO_PROXY=127.0.0.1,localhost\n"
                    "       启动示例：\"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" "
                    "--remote-debugging-port=9222 --user-data-dir=%TEMP%\\chrome-mcp-debug\n"
                    "       若确定端口已通：加 --skip-cdp-check"
                )
                return 1
            if effective and effective.rstrip("/") != cdp_u.rstrip("/"):
                print(f"[INFO] DevTools 在 {effective} 响应，已用该地址作为 cdp_url。")
            cdp_u = effective or cdp_u
        config_path = _write_temp_browser_use_config_for_cdp(cdp_u)
        env["BROWSER_USE_CONFIG_PATH"] = str(config_path.resolve())
        atexit.register(lambda p=str(config_path): Path(p).unlink(missing_ok=True))

    params = StdioServerParameters(
        command=exe,
        args=["--from", "browser-use[cli]", "browser-use", "--mcp"],
        env=env,
    )

    print("=== Browser-Use MCP · P0 冒烟（Agent 执行）===")
    print(f"MCP: {exe} --from browser-use[cli] browser-use --mcp")
    if spawn_browser:
        print("浏览器: 由 browser-use 自行启动（--spawn-browser）")
    elif list_tools_only:
        print("浏览器: 仅列工具，未注入 CDP（完整跑测时请保持 Chrome --remote-debugging-port 已开启）")
    else:
        print(f"浏览器: 附加到已开启远程调试的 Chrome（cdp_http={ (cdp_http or '').strip() or DEFAULT_CDP_HTTP }）")
    print(f"站点: {url}")
    if nav_event_timeout_sec > 0 and env.get("TIMEOUT_NavigateToUrlEvent"):
        print(
            f"[INFO] NavigateToUrl 事件超时: {env['TIMEOUT_NavigateToUrlEvent']}s"
            "（browser-use bubus；可调 --nav-event-timeout 或环境变量 TIMEOUT_NavigateToUrlEvent）"
        )
    print()

    report_rows: list[dict[str, Any]] = []

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}

                if list_tools_only:
                    for n in sorted(names):
                        print(f"  - {n}")
                    return 0

                nav = _pick_tool(names, "browser_navigate", "mcp_browser_navigate")
                gstate = _pick_tool(names, "browser_get_state", "mcp_browser_get_state")
                agent_tool = _pick_tool(
                    names,
                    "retry_with_browser_use_agent",
                    "browser_use_agent",
                )

                if not nav:
                    print(f"[FAIL] 未找到 browser_navigate。工具: {sorted(names)}")
                    return 1

                print(f"[OK] 预导航 {nav} -> {url}")
                await session.call_tool(nav, {"url": url.strip()})

                if gstate and include_screenshot:
                    await session.call_tool(gstate, {"include_screenshot": True})

                # 单条自定义任务（旧 CLI）
                if single_agent_task:
                    if not agent_tool:
                        print(f"[FAIL] 未找到 Agent 工具。当前: {sorted(names)}")
                        return 1
                    print(f"\n--- 单次 Agent: {agent_tool} ---")
                    raw, err = await _call_agent(
                        session,
                        tools_list=listed,
                        agent_tool=agent_tool,
                        task_body=single_agent_task,
                        timeout_sec=timeout_per_case,
                    )
                    if err:
                        print(f"[FAIL] {err}")
                        return 1
                    print(raw[:8000] or "(空)")
                    vd = _parse_verdict(raw)
                    print(f"\n解析 VERDICT: {vd}")
                    return 0 if vd == "PASS" else 1

                if not agent_tool:
                    print(
                        f"[FAIL] P0 套件需要自主 Agent 工具，但未找到（如 retry_with_browser_use_agent）。"
                        f" 当前: {sorted(names)}"
                    )
                    return 1

                cases = _p0_cases(url)
                if case_keys:
                    cases = [c for c in cases if c.key in case_keys]
                    if not cases:
                        print(f"[FAIL] --cases 未匹配任何 key，可选: {[c.key for c in _p0_cases(url)]}")
                        return 1

                print(f"将执行 {len(cases)} 条 P0，Agent 工具: {agent_tool}，单条超时 {timeout_per_case}s\n")

                for c in cases:
                    print(f"────────── {c.key} · {c.name} ──────────")
                    t0 = time.perf_counter()
                    raw, err = await _call_agent(
                        session,
                        tools_list=listed,
                        agent_tool=agent_tool,
                        task_body=c.instruction,
                        timeout_sec=timeout_per_case,
                    )
                    elapsed = time.perf_counter() - t0
                    verdict = "ERROR" if err else _parse_verdict(raw)
                    if err:
                        raw_out = err
                    else:
                        raw_out = raw
                        if verdict == "UNKNOWN":
                            print("[WARN] 未解析到 VERDICT 行，请人工看下方原文")

                    preview = (raw_out or "")[:3500]
                    print(preview)
                    if len(raw_out or "") > 3500:
                        print(f"... 原文共 {len(raw_out)} 字符，已截断")
                    print(f"→ 判定: {verdict}（耗时 {elapsed:.1f}s）\n")

                    report_rows.append(
                        {
                            "key": c.key,
                            "name": c.name,
                            "requirement": c.requirement,
                            "verdict": verdict,
                            "elapsed_sec": round(elapsed, 2),
                            "error": err,
                            "raw_excerpt": (raw_out or "")[:12000],
                        }
                    )

                # 汇总表
                print("=== P0 汇总 ===")
                print(f"{'用例key':<20} {'名称':<14} {'结果':<10} {'说明'}")
                for row in report_rows:
                    note = row["requirement"][:40] + "…" if len(row["requirement"]) > 40 else row["requirement"]
                    print(f"{row['key']:<20} {row['name']:<14} {row['verdict']:<10} {note}")

                bad = {r["verdict"] for r in report_rows} & {"FAIL", "BLOCKED", "ERROR", "UNKNOWN"}
                all_pass = all(r["verdict"] == "PASS" for r in report_rows)

                if json_report:
                    payload = {
                        "site": url,
                        "agent_tool": agent_tool,
                        "timeout_per_case_sec": timeout_per_case,
                        "spawn_browser": spawn_browser,
                        "cdp_http": None if spawn_browser else ((cdp_http or "").strip() or DEFAULT_CDP_HTTP),
                        "cases": report_rows,
                        "summary_all_pass": all_pass,
                    }
                    json_report.parent.mkdir(parents=True, exist_ok=True)
                    json_report.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"\n[OK] JSON 报告已写入: {json_report}")

                if bad:
                    print(f"\n[WARN] 存在需关注结果: {bad}，退出码 1")
                    return 1
                print("\n[OK] 全部用例 VERDICT 均为 PASS（仍建议人工 spot-check）。")
                return 0

    except FileNotFoundError as e:
        print(f"[FAIL] 无法启动子进程: {e}")
        return 1
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return 1


def main() -> int:
    all_keys = {c.key for c in _p0_cases(DEFAULT_URL)}
    parser = argparse.ArgumentParser(
        description="Browser-Use MCP：P0 冒烟（Agent 为手脚）",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="被测站点根 URL")
    parser.add_argument("--list-tools-only", action="store_true")
    parser.add_argument(
        "--agent-task",
        default="",
        help="仅执行这一条自然语言任务（不跑 P0 套件）",
    )
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument(
        "--mcp-command",
        default=os.environ.get("BROWSER_USE_MCP_COMMAND", "uvx"),
    )
    parser.add_argument(
        "--cases",
        default="",
        help=f"逗号分隔子集，可选 key: {','.join(sorted(all_keys))}",
    )
    parser.add_argument(
        "--timeout-per-case",
        type=float,
        default=600.0,
        help="单条 Agent 调用超时（秒），默认 600",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="写入完整 JSON 报告路径",
    )
    parser.add_argument(
        "--spawn-browser",
        action="store_true",
        help="不由本机已打开的 Chrome 调试端口附加，改由 browser-use 自行启动浏览器（旧行为）",
    )
    parser.add_argument(
        "--cdp-http",
        default=os.environ.get("BROWSER_USE_CDP_HTTP", DEFAULT_CDP_HTTP),
        help=f"已启动 Chrome 的 DevTools HTTP 基址（默认 {DEFAULT_CDP_HTTP}，可与 --remote-debugging-port 一致）",
    )
    parser.add_argument(
        "--skip-cdp-check",
        action="store_true",
        help="跳过启动前对 /json/version 的探测（端口转发等场景）",
    )
    parser.add_argument(
        "--nav-event-timeout",
        type=float,
        default=float(os.environ.get("JACHIN_NAV_EVENT_TIMEOUT", str(DEFAULT_NAV_EVENT_TIMEOUT_SEC))),
        help=(
            f"注入 TIMEOUT_NavigateToUrlEvent（秒），减轻 SPA 切 Tab 时默认 30s bubus 超时记入 Errors；"
            f"默认 {DEFAULT_NAV_EVENT_TIMEOUT_SEC}，设 0 表示不注入"
        ),
    )
    args = parser.parse_args()

    case_keys: set[str] | None = None
    if args.cases.strip():
        case_keys = {x.strip() for x in args.cases.split(",") if x.strip()}

    return asyncio.run(
        run_suite(
            url=args.url.strip(),
            list_tools_only=args.list_tools_only,
            single_agent_task=(args.agent_task.strip() or None),
            include_screenshot=args.screenshot,
            mcp_command=args.mcp_command,
            case_keys=case_keys,
            timeout_per_case=max(30.0, args.timeout_per_case),
            json_report=args.json_report,
            spawn_browser=args.spawn_browser,
            cdp_http=str(args.cdp_http or "").strip(),
            skip_cdp_check=args.skip_cdp_check,
            nav_event_timeout_sec=max(0.0, float(args.nav_event_timeout)),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
