#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 P1 六条子模块验收：L3 读取同目录 SKILL Markdown，调度 browser-use MCP。

业务逻辑唯一来源：``skills_repo/k11-herontest-browser-qa/SKILL_P1_MODULES.md``。

**与 ``test_kalaroko_default_scenarios_e2e.py`` 完全相同的 CDP 方式**：仅使用环境变量 ``KALAROKO_CDP_ENDPOINT``（与 ``l3_client/local_mcps/kalaroko_monitor/mcp_kalaroko_monitor._kalaroko_cdp_endpoint`` 一致：无 scheme 时自动加 ``http://``）。须在仓库根 ``.env`` 中配置，且 Chrome 已用 ``--remote-debugging-port`` 启动（例如先跑 ``.\scripts\launch_chrome_debug.ps1``）。脚本写配置前探测 ``/json/version``；可加 ``--auto-launch-chrome-debug`` 在 Windows 上代调该 PS1。

**不自行打开网站（验收语义）**：假定机器人已在**调试 Chrome**里摆好当前标签页；脚本不会替你 navigate 到基准站。
仅靠 SKILL 禁止 navigate **不能**阻止 browser-use 默认再拉起 Chrome；必须在 **L3 使用的** ``~/.jachin/mcp_servers.json`` 里为 ``browser-use`` 设置 ``BROWSER_USE_CONFIG_PATH``（指向含 ``cdp_url`` 的 JSON），**并重启 L3**。本脚本默认在 ``~/.jachin/runtime/browser-use-attach-cdp.json`` 写入该配置；可用 ``--apply-mcp-browser-use-cdp`` 自动合并到 mcp_servers.json。

用法（仓库根）：
  python scripts/run_k11_p1_modules_l3.py
  python scripts/run_k11_p1_modules_l3.py --no-auto-patch-mcp
  python scripts/run_k11_p1_modules_l3.py --apply-mcp-browser-use-cdp
  python scripts/run_k11_p1_modules_l3.py --cases p1_customer_service,p1_share_tab
  python scripts/run_k11_p1_modules_l3.py --log-file logs/k11_p1_last.log --dump-prompt logs/k11_p1_prompt.txt
  python scripts/run_k11_p1_modules_l3.py --quiet
  python scripts/run_k11_p1_modules_l3.py --smoke-diag-dir D:\\zzz\\jachin\\冒烟   # 或默认 Windows 即此路径；另设 JACHIN_K11_SMOKE_DIAG_DIR
  python scripts/run_k11_p1_modules_l3.py --no-smoke-diag   # 不写诊断包

环境（排障时可对照日志中的 [env] 行）：
  JACHIN_L3_HTTP_BASE、K11_BROWSER_CONTEXT_URL、BROWSER_USE_CONFIG_PATH

CDP **只读** ``KALAROKO_CDP_ENDPOINT``（与 E2E / Kalaroko Monitor MCP 同源）；可选 ``--cdp-http`` 仅本次覆盖该值。预检：``/json/list`` + 可选 Playwright ``connect_over_cdp(endpoint)``（endpoint 即上式），写入 L3 prompt。

**默认行为**：写入附加 JSON 后，若存在 ``~/.jachin/mcp_servers.json``，会自动把 **绝对路径** 写入 browser-use 的 ``env.BROWSER_USE_CONFIG_PATH``（与 ``--apply-mcp-browser-use-cdp`` 相同，幂等）。若文件中**尚无** browser-use 条目，会按 ``config/mcp_servers.json.example`` **追加一条**（``uvx … browser-use --mcp``），以便与 **jachin-puppeteer-cdp（主轨）** 形成「确定性 + Agent 辅轨」组合；不需要追加时用 ``--no-append-browser-use-mcp``。**推荐**从配置中移除 **official-mcp-puppeteer**（会 launch 第二套 Chromium）。**修改后必须重启 L3**，否则已运行的 MCP 子进程仍用旧 env → ``about:blank``。

**L2 白名单**：若已配对且 ``l2_gateway_config.json`` 里 ``permissions_snapshot.allowed_skills`` 非空，未列名的 ``mcp:…`` 工具会被剔除，Agent 可能「只有 PowerPoint 等少数 MCP」。在 **L3 进程** 环境设置 ``JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL=1`` 并重启，可并入 ``mcp:*`` 放行本地已注册的 MCP（见 ``l3_node/primitives/tools/tool_pool.py``）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 与 test_kalaroko_default_scenarios_e2e.py 一致：先 merge 仓库根 .env（CHROME_* / KALAROKO_* 才进 os.environ）
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _reload_dotenv() -> None:
    """main 内再次加载：仓库根 + ~/.jachin/.env（后者不覆盖已有键）。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", encoding="utf-8")
        load_dotenv(Path.home() / ".jachin" / ".env", encoding="utf-8")
    except ImportError:
        pass
    except OSError:
        pass


DEFAULT_SKILL = ROOT / "skills_repo" / "k11-herontest-browser-qa" / "SKILL_P1_MODULES.md"
DEFAULT_L3_BASE = os.environ.get("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991")
DEFAULT_CONTEXT_URL = os.environ.get("K11_BROWSER_CONTEXT_URL", "https://www.kalaroko.com/")


def _stable_browser_use_cdp_config_path() -> Path:
    return Path.home() / ".jachin" / "runtime" / "browser-use-attach-cdp.json"


def _write_browser_use_cdp_config_json(*, cdp_http: str, dest: Path) -> Path:
    """与 scripts/test_browser_use_mcp._write_temp_browser_use_config_for_cdp 同结构，写入固定路径供 BROWSER_USE_CONFIG_PATH 引用。"""
    pid, lid, aid = str(uuid4()), str(uuid4()), str(uuid4())
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    # 勿写 user_data_dir: null：browser-use 读配置时 model_dump(exclude_none=True) 会丢掉该键，
    # MCP 侧默认合并会带上 ~/.config/browseruse 的 user_data_dir，干扰「仅 CDP 附加」语义。
    cfg = {
        "browser_profile": {
            pid: {
                "id": pid,
                "default": True,
                "created_at": ts,
                "headless": False,
                "is_local": False,
                "cdp_url": (cdp_http or "").strip().rstrip("/"),
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
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest.resolve()


def _cdp_http_candidates(user_url: str) -> list[str]:
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


def _cdp_http_probe(http_url: str, *, timeout_sec: float = 3.0) -> tuple[bool, str, str | None]:
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


def _kalaroko_cdp_endpoint(*, cli_override: str | None) -> str | None:
    """
    与 ``mcp_kalaroko_monitor._kalaroko_cdp_endpoint`` / E2E 完全一致：
    只认 ``KALAROKO_CDP_ENDPOINT``（``--cdp-http`` 视为单次覆盖写入等价变量）。
    未设置时返回 None；无 scheme 时前缀 ``http://``。
    """
    raw = (cli_override or "").strip() if cli_override else ""
    if not raw:
        raw = (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
    if not raw:
        return None
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = "http://" + raw.lstrip("/")
    return raw


def _run_launch_chrome_debug_ps1(*, repo_root: Path, open_url: str, log: Callable[..., None]) -> bool:
    """调用 launch_chrome_debug.ps1（与 E2E 人工前置相同）。"""
    ps1 = repo_root / "scripts" / "launch_chrome_debug.ps1"
    if not ps1.is_file():
        log(f"[chrome] 未找到 {ps1}", err=True, force=True)
        return False
    cmd: list[str] = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ps1.resolve()),
    ]
    ou = (open_url or "").strip()
    if ou:
        cmd.append(ou)
    try:
        r = subprocess.run(
            cmd,
            cwd=str(repo_root.resolve()),
            timeout=90,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if (r.stdout or "").strip():
            log(f"[chrome] stdout: {(r.stdout or '').strip()[:600]}", force=True)
        if (r.stderr or "").strip():
            log(f"[chrome] stderr: {(r.stderr or '').strip()[:600]}", err=True, force=True)
        if r.returncode != 0:
            log(f"[chrome] launch_chrome_debug.ps1 退出码={r.returncode}", err=True, force=True)
            return False
        log("[chrome] 已执行 launch_chrome_debug.ps1（Start-Process 调试 Chrome）", force=True)
        return True
    except Exception as e:
        log(f"[chrome] 执行失败: {type(e).__name__}: {e}", err=True, force=True)
        return False


def _ensure_devtools_http(
    cdp_http: str,
    log: Callable[..., None],
    *,
    auto_launch: bool,
    open_url: str,
    repo_root: Path,
) -> bool:
    """
    与 E2E 一致：必须先能访问 DevTools HTTP（通常来自 launch_chrome_debug.ps1 起的 Chrome）。
    不可达时打印明确前置；可选在 Windows 上自动跑 PS1 并重试探测。
    """
    ok, err, base = _cdp_http_probe(cdp_http, timeout_sec=4.0)
    if ok:
        log(f"[cdp][gate] DevTools 就绪 {base}/json/version", force=True)
        return True
    log(
        f"[cdp][gate][ERROR] 无法连接 {cdp_http!r}: {err}",
        err=True,
        force=True,
    )
    log(
        "[cdp][gate] 前置（与 test_kalaroko_default_scenarios_e2e.py 相同）：\n"
        "  1) 在仓库根执行（会先开调试专用 Chrome，**不是**任务栏日常实例）：\n"
        "       .\\scripts\\launch_chrome_debug.ps1\n"
        "     或直接打开产品页再测：\n"
        f"       .\\scripts\\launch_chrome_debug.ps1 \"{(open_url or 'https://www.kalaroko.com/').strip()}\"\n"
        "  2) 在仓库根 .env 设置 KALAROKO_CDP_ENDPOINT=...（HTTP 根须与 Chrome --remote-debugging-port 一致，与 E2E 相同）。\n"
        "  3) 可加 --auto-launch-chrome-debug（或 K11_AUTO_LAUNCH_CHROME_DEBUG=1）由脚本代调 PS1。\n"
        "  说明：日常 Chrome 未开远程调试时，Playwright connect_over_cdp 无法挂接。",
        force=True,
    )
    if not auto_launch or sys.platform != "win32":
        return False
    log("[cdp][gate] 尝试 --auto-launch-chrome-debug …", force=True)
    if not _run_launch_chrome_debug_ps1(repo_root=repo_root, open_url=open_url, log=log):
        return False
    time.sleep(3.0)
    last_err = err
    for attempt in range(1, 10):
        ok2, last_err, base2 = _cdp_http_probe(cdp_http, timeout_sec=3.0)
        if ok2:
            log(f"[cdp][gate] 自动拉起后 DevTools 就绪（第 {attempt} 次探测）{base2}/json/version", force=True)
            return True
        time.sleep(1.2)
    log(f"[cdp][gate][ERROR] 自动拉起后仍不可达: {last_err}", err=True, force=True)
    return False


def _cdp_fetch_json_list_pages(
    http_base: str, log: Callable[..., None], *, max_pages: int = 16
) -> tuple[list[dict[str, Any]], str | None]:
    """
    GET /json/list，提取 type==page 的项（url / title / id）。返回 (pages, devtools_base_used)。
    """
    ok, err, base = _cdp_http_probe(http_base, timeout_sec=4.0)
    if not ok or not base:
        log(f"[cdp][json/list] 跳过：DevTools 不可达 {http_base!r}: {err}", err=True, force=True)
        return [], None
    url = f"{base}/json/list"
    try:
        raw = (
            urllib.request.build_opener(urllib.request.ProxyHandler({}))
            .open(url, timeout=6.0)
            .read()
            .decode("utf-8", errors="replace")
        )
        data = json.loads(raw)
    except Exception as e:
        log(f"[cdp][json/list] 读取失败 {url}: {e}", err=True, force=True)
        return [], base
    if not isinstance(data, list):
        log("[cdp][json/list] 响应非数组", err=True, force=True)
        return [], base
    pages: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") != "page":
            continue
        pages.append(item)
    if len(pages) > max_pages:
        pages = pages[:max_pages]
    log(
        f"[cdp][json/list] DevTools={base} page型条目={len(pages)}（至多展示 {max_pages}）",
        force=True,
    )
    for i, p in enumerate(pages):
        u = str(p.get("url") or "")
        ti = str(p.get("title") or "")
        pid = str(p.get("id") or "")[:12]
        log(f"  [tab {i}] id~{pid} title={ti[:80]!r} url={u[:120]!r}", force=True)
    return pages, base


def _playwright_cdp_page_snapshot(cdp_http: str, log: Callable[..., None]) -> tuple[str | None, str | None, str | None]:
    """
    与 kalaroko_capture_page_metrics._pick_cdp_page 一致：connect_over_cdp 后从后往前选第一个可 evaluate 的页。
    返回 (url, title, error_message)。error 非空表示跳过或失败。
    """
    try:
        import asyncio

        from playwright.async_api import async_playwright
    except ImportError:
        msg = "未安装 playwright（可选：pip install playwright && playwright install chromium）"
        log(f"[cdp][playwright] {msg}", force=True)
        return None, None, msg

    async def _run() -> tuple[str | None, str | None, str | None]:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp_http)
                if not browser.contexts:
                    return None, None, "connect_over_cdp 成功但 browser.contexts 为空"
                ctx = browser.contexts[0]
                pgs = list(getattr(ctx, "pages", []) or [])

                async def _alive(pg: Any) -> bool:
                    try:
                        if pg.is_closed():
                            return False
                        await asyncio.wait_for(pg.evaluate("() => 1"), timeout=2.5)
                        return True
                    except Exception:
                        return False

                picked = None
                for idx in range(len(pgs) - 1, -1, -1):
                    if await _alive(pgs[idx]):
                        picked = pgs[idx]
                        break
                if picked is None and pgs:
                    picked = pgs[-1]
                if picked is None:
                    return None, None, "context.pages 为空（无标签页）"
                url = picked.url or ""
                title = await asyncio.wait_for(picked.title(), timeout=4.0)
                return url, title, None
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"

    try:
        url, title, err = asyncio.run(_run())
        if err:
            log(f"[cdp][playwright] connect_over_cdp 快照失败: {err}", err=True, force=True)
        else:
            log(
                f"[cdp][playwright] 当前可驱动页: url={url[:200]!r} title={title[:120]!r}",
                force=True,
            )
        return url, title, err
    except RuntimeError as e:
        # 嵌套 asyncio 等
        log(f"[cdp][playwright] {e}", err=True, force=True)
        return None, None, str(e)


def _format_cdp_preflight_for_prompt(
    *,
    cdp_http: str,
    devtools_base: str | None,
    json_pages: list[dict[str, Any]],
    pw_url: str | None,
    pw_title: str | None,
    pw_err: str | None,
    playwright_skipped: bool = False,
) -> str:
    """写入 user_input，让 L3 知道本机 CDP 上已有真实页面（与 E2E 同源）。"""
    lines = [
        "【本脚本 CDP 预检 · 与 Kalaroko E2E 同源（Playwright connect_over_cdp + DevTools /json/list）】",
        f"- 配置的 DevTools HTTP 基址：`{cdp_http}`",
    ]
    if devtools_base:
        lines.append(f"- 探测到的基址：`{devtools_base}`")
    if json_pages:
        lines.append(f"- `/json/list` 中 page 型标签（前 {len(json_pages)} 个，供对照；**不是**要你 navigate）：")
        for i, p in enumerate(json_pages[:12]):
            u = str(p.get("url") or "")
            ti = str(p.get("title") or "")
            lines.append(f"  - tab[{i}] title={ti[:100]!r} url={u[:160]!r}")
    else:
        lines.append("- `/json/list`：未拿到 page 条目（Chrome 可能未开或端口不对）。")
    if playwright_skipped:
        lines.append("- Playwright 快照：**已跳过**（`--no-cdp-playwright-verify`）。")
    elif pw_err:
        lines.append(f"- Playwright 快照：**未执行或失败** — {pw_err[:300]}")
    elif pw_url is not None:
        lines.append(
            f"- Playwright **当前可驱动页**（与自动化读同一 CDP）：url=`{pw_url[:220]}` title=`{pw_title or ''}`"
        )
    lines.append(
        "- 若 L3 工具仍报 about:blank：优先查 **browser-use / MCP 是否附加同一 CDP**（重启 L3、BROWSER_USE_CONFIG_PATH），"
        "并确认用的是 **launch_chrome_debug.ps1（或等价 --remote-debugging-port）起的调试 Chrome**，而非未暴露 CDP 的日常实例。"
    )
    return "\n".join(lines) + "\n"


def _log_dotenv_sources(log: Callable[..., None]) -> None:
    r = ROOT / ".env"
    j = Path.home() / ".jachin" / ".env"
    log(
        f"[diag][dotenv] 仓库根 .env exists={r.is_file()} path={r.resolve()}\n"
        f"[diag][dotenv] ~/.jachin/.env exists={j.is_file()} path={j.resolve()}",
        force=True,
    )


def _summarize_attach_json(path: Path, log: Callable[..., None]) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as e:
        log(f"[diag][attach-json] 读取失败: {e}", err=True, force=True)
        return
    except json.JSONDecodeError as e:
        log(f"[diag][attach-json] JSON 无效: {e}", err=True, force=True)
        return
    bp = data.get("browser_profile")
    if not isinstance(bp, dict):
        log("[diag][attach-json] 缺少 browser_profile 对象", force=True)
        return
    urls: list[str] = []
    for _k, prof in bp.items():
        if isinstance(prof, dict):
            u = prof.get("cdp_url")
            if isinstance(u, str) and u.strip():
                urls.append(u.strip())
    try:
        sz = path.stat().st_size
    except OSError:
        sz = -1
    log(
        f"[diag][attach-json] file={path.resolve()} size_bytes={sz} "
        f"cdp_urls={urls} profiles={len(bp)}",
        force=True,
    )


def _log_mcp_browser_use_bu_path(log: Callable[..., None], *, phase: str) -> None:
    p = Path.home() / ".jachin" / "mcp_servers.json"
    if not p.is_file():
        log(f"[diag][mcp][{phase}] 无文件: {p}", force=True)
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8").lstrip("\ufeff"))
    except Exception as e:
        log(f"[diag][mcp][{phase}] 解析失败: {e}", err=True, force=True)
        return
    _root, servers = _mcp_servers_root_and_list(data)
    if servers is None:
        log(
            f"[diag][mcp][{phase}] 根格式非 {{\"mcp_servers\":[...]}} 也非数组 [...]，无法枚举条目",
            force=True,
        )
        return
    log(
        f"[diag][mcp][{phase}] 根形态={'array' if isinstance(data, list) else 'object+mcp_servers'} "
        f"条目数={len(servers)}",
        force=True,
    )
    for i, e in enumerate(servers):
        if not isinstance(e, dict) or not _looks_like_browser_use_entry(e):
            continue
        env = e.get("env") if isinstance(e.get("env"), dict) else {}
        bu = env.get("BROWSER_USE_CONFIG_PATH")
        disp = bu if isinstance(bu, str) else repr(bu)
        if len(disp) > 160:
            disp = disp[:160] + "…"
        log(
            f"[diag][mcp][{phase}] server_index={i} id={e.get('id')!r} "
            f"BROWSER_USE_CONFIG_PATH={disp!r}",
            force=True,
        )
        return
    log(f"[diag][mcp][{phase}] 未找到 browser-use 条目", force=True)
    for i, e in enumerate(servers):
        if not isinstance(e, dict):
            continue
        sid = e.get("id")
        cmd = e.get("command")
        args = e.get("args")
        prev = ""
        if isinstance(args, list):
            parts = [str(a) for a in args[:8] if isinstance(a, (str, int, float, bool))]
            prev = " ".join(parts)
            if len(args) > 8:
                prev += " …"
        log(
            f"[diag][mcp][{phase}] hint server_index={i} id={sid!r} command={cmd!r} "
            f"args_preview={prev!r}",
            force=True,
        )
    log(
        f"[diag][mcp][{phase}] 说明：若仍保留 **official-mcp-puppeteer**（npx @modelcontextprotocol/server-puppeteer），"
        "会 **launch** 独立 Chromium，与 KALAROKO_CDP_ENDPOINT 所连 Chrome 是两套进程。**推荐**仅保留 **jachin-puppeteer-cdp**（CDP connect）+ **browser-use**（BROWSER_USE_CONFIG_PATH）；见 config/mcp_servers.json.example。",
        force=True,
    )


def _log_l2_allowlist_mcp_hint(log: Callable[..., None]) -> None:
    """配对 L2 且 allowed_skills 非空时，提示 MCP 可能被白名单剔光（须 L3 侧 env，非本脚本进程）。"""
    p = Path.home() / ".jachin" / "l2_gateway_config.json"
    if not p.is_file():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8").lstrip("\ufeff"))
    except Exception as e:
        log(f"[diag][allowlist] 读取 {p} 失败: {e}", err=True, force=True)
        return
    if not data.get("paired"):
        return
    snap = data.get("permissions_snapshot") or {}
    if not isinstance(snap, dict):
        return
    allowed = snap.get("allowed_skills")
    if not isinstance(allowed, list) or not allowed:
        return
    env_here = (os.environ.get("JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    log(
        f"[diag][allowlist] 已配对 L2：permissions_snapshot.allowed_skills 非空（{len(allowed)} 项）。"
        "未列入该名单的 MCP（如 mcp:puppeteer_*）在合并工具池时会被 **剔除**，"
        "模型可能误判「没有浏览器 MCP」。"
        "请在 **启动 L3 的环境**（非本脚本）设置 JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL=1 并 **重启 L3**，"
        "或在 L2 白名单中加入各浏览器 MCP 的 tool id / mcp:*。"
        f"（本脚本进程该变量={'已设' if env_here else '未设'}，仅作对照；以 L3 进程为准。）",
        force=True,
    )


def _preflight_attach_json_and_devtools(
    *,
    attach_path: Path,
    cdp_http: str,
    log: Callable[..., None],
) -> int | None:
    """校验附加 JSON 与 DevTools；失败返回 exit 码。"""
    try:
        data = json.loads(attach_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        log(f"[preflight][ERROR] 附加配置不可读/非 JSON: {e}", err=True, force=True)
        return 2
    bp = data.get("browser_profile")
    if not isinstance(bp, dict) or not bp:
        log("[preflight][ERROR] 附加配置缺少 browser_profile", err=True, force=True)
        return 2
    if not any(
        isinstance(v, dict) and str(v.get("cdp_url") or "").strip() for v in bp.values()
    ):
        log("[preflight][ERROR] browser_profile 内无 cdp_url", err=True, force=True)
        return 2
    ok, err, base = _cdp_http_probe(cdp_http, timeout_sec=4.0)
    if not ok:
        log(
            f"[preflight][ERROR] DevTools 不可达 {cdp_http!r}: {err}",
            err=True,
            force=True,
        )
        log(
            "[preflight][HINT] 推荐与 E2E 相同：仓库根执行 .\\scripts\\launch_chrome_debug.ps1\n"
            "  （独立用户目录 %TEMP%\\chrome-debug-boss；端口须与 KALAROKO_CDP_ENDPOINT 一致）。\n"
            "  等价手动命令示例：\n"
            '    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            "--remote-debugging-port=<端口> "
            '--user-data-dir="%TEMP%\\chrome-debug-boss"',
            force=True,
        )
        return 2
    log(f"[preflight] DevTools OK: {base}/json/version", force=True)
    try:
        ver_body = (
            urllib.request.build_opener(urllib.request.ProxyHandler({}))
            .open(f"{base}/json/version", timeout=4.0)
            .read(800)
            .decode("utf-8", errors="replace")
        )
        log(f"[preflight] /json/version 片段: {ver_body[:400]!r}", force=True)
    except Exception as e:
        log(f"[preflight][WARN] 读取 version 正文失败（已确认 200）: {e}", force=True)
    return None


def _looks_like_browser_use_entry(e: dict[str, Any]) -> bool:
    sid = str(e.get("id") or "").strip().lower()
    if sid in ("browser-use", "browser_use"):
        return True
    if "browser" in sid and "use" in sid:
        return True
    args = e.get("args")
    if isinstance(args, list):
        flat = " ".join(str(a) for a in args if isinstance(a, str)).lower()
        # 常见：uvx browser-use --mcp；也兼容仅含包名/入口、未写 --mcp 的变体
        if "browser-use" in flat or "browser_use" in flat:
            return True
    cmd = str(e.get("command") or "").lower()
    if "browser-use" in cmd or "browser_use" in cmd:
        return True
    return False


def _default_browser_use_mcp_entry(*, cfg_abs: str) -> dict[str, Any]:
    """与 config/mcp_servers.json.example 中 browser-use 条一致；BROWSER_USE_CONFIG_PATH 写绝对路径以便未展开 ${...} 时也能附加 CDP。"""
    return {
        "id": "browser-use",
        "name": "Browser-Use（Agent 级浏览器自动化 MCP · K11 脚本在缺失时自动追加）",
        "command": "uvx",
        "args": [
            "--from",
            "browser-use[cli]",
            "browser-use",
            "--mcp",
        ],
        "env": {
            "PYTHONIOENCODING": "utf-8",
            "OPENAI_API_KEY": "${OPENAI_API_KEY}",
            "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
            "BROWSER_USE_HEADLESS": "${BROWSER_USE_HEADLESS}",
            "BROWSER_USE_CONFIG_PATH": cfg_abs,
            "HTTP_PROXY": "${HTTP_PROXY}",
            "HTTPS_PROXY": "${HTTPS_PROXY}",
        },
    }


def _mcp_servers_root_and_list(data: Any) -> tuple[Any, list[Any]] | tuple[None, None]:
    """
    ~/.jachin/mcp_servers.json 常见两种形态：
    - {"mcp_servers": [ {...}, ... ]}（仓库示例）
    - [ {...}, ... ]（部分编辑器/合并工具导出的根数组）
    返回 (写回时的根对象, 可原地修改的 server 列表)；无法识别则 (None, None)。
    """
    if isinstance(data, list):
        return data, data
    if isinstance(data, dict):
        s = data.get("mcp_servers")
        if isinstance(s, list):
            return data, s
    return None, None


def _patch_mcp_servers_browser_use_config_path(
    *,
    cfg_abs: str,
    log: Callable[..., None],
    append_if_missing: bool = True,
) -> tuple[bool, str]:
    p = Path.home() / ".jachin" / "mcp_servers.json"
    if not p.is_file():
        return False, f"不存在 {p}，请先复制 config/mcp_servers.json.example 并启动过 L3 一次"
    raw = p.read_text(encoding="utf-8")
    try:
        data: Any = json.loads(raw.lstrip("\ufeff"))
    except json.JSONDecodeError as e:
        return False, f"JSON 解析失败: {e}"
    _root, servers = _mcp_servers_root_and_list(data)
    if servers is None:
        return (
            False,
            "mcp_servers.json 根须为 {\"mcp_servers\": [...]} 或 JSON 数组 [...]；当前格式无法识别",
        )
    patched: list[int] = []
    for i, e in enumerate(servers):
        if not isinstance(e, dict):
            continue
        if not _looks_like_browser_use_entry(e):
            continue
        env = e.get("env")
        if not isinstance(env, dict):
            env = {}
            e["env"] = env
        env["BROWSER_USE_CONFIG_PATH"] = cfg_abs
        patched.append(i)
    appended = False
    if not patched and append_if_missing:
        taken_ids = {
            str(e.get("id") or "").strip().lower()
            for e in servers
            if isinstance(e, dict) and str(e.get("id") or "").strip()
        }
        if "browser-use" in taken_ids:
            return (
                False,
                "已存在 id=browser-use 的条目但未命中 browser-use 识别规则（请检查 JSON 结构）；"
                "见 [diag][mcp]",
            )
        servers.append(_default_browser_use_mcp_entry(cfg_abs=cfg_abs))
        appended = True
        patched = [len(servers) - 1]
        log(
            "[cdp][auto-patch] ~/.jachin/mcp_servers.json 原无 browser-use 条目，"
            "已按 config/mcp_servers.json.example 追加一条（uvx browser-use --mcp）；"
            "请确认本机已安装 uv/uvx，并**重启 L3** 后 tools/list 才含 browser_* / retry_with_browser_use_agent。",
            force=True,
        )
    if not patched:
        return (
            False,
            "未找到 browser-use MCP 条目，且未启用自动追加（见 --no-append-browser-use-mcp）；"
            "或需手动从 config/mcp_servers.json.example 合并 browser-use；"
            "见日志 [diag][mcp] 各条 id/command/args 摘要",
        )
    if len(patched) > 1 and not appended:
        log(f"[cdp][WARN] 命中多条 browser-use 条目，已全部写入 BROWSER_USE_CONFIG_PATH: 索引 {patched}")
    bak = p.with_suffix(".json.bak_k11_cdp")
    try:
        bak.write_text(raw, encoding="utf-8")
    except OSError as e:
        return False, f"无法写备份 {bak}: {e}"
    try:
        p.write_text(json.dumps(_root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"无法写回 {p}: {e}"
    suffix = "（本次已追加 browser-use 条目）" if appended else ""
    return True, f"已更新 {p}，备份 {bak.name}；请重启 L3 使 MCP 子进程加载新 env{suffix}"

_VERDICT_RE = re.compile(
    r"^\s*VERDICT\s*:\s*(PASS|FAIL|BLOCKED|SKIP|UNKNOWN)\s*$",
    re.I | re.MULTILINE,
)

# 回答里常见异常线索（仅启发排障，非严谨解析）
_ANSWER_SIGNAL_RES = [
    ("err", re.compile(r"(?i)\b(traceback|exception|error:|failed|timeout|ECONNREFUSED)\b")),
    ("mcp", re.compile(r"(?i)\b(mcp|browser_use|browser-use|cdp|websocket)\b")),
    ("nav", re.compile(r"(?i)browser_navigate|NavigateToUrl")),
]


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_logger(*, quiet: bool, log_file: Path | None) -> tuple[Callable[..., None], TextIO | None]:
    """返回 (log, file_handle)。log(msg, detail=..., force=...)"""
    fh: TextIO | None = None
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log_file, "w", encoding="utf-8")

    def log(
        msg: str,
        *,
        detail: str | None = None,
        force: bool = False,
        err: bool = False,
    ) -> None:
        prefix = f"[k11-p1-l3][{_ts()}]"
        line = f"{prefix} {msg}"
        target = sys.stderr if err else sys.stdout
        if fh:
            fh.write(line + "\n")
            if detail:
                fh.write(detail.rstrip() + "\n")
            fh.flush()
        if quiet and not force:
            return
        target.write(line + "\n")
        target.flush()
        if detail:
            target.write(detail.rstrip() + "\n")
            target.flush()

    return log, fh


def _strip_yaml_frontmatter(text: str) -> str:
    # UTF-8 BOM 会导致 startswith("---") 失败，整段 YAML 会误入「正文」
    t = text.replace("\ufeff", "").strip()
    if not t.startswith("---"):
        return t
    rest = t[3:].lstrip("\n")
    end = rest.find("\n---")
    if end == -1:
        return t
    return rest[end + 4 :].lstrip()


def _substitute_context(text: str, context_url: str) -> str:
    return text.replace("{{CONTEXT_URL}}", (context_url or "").strip())


def _http_post_json(
    url: str, body: dict[str, Any], *, timeout: float
) -> tuple[int, dict[str, Any] | str, str]:
    """返回 (http_code, parsed|err_token, raw_body)。"""
    raw_body = ""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        try:
            raw_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw_body = str(e)
        code = e.code
    except Exception as e:
        return 0, f"request_failed:{type(e).__name__}: {e}", ""
    try:
        parsed = json.loads(raw_body)
        if not isinstance(parsed, dict):
            return code, f"not_dict:{raw_body[:1200]}", raw_body
        return code, parsed, raw_body
    except json.JSONDecodeError:
        return code, f"invalid_json:{raw_body[:1200]}", raw_body


def _build_user_input(
    *,
    skill_body: str,
    context_url: str,
    case_keys: set[str] | None,
    cdp_config_abs: str | None,
    cdp_preflight_block: str | None = None,
) -> str:
    subset = ""
    if case_keys:
        subset = (
            "\n【子集】本轮**仅**执行以下用例 key（其余在汇总表标 SKIP）：\n- "
            + "\n- ".join(sorted(case_keys))
            + "\n"
        )
    attach_hint = ""
    if cdp_config_abs:
        attach_hint = f"""
【宿主 / MCP · 必须附加既有 Chrome（与「禁止 navigate」并列）】
- 用户已按 **KALAROKO_CDP_ENDPOINT**（与 E2E 相同）开好远程调试 Chrome；**browser-use MCP 子进程**须通过 ``BROWSER_USE_CONFIG_PATH`` 指向含 **同一 cdp_url** 的配置。若 MCP 仍默认再启一只浏览器，则**不是**本验收场景。
- 若 Observation 显示**第二只独立 Chrome 或未附加同一 CDP**：相关用例 **VERDICT: BLOCKED**，说明须配置 ``BROWSER_USE_CONFIG_PATH`` 并重启 L3，**不要** navigate 凑 PASS。
- 本脚本生成的附加用配置文件（供人工核对）：`{cdp_config_abs}`
"""
    pre = ""
    if cdp_preflight_block and cdp_preflight_block.strip():
        pre = cdp_preflight_block.strip() + "\n\n"
    return f"""{pre}你是 Jachin L3 浏览器验收执行器。请**严格**按下列 SKILL 文档执行。

【硬约束 · 与机器人 + KALAROKO_CDP_ENDPOINT 已开页面一致 · 违反则 BLOCKED/FAIL】
- **禁止**使用 browser_navigate / mcp_browser_navigate 或等价能力，以「开始测试、打开产品站」为目的加载任意 URL（含 `{context_url.strip()}`）。机器人已摆好当前标签页。
- 仅在**当前已附加的 Chrome 标签页**上：browser_get_state、点击、滚动、extract、retry_with_browser_use_agent（任务正文须写明**不得对基准 URL 做首开导航**）。
- 若 **browser_get_state** 为 ``about:blank`` / 空标题：先 **browser_list_tabs**，再 **browser_switch_tab** 切到含真实站点 URL 的标签页（多 Tab 时常为「焦点不在业务页」）；**禁止**用 navigate 当替代方案去「打开站点」。
- 若当前页完全无法测：相关用例 **VERDICT: BLOCKED**，说明当前 URL/Observation，**不要**为通过而擅自导航。
{attach_hint}
【参考站点（仅识别文案用，不是跳转目标）】
{context_url.strip()}
{subset}
--- SKILL 正文 ---

{skill_body}
"""


def _all_verdicts(text: str) -> list[str]:
    return [m.group(1).upper() for m in _VERDICT_RE.finditer(text or "")]


def _verdict_positions(text: str) -> list[tuple[int, int, str]]:
    """(line_1based, col, verdict)"""
    out: list[tuple[int, int, str]] = []
    for m in _VERDICT_RE.finditer(text or ""):
        prefix = text[: m.start()]
        line = prefix.count("\n") + 1
        col = m.start() - (prefix.rfind("\n") + 1 if "\n" in prefix else 0)
        out.append((line, col, m.group(1).upper()))
    return out


def _scan_answer_signals(text: str, log: Callable[..., None], *, max_hits: int = 30) -> None:
    if not (text or "").strip():
        log("  [signal] 回答为空，跳过关键字扫描", force=True)
        return
    hits = 0
    for label, rx in _ANSWER_SIGNAL_RES:
        for m in rx.finditer(text):
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 120)
            snippet = text[start:end].replace("\n", " ")
            log(f"  [signal:{label}] …{snippet}…")
            hits += 1
            if hits >= max_hits:
                log(f"  [signal] 已达 max_hits={max_hits}，截断")
                return


def _summarize_response_dict(resp: dict[str, Any], log: Callable[..., None]) -> None:
    log("[response] 顶层键: " + ", ".join(sorted(resp.keys())))
    for k in sorted(resp.keys()):
        v = resp[k]
        if k == "answer" and isinstance(v, str):
            log(f"  [answer] 字符数={len(v)} 行数={v.count(chr(10)) + 1}")
        elif isinstance(v, str):
            s = v.replace("\n", "\\n")
            log(f"  [{k}] str len={len(v)} preview={s[:240]!r}{'…' if len(s) > 240 else ''}")
        elif isinstance(v, (dict, list)):
            try:
                compact = json.dumps(v, ensure_ascii=False)
            except (TypeError, ValueError):
                compact = repr(v)
            log(f"  [{k}] {type(v).__name__} len={len(v)} preview={compact[:500]}{'…' if len(compact) > 500 else ''}")
        else:
            log(f"  [{k}] {type(v).__name__} = {v!r}")


def _log_relevant_env(log: Callable[..., None], *, title: str = "[env] 与排障相关的环境变量（值可能为空）：") -> None:
    keys = [
        "JACHIN_L3_HTTP_BASE",
        "K11_BROWSER_CONTEXT_URL",
        "KALAROKO_CDP_ENDPOINT",
        "K11_AUTO_LAUNCH_CHROME_DEBUG",
        "BROWSER_USE_CONFIG_PATH",
        "JACHIN_BROWSER_USE_ATTACH_CONFIG",
        "JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL",
        "JACHIN_HOME",
        "USERPROFILE",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    log(title)
    for k in keys:
        v = os.environ.get(k)
        if v is None:
            log(f"  {k}=(未设置)")
        else:
            log(f"  {k}={v[:200]!r}{'…' if len(v) > 200 else ''}")


def _default_smoke_diag_dir() -> Path:
    env = (os.environ.get("JACHIN_K11_SMOKE_DIAG_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        return Path(r"D:\zzz\jachin\冒烟")
    return Path.home() / "jachin-smoke-diag"


def _looks_like_official_npx_puppeteer(e: dict[str, Any]) -> bool:
    args = e.get("args")
    if isinstance(args, list):
        flat = " ".join(str(a) for a in args if isinstance(a, str)).lower()
        if "server-puppeteer" in flat or "@modelcontextprotocol/server-puppeteer" in flat:
            return True
    return False


def _looks_like_jachin_puppeteer_cdp_entry(e: dict[str, Any]) -> bool:
    sid = str(e.get("id") or "").strip().lower().replace("_", "-")
    if "jachin-puppeteer-cdp" in sid:
        return True
    args = e.get("args")
    if isinstance(args, list):
        flat = " ".join(str(a) for a in args if isinstance(a, str)).lower()
        if "mcp-jachin-puppeteer-cdp" in flat:
            return True
    return False


def _mask_env_preview(env: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in env.items():
        if v is None:
            continue
        s = str(v)
        kl = str(k).upper()
        if any(x in kl for x in ("KEY", "SECRET", "TOKEN", "PASSWORD", "SESSDATA")) and len(s) > 8:
            out[str(k)] = s[:4] + "…" + s[-2:]
        else:
            out[str(k)] = s[:500] + ("…" if len(s) > 500 else "")
    return out


def _mcp_servers_diag_snapshot() -> dict[str, Any]:
    p = Path.home() / ".jachin" / "mcp_servers.json"
    snap: dict[str, Any] = {"path": str(p.resolve()), "exists": p.is_file()}
    if not p.is_file():
        return snap
    try:
        data = json.loads(p.read_text(encoding="utf-8").lstrip("\ufeff"))
    except Exception as e:
        snap["parse_error"] = f"{type(e).__name__}: {e}"
        return snap
    _root, servers = _mcp_servers_root_and_list(data)
    if servers is None:
        snap["parse_error"] = "root 既不是 {\"mcp_servers\":[]} 也不是 JSON 数组"
        return snap
    entries: list[dict[str, Any]] = []
    for i, e in enumerate(servers):
        if not isinstance(e, dict):
            entries.append({"index": i, "note": "non-dict"})
            continue
        env = e.get("env") if isinstance(e.get("env"), dict) else {}
        args = e.get("args")
        aprev = None
        if isinstance(args, list):
            aprev = " ".join(str(x) for x in args[:14])
            if len(args) > 14:
                aprev += " …"
        entries.append(
            {
                "index": i,
                "id": e.get("id"),
                "command": e.get("command"),
                "args_preview": aprev,
                "env_masked": _mask_env_preview(env),
                "flags": {
                    "browser_use_like": _looks_like_browser_use_entry(e),
                    "jachin_puppeteer_cdp": _looks_like_jachin_puppeteer_cdp_entry(e),
                    "official_npx_puppeteer": _looks_like_official_npx_puppeteer(e),
                },
            }
        )
    snap["server_count"] = len(servers)
    snap["servers"] = entries
    snap["summary"] = {
        "has_browser_use": any(x["flags"]["browser_use_like"] for x in entries if "flags" in x),
        "has_jachin_puppeteer": any(x["flags"]["jachin_puppeteer_cdp"] for x in entries if "flags" in x),
        "has_official_puppeteer": any(x["flags"]["official_npx_puppeteer"] for x in entries if "flags" in x),
    }
    return snap


def _attach_json_diag_snapshot(path: str | None) -> dict[str, Any]:
    if not path:
        return {"path": None, "exists": False}
    pp = Path(path)
    out: dict[str, Any] = {"path": str(pp.resolve()), "exists": pp.is_file()}
    if not pp.is_file():
        return out
    try:
        data = json.loads(pp.read_text(encoding="utf-8-sig"))
    except Exception as e:
        out["read_error"] = f"{type(e).__name__}: {e}"
        return out
    urls: list[str] = []
    bp = data.get("browser_profile")
    if isinstance(bp, dict):
        for _k, prof in bp.items():
            if isinstance(prof, dict) and prof.get("cdp_url"):
                urls.append(str(prof.get("cdp_url")))
    out["cdp_urls_in_file"] = urls
    return out


def _l2_allowlist_diag() -> dict[str, Any]:
    p = Path.home() / ".jachin" / "l2_gateway_config.json"
    d: dict[str, Any] = {"path": str(p.resolve()), "exists": p.is_file()}
    if not p.is_file():
        return d
    try:
        data = json.loads(p.read_text(encoding="utf-8").lstrip("\ufeff"))
    except Exception as e:
        d["read_error"] = f"{type(e).__name__}: {e}"
        return d
    d["paired"] = bool(data.get("paired"))
    snap = data.get("permissions_snapshot") or {}
    if isinstance(snap, dict):
        allowed = snap.get("allowed_skills")
        d["allowed_skills_count"] = len(allowed) if isinstance(allowed, list) else 0
        d["allowed_skills_nonempty"] = bool(isinstance(allowed, list) and len(allowed) > 0)
    merge = (os.environ.get("JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    d["jachin_merge_local_mcp_in_tool_pool_in_this_script_process"] = merge
    return d


def _fill_smoke_checklist(sr: dict[str, Any]) -> None:
    chk: list[str] = []
    cdp = sr.get("cdp") or {}
    ss = sr.get("script_side_chrome") or {}
    if cdp.get("devtools_reachable") is False:
        chk.append(
            "DevTools /json/version 不可达：你点开的可能是「日常 Chrome」而非带 --remote-debugging-port 的调试实例；"
            "或端口与 KALAROKO_CDP_ENDPOINT 不一致。"
        )
    if cdp.get("devtools_reachable") and not ss.get("playwright_url") and not sr.get("playwright_skipped"):
        chk.append(
            "CDP 门禁通过但 Playwright 未拿到页面 URL：检查是否安装 playwright、或当前焦点页为 devtools:// / 空白页。"
        )
    pu = (ss.get("playwright_url") or "").lower()
    if pu == "about:blank" or pu.startswith("chrome://"):
        chk.append("Playwright 快照页为空白或 chrome://：在调试 Chrome 里切到业务标签再测。")

    mcp = sr.get("mcp_servers") or {}
    summ = mcp.get("summary") or {}
    if mcp.get("exists") and not summ.get("has_browser_use") and not summ.get("has_jachin_puppeteer"):
        chk.append(
            "mcp_servers.json 无 browser-use / jachin-puppeteer-cdp：L3 侧可能没有「连 CDP」的浏览器工具。"
        )
    if summ.get("has_official_puppeteer"):
        chk.append(
            "仍存在 official npx @modelcontextprotocol/server-puppeteer：会 launch **另一只** Chromium，"
            "易表现为「没用我已开的窗口」。请删此项，改用 jachin-puppeteer-cdp。"
        )

    l2 = sr.get("l2") or {}
    if l2.get("allowed_skills_nonempty"):
        m = "开" if l2.get("jachin_merge_local_mcp_in_tool_pool_in_this_script_process") else "关"
        chk.append(
            f"已配对 L2 且 allowed_skills 非空：浏览器类 mcp:* 可能被工具池剃掉。"
            f"请在 **启动 L3 的进程**（不是本脚本）设 JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL=1 并重启 L3。"
            f"（本脚本进程 merge_local={m}，仅供参考。）"
        )

    chk.append(
        "本脚本只通过 HTTP 调 L3；脚本里的 os.environ **不会**传给 L3。真正决定子浏览器的是 "
        "~/.jachin/mcp_servers.json 里各 MCP 的 env（BROWSER_USE_CONFIG_PATH、PUPPETEER_BROWSER_URL 等）。"
    )
    chk.append("改 mcp_servers 后必须**完全退出并重启 L3**，否则仍是旧 MCP 子进程。")

    if (
        cdp.get("devtools_reachable")
        and ss.get("playwright_url")
        and "about" not in (ss.get("playwright_url") or "").lower()
    ):
        chk.append(
            "若上项成立且 Playwright 已看到真实业务 URL，但 L3 回答仍 about:blank："
            "几乎可断定是 **browser-use / jachin-puppeteer-cdp 未附加同一 CDP** 或 **未重启 L3**。"
        )

    sr["checklist"] = chk


def _format_smoke_diag_human(sr: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("K11 P1 冒烟诊断报告（run_k11_p1_modules_l3.py）")
    lines.append("=" * 80)
    lines.append("")
    lines.append("【核心问题】为什么「没用我已经启动好的谷歌浏览器」？")
    lines.append("-" * 40)
    lines.append(
        "常见有三种不同情况，请先对号入座：\n"
        "\n"
        "  A) 你开的是「平时任务栏里的 Chrome」——若启动参数里没有 --remote-debugging-port，\n"
        "     则本机 **根本没有** 对自动化开放的 CDP HTTP 口；脚本连不上，不是你的错，是实例不对。\n"
        "     请用 launch_chrome_debug.ps1 或手动带 remote-debugging-port 起一只**调试专用** Chrome。\n"
        "\n"
        "  B) 调试 Chrome 已开、本脚本 Playwright 也能看到 kalaroko.com——说明 **脚本侧已用对你的浏览器**。\n"
        "     此时若 L3 仍 about:blank，是 **L3 里 MCP 子进程** 没连同一 CDP（配置/未重启），\n"
        "     不是「脚本没连上 Chrome」。\n"
        "\n"
        "  C) mcp_servers 里仍有 official-mcp-puppeteer（npx server-puppeteer）——它会 **再 launch 一只 Chromium**，\n"
        "     模型可能操作那只新窗口，看起来就像「没用我原来那扇窗」。\n"
    )
    lines.append("")
    lines.append("【二】本脚本侧：CDP / Playwright（是否连上「调试 Chrome」）")
    lines.append("-" * 40)
    cdp = sr.get("cdp") or {}
    for k in sorted(cdp.keys()):
        lines.append(f"  {k}: {cdp[k]!r}")
    ss = sr.get("script_side_chrome") or {}
    lines.append("  --- Playwright / json/list ---")
    for k in sorted(ss.keys()):
        lines.append(f"  {k}: {ss[k]!r}")
    lines.append("")
    lines.append("【三】browser-use 附加 JSON")
    lines.append("-" * 40)
    lines.append(json.dumps(sr.get("attach_json") or {}, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("【四】~/.jachin/mcp_servers.json 摘要（决定 L3 用哪只浏览器）")
    lines.append("-" * 40)
    mcp = sr.get("mcp_servers") or {}
    lines.append(json.dumps({k: mcp[k] for k in mcp if k != "servers"}, ensure_ascii=False, indent=2))
    lines.append("  （完整逐条 env 见同次生成的 .json 文件）")
    lines.append("")
    lines.append("【五】排查清单（自动生成）")
    lines.append("-" * 40)
    for i, line in enumerate(sr.get("checklist") or [], start=1):
        lines.append(f"  {i}. {line}")
    lines.append("")
    lines.append("【六】L2 白名单 / HTTP")
    lines.append("-" * 40)
    lines.append(json.dumps(sr.get("l2") or {}, ensure_ascii=False, indent=2))
    lines.append("")
    http = sr.get("http") or {}
    if http:
        lines.append(json.dumps(http, ensure_ascii=False, indent=2))
        lines.append("")
    lines.append("【七】退出码与异常")
    lines.append("-" * 40)
    lines.append(f"  exit_code: {sr.get('exit_code')!r}")
    lines.append(f"  early_exit_phase: {sr.get('early_exit_phase')!r}")
    if sr.get("exception"):
        lines.append(f"  exception:\n{sr['exception']}")
    lines.append("")
    lines.append("【八】meta")
    lines.append("-" * 40)
    lines.append(json.dumps(sr.get("meta") or {}, ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("（完）并列 .json 便于工具 diff。")
    return "\n".join(lines)


def _write_smoke_diag_bundle(out_dir: Path, report: dict[str, Any]) -> tuple[Path | None, Path | None]:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[k11-p1-l3][smoke-diag] 无法创建目录 {out_dir}: {e}", file=sys.stderr)
        return None, None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = out_dir / f"k11_p1_smoke_diag_{ts}"
    jp = stem.with_suffix(".json")
    tp = stem.with_suffix(".txt")
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tp.write_text(_format_smoke_diag_human(report), encoding="utf-8")
    except OSError as e:
        print(f"[k11-p1-l3][smoke-diag] 写入失败: {e}", file=sys.stderr)
        return None, None
    return tp, jp


def main() -> int:
    ap = argparse.ArgumentParser(description="K11 P1 六模块：L3 + SKILL_P1_MODULES.md（KALAROKO_CDP_ENDPOINT 当前页，与 E2E 同源）")
    ap.add_argument("--skill-md", type=Path, default=DEFAULT_SKILL, help="SKILL Markdown 路径")
    ap.add_argument("--context-url", default=DEFAULT_CONTEXT_URL, help="写入 MD 占位 {{CONTEXT_URL}}，脚本不发起导航")
    ap.add_argument("--l3-base", default=DEFAULT_L3_BASE, help="L3 HTTP 根地址")
    ap.add_argument("--max-iterations", type=int, default=48)
    ap.add_argument("--l3-timeout-sec", type=float, default=3600.0)
    ap.add_argument(
        "--cases",
        default="",
        help="逗号分隔：p1_customer_service,…",
    )
    ap.add_argument("--json-report", type=Path, default=None)
    ap.add_argument(
        "--dump-prompt",
        type=Path,
        default=None,
        help="将完整 user_input 写入文件，便于与 L3 日志对照",
    )
    ap.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="详细日志写入该文件（与控制台同步）",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="仅输出错误与最终摘要（仍写入 --log-file 若指定）",
    )
    ap.add_argument(
        "--cdp-http",
        default="",
        help="单次覆盖 KALAROKO_CDP_ENDPOINT（与 E2E 读 .env 同源）；留空则仅从环境/.env 读取 KALAROKO_CDP_ENDPOINT",
    )
    ap.add_argument(
        "--no-cdp-playwright-verify",
        action="store_true",
        help="跳过 Playwright connect_over_cdp 快照（仍会做 /json/list）；加快启动或避免未安装 playwright",
    )
    ap.add_argument(
        "--skip-cdp-gate",
        action="store_true",
        help="跳过写配置前的 DevTools /json/version 门禁（不推荐）",
    )
    ap.add_argument(
        "--auto-launch-chrome-debug",
        action="store_true",
        help="门禁失败时在 Windows 自动执行 scripts/launch_chrome_debug.ps1；也可设环境变量 K11_AUTO_LAUNCH_CHROME_DEBUG=1",
    )
    ap.add_argument(
        "--no-write-cdp-config",
        action="store_true",
        help="不写入 ~/.jachin/runtime/browser-use-attach-cdp.json（仍发 L3；无配置时 MCP 可能仍新开浏览器）",
    )
    ap.add_argument(
        "--cdp-config-out",
        type=Path,
        default=None,
        help="覆盖默认配置文件路径（默认 ~/.jachin/runtime/browser-use-attach-cdp.json）",
    )
    ap.add_argument(
        "--apply-mcp-browser-use-cdp",
        action="store_true",
        help="将 BROWSER_USE_CONFIG_PATH 合并进 ~/.jachin/mcp_servers.json（与默认自动合并等价，显式再执行一次）",
    )
    ap.add_argument(
        "--no-auto-patch-mcp",
        action="store_true",
        help="不自动改写 ~/.jachin/mcp_servers.json（默认会自动写入绝对路径 BROWSER_USE_CONFIG_PATH）",
    )
    ap.add_argument(
        "--no-append-browser-use-mcp",
        action="store_true",
        help="禁止在 mcp_servers 中缺少 browser-use 时自动追加条目（默认会追加，与 jachin-puppeteer-cdp 主轨形成 Agent 辅轨）",
    )
    ap.add_argument(
        "--no-preflight",
        action="store_true",
        help="跳过附加 JSON 校验与 DevTools /json/version 探测（不推荐）",
    )
    ap.add_argument(
        "--cdp-probe",
        action="store_true",
        help="（已弃用：默认预检已含探测）仅在与 --no-preflight 同用时再测一次 DevTools",
    )
    ap.add_argument(
        "--print-setenv-ps",
        action="store_true",
        help="打印 PowerShell 设置 BROWSER_USE_CONFIG_PATH 的一行命令后退出（不写 mcp_servers.json）",
    )
    ap.add_argument(
        "--smoke-diag-dir",
        default="",
        help="冒烟诊断包输出目录（.txt + .json）；空则 Windows 默认 D:\\zzz\\jachin\\冒烟，或环境变量 JACHIN_K11_SMOKE_DIAG_DIR",
    )
    ap.add_argument(
        "--no-smoke-diag",
        action="store_true",
        help="不写入冒烟诊断包",
    )
    args = ap.parse_args()

    log, log_fh = _make_logger(quiet=args.quiet, log_file=args.log_file)
    rc = 0
    smoke_report: dict[str, Any] = {
        "schema": "k11_p1_smoke_diag/v1",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "argv": list(sys.argv),
            "cwd": str(Path.cwd().resolve()),
            "python": sys.version,
            "executable": sys.executable,
            "platform": sys.platform,
        },
    }
    diag_exit: list[int] = [0]
    smoke_diag_dir: Path | None = None
    if not args.no_smoke_diag:
        sdd = (args.smoke_diag_dir or "").strip()
        smoke_diag_dir = Path(sdd).expanduser() if sdd else _default_smoke_diag_dir()

    def _mark_exit(code: int) -> int:
        diag_exit[0] = code
        return code

    try:
        if args.quiet and not args.log_file:
            print(
                "[k11-p1-l3] 提示：已 --quiet 且未指定 --log-file，控制台几乎无输出；"
                "排障请加 --log-file path.log",
                file=sys.stderr,
            )
        log("========== K11 P1 · L3 触发 ==========")
        log(f"[meta] 脚本: {Path(__file__).resolve()}")
        log(f"[meta] 仓库根 ROOT={ROOT}")
        log(f"[meta] cwd={Path.cwd()}")
        log(f"[meta] Python={sys.version.split()[0]} executable={sys.executable}")
        _reload_dotenv()
        _log_dotenv_sources(log)
        _log_relevant_env(log, title="[env] 启动时（已 merge 仓库与 ~/.jachin/.env）：")
        _log_l2_allowlist_mcp_hint(log)

        cli_cdp = (args.cdp_http or "").strip() or None
        cdp_http = _kalaroko_cdp_endpoint(cli_override=cli_cdp)
        if not cdp_http:
            log(
                "[cdp][FAIL] 未配置 KALAROKO_CDP_ENDPOINT（与 test_kalaroko_default_scenarios_e2e.py / "
                "kalaroko_monitor MCP 相同）。请在仓库根 .env 中设置，端口须与 Chrome --remote-debugging-port 一致，例如：\n"
                "  KALAROKO_CDP_ENDPOINT=http://127.0.0.1:<端口>\n"
                "并先启动远程调试 Chrome（如 .\\scripts\\launch_chrome_debug.ps1）。\n"
                "也可单次传入：--cdp-http http://127.0.0.1:<端口>",
                err=True,
                force=True,
            )
            smoke_report["early_exit_phase"] = "missing_kalaroko_cdp_endpoint"
            smoke_report["cdp"] = {"resolved": None, "devtools_reachable": False}
            return _mark_exit(2)
        os.environ["KALAROKO_CDP_ENDPOINT"] = cdp_http
        smoke_report["cdp"] = {"resolved": cdp_http, "from_cli_override": bool(cli_cdp)}
        log(
            f"[cdp] KALAROKO_CDP_ENDPOINT={cdp_http!r}（与 E2E/MCP _kalaroko_cdp_endpoint 一致"
            f"{'; 来自 --cdp-http' if cli_cdp else ''}）",
            force=True,
        )

        auto_launch_chrome = bool(args.auto_launch_chrome_debug) or (
            (os.environ.get("K11_AUTO_LAUNCH_CHROME_DEBUG") or "").strip().lower() in ("1", "true", "yes")
        )
        if not args.skip_cdp_gate:
            _open_for_ps1 = (args.context_url or "").strip() or DEFAULT_CONTEXT_URL
            if not _ensure_devtools_http(
                cdp_http,
                log,
                auto_launch=auto_launch_chrome,
                open_url=_open_for_ps1,
                repo_root=ROOT,
            ):
                smoke_report["cdp"]["devtools_reachable"] = False
                smoke_report["early_exit_phase"] = "devtools_gate_failed"
                return _mark_exit(2)
            ok_b, _, base_b = _cdp_http_probe(cdp_http, timeout_sec=2.0)
            smoke_report["cdp"]["devtools_reachable"] = bool(ok_b)
            if base_b:
                smoke_report["cdp"]["devtools_base"] = base_b
        else:
            log("[cdp][gate] 已 --skip-cdp-gate，跳过 DevTools 门禁（仍可能后续 preflight/Playwright 失败）", force=True)
            smoke_report["cdp"]["skip_cdp_gate"] = True
            smoke_report["cdp"]["devtools_reachable"] = None

        cdp_cfg_path = Path(args.cdp_config_out) if args.cdp_config_out else _stable_browser_use_cdp_config_path()
        cdp_cfg_abs: str | None = None
        if not args.no_write_cdp_config:
            cdp_cfg_abs = str(_write_browser_use_cdp_config_json(cdp_http=cdp_http, dest=cdp_cfg_path))
            os.environ["BROWSER_USE_CONFIG_PATH"] = cdp_cfg_abs
            log(
                f"[cdp] 已写入 browser-use 附加配置: {cdp_cfg_abs}",
                force=True,
            )
            log(
                f"[cdp] 本脚本进程已设置 os.environ['BROWSER_USE_CONFIG_PATH']={cdp_cfg_abs!r}。"
                " 注意：HTTP 调用的 **远端 L3** 不会读取本进程环境变量；"
                "L3 依赖其 **MCP 子进程** 启动时的 env（见下方 mcp_servers 诊断与自动合并）。",
                force=True,
            )
            _summarize_attach_json(Path(cdp_cfg_abs), log)
            smoke_report["attach_json"] = _attach_json_diag_snapshot(cdp_cfg_abs)
            _log_mcp_browser_use_bu_path(log, phase="before_auto_patch")
            mcp_json = Path.home() / ".jachin" / "mcp_servers.json"
            if not args.no_auto_patch_mcp:
                if mcp_json.is_file():
                    ok_ap, msg_ap = _patch_mcp_servers_browser_use_config_path(
                        cfg_abs=cdp_cfg_abs,
                        log=log,
                        append_if_missing=not args.no_append_browser_use_mcp,
                    )
                    if ok_ap:
                        log(f"[cdp][auto-patch] {msg_ap}", force=True)
                    else:
                        log(f"[cdp][auto-patch][WARN] 未改写 mcp_servers: {msg_ap}", err=True, force=True)
                else:
                    log(
                        f"[cdp][auto-patch][WARN] 无 {mcp_json}，跳过自动合并；"
                        "请从 config/mcp_servers.json.example 复制并启用 browser-use，或首次启动 L3 生成后再跑本脚本。",
                        err=True,
                        force=True,
                    )
            else:
                log("[cdp] 已 --no-auto-patch-mcp，未改写 mcp_servers.json", force=True)
            _log_mcp_browser_use_bu_path(log, phase="after_patch")
            log(
                "[cdp] **必读**：若你刚改 mcp_servers / 或首次写入 BROWSER_USE_CONFIG_PATH，"
                "必须 **完全退出并重启 L3**，否则 browser-use 仍为旧子进程 → 常见症状为 **about:blank**（新开默认浏览器）。",
                force=True,
            )
            log(
                "[cdp] L3 侧另有 core/mcp_embedded_runtime：在占位符为空时注入上述 JSON 路径；"
                "与 mcp_servers 绝对路径互为备份。",
                force=True,
            )
            _log_relevant_env(
                log,
                title="[env] 写入配置并 export 后（仅本进程；用于对照 .env 是否曾设置）：",
            )
        else:
            log("[cdp] 已 --no-write-cdp-config，跳过写入；prompt 内不含配置文件路径提示", force=True)
            smoke_report["attach_json"] = _attach_json_diag_snapshot(None)

        if args.print_setenv_ps:
            if not cdp_cfg_abs:
                cdp_cfg_abs = str(cdp_cfg_path.resolve()) if cdp_cfg_path.is_file() else ""
            if not cdp_cfg_abs:
                log("[FAIL] 无配置文件路径：请去掉 --no-write-cdp-config 或先写入", err=True, force=True)
                smoke_report["early_exit_phase"] = "print_setenv_no_config"
                return _mark_exit(2)
            print(
                f"$env:BROWSER_USE_CONFIG_PATH='{cdp_cfg_abs}'",
                flush=True,
            )
            log("========== 仅打印 setenv，未调用 L3 ==========")
            smoke_report["early_exit_phase"] = "print_setenv_ps_only"
            return _mark_exit(0)

        if args.apply_mcp_browser_use_cdp:
            if not cdp_cfg_abs:
                if not cdp_cfg_path.is_file():
                    log(
                        "[cdp][FAIL] --apply-mcp-browser-use-cdp 需要已生成的配置文件；"
                        "请勿加 --no-write-cdp-config，或先手动写入同路径 JSON",
                        err=True,
                        force=True,
                    )
                    return _mark_exit(2)
                cdp_cfg_abs = str(cdp_cfg_path.resolve())
            ok, msg = _patch_mcp_servers_browser_use_config_path(
                cfg_abs=cdp_cfg_abs,
                log=log,
                append_if_missing=not args.no_append_browser_use_mcp,
            )
            if ok:
                log(f"[cdp][explicit-apply] {msg}", force=True)
            else:
                log(f"[cdp][FAIL] {msg}", err=True, force=True)
                smoke_report["early_exit_phase"] = "explicit_apply_mcp_failed"
                return _mark_exit(2)
            _log_mcp_browser_use_bu_path(log, phase="after_explicit_apply")

        if args.no_preflight and args.cdp_probe:
            ok, err, base = _cdp_http_probe(cdp_http, timeout_sec=3.0)
            if ok:
                log(f"[cdp][probe-only] OK: {base}/json/version", force=True)
            else:
                log(f"[cdp][probe-only][WARN] {err}", err=True, force=True)

        skill_path = Path(args.skill_md)
        if not skill_path.is_absolute():
            skill_path = ROOT / skill_path
        log(f"[skill] 路径={skill_path.resolve()} exists={skill_path.is_file()}")
        if not skill_path.is_file():
            log(f"[FAIL] 找不到 SKILL: {skill_path}", err=True, force=True)
            smoke_report["early_exit_phase"] = "skill_md_not_found"
            return _mark_exit(2)

        raw = skill_path.read_text(encoding="utf-8-sig")
        log(f"[skill] 文件字节约={len(raw.encode('utf-8'))} 字符={len(raw)}")
        body = _substitute_context(_strip_yaml_frontmatter(raw), args.context_url)
        log(f"[skill] 去掉 frontmatter 并替换 CONTEXT 后 字符={len(body)}")

        case_keys: set[str] | None = None
        if args.cases.strip():
            case_keys = {x.strip() for x in args.cases.split(",") if x.strip()}
        log(f"[run] context-url（仅文案，脚本不导航）={args.context_url!r}")
        log(f"[run] case_keys={sorted(case_keys) if case_keys else '（全部 6 条）'}")

        attach_for_preflight = cdp_cfg_abs or (
            str(cdp_cfg_path.resolve()) if cdp_cfg_path.is_file() else None
        )
        if not args.no_preflight and attach_for_preflight:
            pe = _preflight_attach_json_and_devtools(
                attach_path=Path(attach_for_preflight),
                cdp_http=cdp_http,
                log=log,
            )
            if pe is not None:
                smoke_report["early_exit_phase"] = "preflight_attach_or_devtools_failed"
                return _mark_exit(pe)
        elif args.no_preflight:
            log("[preflight] 已 --no-preflight，跳过附加 JSON 与 /json/version 校验", force=True)
        elif not attach_for_preflight:
            log("[preflight][WARN] 无附加配置文件路径，跳过 attach/version 预检", force=True)

        json_pages, devtools_base = _cdp_fetch_json_list_pages(cdp_http, log)
        pw_url: str | None = None
        pw_title: str | None = None
        pw_err: str | None = None
        if not args.no_cdp_playwright_verify:
            pw_url, pw_title, pw_err = _playwright_cdp_page_snapshot(cdp_http, log)
        else:
            log("[cdp][playwright] 已 --no-cdp-playwright-verify，跳过 connect_over_cdp 快照", force=True)

        tab_brief = [
            {"url": str(p.get("url") or "")[:220], "title": str(p.get("title") or "")[:100]}
            for p in (json_pages or [])[:12]
        ]
        smoke_report["playwright_skipped"] = bool(args.no_cdp_playwright_verify)
        smoke_report["script_side_chrome"] = {
            "json_list_page_count": len(json_pages),
            "json_list_tabs_preview": tab_brief,
            "devtools_base_from_list": devtools_base,
            "playwright_url": pw_url,
            "playwright_title": pw_title,
            "playwright_err": pw_err,
        }

        cdp_preflight_block = _format_cdp_preflight_for_prompt(
            cdp_http=cdp_http,
            devtools_base=devtools_base,
            json_pages=json_pages,
            pw_url=pw_url,
            pw_title=pw_title,
            pw_err=pw_err,
            playwright_skipped=bool(args.no_cdp_playwright_verify),
        )

        user_input = _build_user_input(
            skill_body=body,
            context_url=args.context_url,
            case_keys=case_keys,
            cdp_config_abs=cdp_cfg_abs if not args.no_write_cdp_config else None,
            cdp_preflight_block=cdp_preflight_block,
        )
        log(f"[run] user_input 总字符={len(user_input)} 约行数={user_input.count(chr(10)) + 1}")

        if args.dump_prompt:
            args.dump_prompt.parent.mkdir(parents=True, exist_ok=True)
            args.dump_prompt.write_text(user_input, encoding="utf-8")
            log(f"[run] 已写入完整 prompt: {args.dump_prompt.resolve()}")

        if not args.quiet:
            head = "\n".join(user_input.splitlines()[:24])
            tail = "\n".join(user_input.splitlines()[-12:])
            log("[run] user_input 前 24 行：")
            log(head)
            log("[run] user_input 后 12 行：")
            log(tail)

        base = str(args.l3_base).rstrip("/")

        post_url = f"{base}/api/v3/agent/run"
        max_it = max(1, min(int(args.max_iterations), 96))
        timeout_sec = max(60.0, float(args.l3_timeout_sec))
        payload = {
            "user_input": user_input,
            "max_iterations": max_it,
            "implicit_attribution": {"channel": "http_k11_p1_modules_l3"},
        }
        log(f"[http] POST {post_url}")
        log(f"[http] timeout_sec={timeout_sec} max_iterations={max_it}")
        log(f"[http] payload 键={list(payload.keys())} user_input_len={len(user_input)}")
        log(
            f"[http][diag] 若回答中大量 about:blank + BLOCKED："
            f" 1) 确认本机 Chrome 已 {cdp_http!r} 2) 已重启 L3 3) 任务管理器无「多余」自动化 Chrome",
            force=True,
        )

        t0 = datetime.now(timezone.utc)
        log(f"[http] 请求开始 t0_utc={t0.isoformat()}", force=True)
        code, resp, raw_http = _http_post_json(post_url, payload, timeout=timeout_sec)
        t1 = datetime.now(timezone.utc)
        dt = (t1 - t0).total_seconds()
        log(
            f"[http] 结束 t1_utc={t1.isoformat()} wall_s={dt:.3f} http_code={code} raw_body_len={len(raw_http)}",
            force=True,
        )

        if isinstance(resp, str):
            log(f"[FAIL] 响应无法解析为 JSON 对象: {resp[:2000]}", err=True, force=True)
            if raw_http:
                log("[http] raw_body 前 4000 字符：", detail=raw_http[:4000], err=True, force=True)
            smoke_report["early_exit_phase"] = "l3_response_not_json"
            smoke_report["http"] = {"http_code": code, "post_url": post_url, "wall_sec": dt}
            return _mark_exit(2)

        _summarize_response_dict(resp, log)

        if code == 503 or (
            isinstance(resp.get("error"), str) and "尚未就绪" in str(resp.get("error"))
        ):
            log("[FAIL] L3 Agent 未就绪（engine 未挂载或 WS 未连）", err=True, force=True)
            smoke_report["early_exit_phase"] = "l3_agent_not_ready"
            smoke_report["http"] = {"http_code": code, "post_url": post_url, "wall_sec": dt, "error": resp.get("error")}
            return _mark_exit(1)
        if code >= 400 or resp.get("error"):
            log(f"[FAIL] HTTP {code} error={resp.get('error')!r}", err=True, force=True)
            smoke_report["early_exit_phase"] = "l3_http_error"
            smoke_report["http"] = {"http_code": code, "post_url": post_url, "wall_sec": dt, "error": resp.get("error")}
            return _mark_exit(1)

        answer = (resp.get("answer") or "").strip()
        log(f"[answer] 总长度={len(answer)}")

        log("\n[k11-p1-l3] ========== L3 回答正文（控制台可能截断，完整内容见 --json-report / 自行复制）==========\n")
        limit = 200_000
        if len(answer) <= limit:
            print(answer, flush=True)
            if log_fh:
                log_fh.write(answer + "\n")
                log_fh.flush()
        else:
            print(answer[:limit], flush=True)
            print(f"\n…（控制台截断，共 {len(answer)} 字符）\n", flush=True)
            if log_fh:
                log_fh.write(answer + "\n")
                log_fh.flush()

        vpos = _verdict_positions(answer)
        verdicts = [v for _, _, v in vpos]
        log(f"[verdict] 匹配到 {len(verdicts)} 行: {verdicts}")
        for line, col, vd in vpos:
            log(f"  行 {line}:{col} → {vd}")

        _scan_answer_signals(answer, log)

        if (
            answer
            and "about:blank" in answer.lower()
            and verdicts
            and all(v == "BLOCKED" for v in verdicts)
        ):
            log(
                "[diag][answer] 全部为 BLOCKED 且含 about:blank → 多为 **browser-use 未附加 KALAROKO_CDP_ENDPOINT 同一 CDP**（未重启 L3 /"
                " mcp_servers 无 BROWSER_USE_CONFIG_PATH）或 **焦点在空标签**。请对照本脚本上方 [diag][mcp] 与 [preflight]。",
                err=True,
                force=True,
            )

        bad = {"FAIL", "BLOCKED", "UNKNOWN"}
        if not verdicts:
            log("[verdict][WARN] 未解析到任何 VERDICT: 行", err=True, force=True)
            rc = 1
        elif any(v in bad for v in verdicts):
            log(f"[verdict][FAIL] 含 {bad & set(verdicts)}", err=True, force=True)
            rc = 1
        else:
            log("[verdict][OK] 全部为 PASS/SKIP（无 FAIL/BLOCKED/UNKNOWN）")

        smoke_report["http"] = {
            "post_url": post_url,
            "http_code": code,
            "wall_sec": dt,
            "raw_body_len": len(raw_http),
            "api_error": resp.get("error") if isinstance(resp, dict) else None,
            "answer_char_len": len(answer),
            "verdicts_parsed": verdicts,
            "final_script_rc": rc,
        }

        if args.json_report:
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(
                json.dumps(
                    {
                        "ts_end_utc": _ts(),
                        "wall_sec": dt,
                        "http_code": code,
                        "skill_md": str(skill_path.resolve()),
                        "context_url": args.context_url,
                        "l3_post_url": post_url,
                        "max_iterations": max_it,
                        "case_keys": sorted(case_keys) if case_keys else None,
                        "verdicts": verdicts,
                        "verdict_positions": [{"line": a, "col": b, "verdict": c} for a, b, c in vpos],
                        "answer": answer,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            log(f"[report] JSON 已写入: {args.json_report.resolve()} （含完整 answer）")

        log(f"========== 结束 exit_code={rc} ==========")
        return _mark_exit(rc)
    except Exception as e:
        log(f"[FAIL] 未捕获异常: {type(e).__name__}: {e}", err=True, force=True)
        tb = traceback.format_exc()
        log(tb, err=True, force=True)
        smoke_report["exception"] = tb
        smoke_report["early_exit_phase"] = "uncaught_exception"
        return _mark_exit(3)
    finally:
        if smoke_diag_dir is not None:
            try:
                smoke_report["exit_code"] = diag_exit[0]
                smoke_report["mcp_servers"] = _mcp_servers_diag_snapshot()
                smoke_report["l2"] = _l2_allowlist_diag()
                _fill_smoke_checklist(smoke_report)
                tpath, jpath = _write_smoke_diag_bundle(smoke_diag_dir, smoke_report)
                if tpath and jpath:
                    print(
                        f"[k11-p1-l3][smoke-diag] 诊断包已写入（排查「为何没用已开的 Chrome」请看 .txt）:\n"
                        f"  {tpath}\n"
                        f"  {jpath}",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as e:
                print(
                    f"[k11-p1-l3][smoke-diag] 生成诊断包失败: {type(e).__name__}: {e}",
                    file=sys.stderr,
                    flush=True,
                )
        if log_fh:
            log_fh.close()


if __name__ == "__main__":
    raise SystemExit(main())
