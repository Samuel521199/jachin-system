#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Herontest 站点验收：通过 **L3 大模型 + Puppeteer MCP**（或直连 MCP）验证目标页。

默认目标：https://www.herontest.xin/

验收项（对齐 ``docs/K11_平台冒烟测试用例.md`` 中 P0；L3 Agent 模式由模型调用 MCP 完成并输出 JSON）：

1. 环境访问 · 2. 首页加载 · 3. 页面标题 KalaroKo · 4. Play Now! 可见可点 ·
5. 分类 All/1vs1/Party/Live 可切换 · 6. 游戏卡片展示 · 7. Console 无严重报错（或如实说明采集限制）。

三种模式：

1) **L3 Agent（默认）**：``POST {L3}/api/v3/agent/run``，由 ReAct 主循环选用工具清单中的
   ``mcp:puppeteer_*`` 完成测试。**需本机 L3 已启动**（如 ``python -m l3_node --ws-only``），且
   ``~/.jachin/mcp_servers.json`` 已配置 official-mcp-puppeteer。

2) **direct-stdio**：本脚本直接 ``npx @modelcontextprotocol/server-puppeteer``，不经过大模型。

3) **mcp-execute**：``POST /api/v3/mcp/execute`` 单次调工具；需 ``JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1``（开发）。

前置（direct-stdio / 部分 L3 侧）：
  - Node.js + npx；pip install mcp>=1.0.0（仅 direct-stdio 必需）
  - Chromium：``PUPPETEER_EXECUTABLE_PATH`` 或 ``npx puppeteer browsers install chrome``

用法（仓库根目录）：
  python -m l3_node --ws-only   # 另开终端先起 L3（HTTP API 默认 18991）
  python scripts/test_puppeteer_mcp_herontest.py
  python scripts/test_puppeteer_mcp_herontest.py --l3-base http://127.0.0.1:18991 --max-iterations 36
  python scripts/test_puppeteer_mcp_herontest.py --direct-stdio
  set JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1 && python scripts/test_puppeteer_mcp_herontest.py --mcp-execute
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_dotenv_for_test() -> None:
    """与 L3 类似：合并仓库根 .env 与 ~/.jachin/.env，使 PUPPETEER_EXECUTABLE_PATH 等生效。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (ROOT / ".env", Path.home() / ".jachin" / ".env"):
        if p.is_file():
            load_dotenv(p, encoding="utf-8")


def _log_puppeteer_browser_env() -> None:
    pep = (os.environ.get("PUPPETEER_EXECUTABLE_PATH") or "").strip()
    if pep:
        short = pep if len(pep) <= 60 else pep[:28] + "…" + pep[-24:]
        print(f"[puppeteer-test] 将使用 PUPPETEER_EXECUTABLE_PATH={short}", flush=True)
    else:
        print(
            "[puppeteer-test] 当前进程**未**设置 PUPPETEER_EXECUTABLE_PATH。"
            "若你只改了「系统环境变量」，请**关闭并重新打开 PowerShell**后再运行；"
            "若写在 .env，请确认路径为仓库根或 ~/.jachin/.env（本脚本已尝试加载）。"
            "使用 L3 时还须在 mcp_servers.json 的 official-mcp-puppeteer.env 里声明。",
            flush=True,
        )


def _http_post_json(
    url: str, body: dict[str, Any], *, timeout: float
) -> tuple[int, dict[str, Any] | str]:
    try:
        import urllib.error
        import urllib.request
    except ImportError:
        return 0, "urllib_unavailable"

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    except Exception as e:
        return 0, f"request_failed:{e}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return code, f"not_dict:{raw[:500]}"
        return code, parsed
    except json.JSONDecodeError:
        return code, raw


def _build_l3_agent_user_input(target_url: str) -> str:
    return (
        "【最高优先级·必读】测试7（Console/运行时错误）**禁止**以「工具清单没有 Console 工具」「无日志相关工具」"
        "「无法完全验证」等理由输出 pass=false——此类理由视为**无效、违规**，因官方 MCP 本就不提供 get_console_logs。"
        "测试7**唯一**验收路径：用 **mcp:puppeteer_evaluate** 执行下文「脚本A」与「脚本B」。"
        "**evidence 数组中必须至少包含一条**能证明已执行脚本A（含 **__K11_SMOKTEST_ERR__** 或「脚本A」字样）"
        "及脚本B（含返回数组摘要或「脚本B」字样）；否则视为未完成测试7。\n\n"
        "你是站点验收助手（K11 冒烟扩展）。你必须通过「可用工具」中与无头浏览器/Puppeteer 相关的 MCP 工具完成验证"
        "（工具 id 通常为 mcp:puppeteer_navigate、mcp:puppeteer_evaluate、mcp:puppeteer_screenshot、mcp:puppeteer_click 等，"
        "以本回合系统注入的工具清单为准；若前缀略有差异，以清单中的实际 id 为准）。"
        "禁止不调用工具仅凭猜测作答。\n\n"
        f"验收页面：{target_url}\n\n"
        "**测试1 — 环境访问**：使用导航类工具打开上述 URL。"
        "根据 Observation 判断页面是否成功加载（若出现 Navigation timeout、Could not find Chrome、"
        "net::ERR 等错误则测试1 不通过）。"
        "若 Observation 为 JSON 且含 **foreground_sync_budget_exceeded**，表示 **L3 前台同步超时（通常约 5s）**，"
        "与站点可达性无关；须在结论中明确写出该原因，**不要**将其等同于「网站打不开」。\n\n"
        "**测试2 — 首页加载**：导航成功后，用 puppeteer_evaluate 等评估首屏，例如 document.readyState、"
        "document.body、主内容区文本量；若几乎无文本且结构异常，判为疑似白屏或异常。\n\n"
        "**测试3 — 页面标题（K11 P0）**：用 evaluate 读取 document.title，"
        "期望与产品一致（通常为 **KalaroKo**，允许前后空白；若站点使用变体请在 detail 写明实际值与是否可接受）。\n\n"
        "**测试4 — 主按钮 Play Now!（K11 P0）**：确认存在文案含 **Play Now**（或全名 Play Now!）的主按钮/链接，"
        "在 DOM 中可见；在可行时用 puppeteer_click 点击一次并观察是否无致命报错（若点击会跳转外链可只验证可见与可点）。\n\n"
        "**测试5 — 分类切换（K11 P0）**：首页分类 **All / 1 vs 1 / Party / Live**（文案允许大小写或略写差异）"
        "应能依次点击切换，且切换后界面有合理变化（可用 evaluate 对比切换前后 URL、aria-selected、或关键区块文本）。\n\n"
        "**测试6 — 游戏卡片展示（K11 P0）**：首页应能看到游戏卡片列表；用 evaluate 检查卡片容器内是否有"
        "图片（img 自然宽/高或 src）、标题/名称文本，无明显整块缺失（若懒加载可说明需滚动后二次检查）。\n\n"
        "**测试7 — 页面无严重报错 / Console（K11 P0）**（**禁止**以「没有专用 Console 工具」为由判 FAIL）：\n"
        "- 官方 Puppeteer MCP **没有**单独的 get_console_logs 工具是**正常**的；**必须**用 **mcp:puppeteer_evaluate** "
        "在页面内注入采集逻辑，这是本项的标准做法。\n"
        "- **时机**：在 **mcp:puppeteer_navigate 首次成功打开目标页之后**，立刻执行一次 evaluate 注入下面「脚本A」；"
        "在完成测试4/5 的点击、切换等操作**之后**，再执行一次 evaluate 运行「脚本B」取回累计错误列表。\n"
        "- **跨页与重载**：脚本A 使用 **sessionStorage**（键名固定）持久化，避免 SPA 内路由或部分重载导致 "
        "**仅存在 window 上的数组丢失**；若发生**整页跳转到不同 origin**导致 sessionStorage 不可用，"
        "须在新文档中**重新注入脚本A** 再继续操作，最后在停留页执行脚本B。\n"
        "- **判定**：脚本B 返回的数组若 **含任一** type 为 error / onerror / console_error 的条目，"
        "且消息看起来属于**应用严重错误**（非可忽略的第三方统计噪声可在 detail 说明），则 test7 pass=false 并列出摘要；"
        "若数组为空或仅有可忽略的噪音，则 pass=true。\n"
        "脚本A（注入监听，整段作为 puppeteer_evaluate 的 script 一次执行）：\n"
        "```javascript\n"
        "(function(){var K='__K11_SMOKTEST_ERR__';function L(){try{return JSON.parse("
        "sessionStorage.getItem(K)||'[]')}catch(e){return[]}}function S(a){sessionStorage.setItem(K,"
        "JSON.stringify(a))}var buf=L();window.__K11_TEST_ERRORS__=buf;function P(){"
        "S(window.__K11_TEST_ERRORS__)}window.addEventListener('error',function(ev){"
        "window.__K11_TEST_ERRORS__.push({type:'error',msg:String(ev.message||''),"
        "filename:String(ev.filename||''),lineno:ev.lineno});P()},true);var oe=window.onerror;"
        "window.onerror=function(msg,src,line,col,err){window.__K11_TEST_ERRORS__.push({type:'onerror',"
        "msg:String(msg),src:String(src||''),line:line,col:col});P();return oe?oe.apply(this,arguments):false};"
        "var ce=console.error;console.error=function(){var s=Array.prototype.slice.call(arguments).map(String).join(' ');"
        "window.__K11_TEST_ERRORS__.push({type:'console_error',msg:s});P();return ce.apply(console,arguments)};})();\n"
        "```\n"
        "脚本B（读取累计，作为最后一次 evaluate）：\n"
        "```javascript\n"
        "(function(){try{return JSON.parse(sessionStorage.getItem('__K11_SMOKTEST_ERR__')||'[]')}catch(e){return[]}})()\n"
        "```\n"
        "**建议工具顺序**：mcp:puppeteer_navigate → **脚本A** → 测试2～6 所需的 evaluate/click → **脚本B** → 输出 Final Answer。\n\n"
        "最后一轮输出必须包含一段可解析的 JSON（可放在 markdown 代码块内），格式严格如下（键名勿改）：\n"
        "```json\n"
        "{\n"
        '  "test1_environment_access": { "pass": true, "detail": "" },\n'
        '  "test2_first_screen": { "pass": true, "detail": "" },\n'
        '  "test3_page_title_kalaroko": { "pass": true, "detail": "" },\n'
        '  "test4_play_now_button": { "pass": true, "detail": "" },\n'
        '  "test5_category_tabs": { "pass": true, "detail": "" },\n'
        '  "test6_game_cards": { "pass": true, "detail": "" },\n'
        '  "test7_console_no_severe": { "pass": true, "detail": "" },\n'
        '  "evidence": ["须含脚本A/脚本B 与 __K11_SMOKTEST_ERR__ 相关证明", "…"]\n'
        "}\n"
        "```\n\n"
        "若某步失败或无法验证，对应 pass 填 false，并在 detail 写明原因；测试1/2 失败时后续项可标 false 并说明依赖未满足。"
        "**再次强调测试7**：不得以「没有工具」为借口；只能依据脚本B 返回数组是否含严重错误来判 pass。"
    )


_LEGACY_TEST7_EXCUSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"工具清单.*无.*Console", re.I),
    re.compile(r"没有.*Console.*工具", re.I),
    re.compile(r"无.*Console.*日志", re.I),
    re.compile(r"无法完全验证", re.I),
    re.compile(r"无.*相关工具.*验证", re.I),
)


def _is_legacy_test7_excuse(detail: str) -> bool:
    """模型常用「无 Console 工具」搪塞测试7，与既定 evaluate 方案矛盾。"""
    d = (detail or "").strip()
    if not d:
        return False
    return any(p.search(d) for p in _LEGACY_TEST7_EXCUSE_PATTERNS)


def _evidence_mentions_k11_console_protocol(ev: Any) -> bool:
    """evidence 是否体现已执行脚本A/B（sessionStorage 键）。"""
    if not isinstance(ev, list):
        return False
    blob = " ".join(str(x) for x in ev).lower()
    return (
        "__k11_smoktest_err__" in blob
        or "k11_smoktest" in blob
        or "脚本a" in blob
        or "脚本b" in blob
    )


def _sanitize_json_text(s: str) -> str:
    """模型偶发使用 NBSP（U+00A0）等不可见字符，会导致 json.loads 失败。"""
    if not s:
        return s
    return (
        s.replace("\u00a0", " ")
        .replace("\ufeff", "")
        .replace("\u200b", "")
    )


def _parse_report_json(answer: str) -> dict[str, Any] | None:
    if not (answer or "").strip():
        return None
    text = answer.strip()
    candidates: list[str] = []
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        candidates.append(m.group(1).strip())
    lb, rb = text.find("{"), text.rfind("}")
    if lb != -1 and rb > lb:
        candidates.append(text[lb : rb + 1])
    for frag in candidates:
        frag = _sanitize_json_text(frag)
        try:
            o = json.loads(frag)
            if isinstance(o, dict) and "test1_environment_access" in o:
                return o
        except json.JSONDecodeError:
            continue
    return None


def _print_l3_foreground_debug() -> None:
    """打印本机 nexus foreground_tools 与 Puppeteer 是否豁免，便于对照日志。"""
    print("[puppeteer-test] ---------- L3 前台工具策略（本机进程可读） ----------", flush=True)
    nexus = Path.home() / ".jachin" / "nexus_config.json"
    print(f"[puppeteer-test] nexus_config: {nexus}  exists={nexus.is_file()}", flush=True)
    try:
        from l3_node.foreground_tool_policy import (
            load_foreground_tools_config,
            tool_bypasses_foreground_timeout,
        )

        cfg = load_foreground_tools_config()
        sec = float(cfg.get("sync_timeout_sec") or 5.0)
        en = bool(cfg.get("enabled", True))
        nav_bypass = tool_bypasses_foreground_timeout(
            "mcp:puppeteer_navigate",
            cfg,
            mcp_declares_long_running=False,
        )
        print(
            f"[puppeteer-test] foreground_tools.enabled={en}  sync_timeout_sec={sec}",
            flush=True,
        )
        print(
            "[puppeteer-test] 说明: 未豁免的前台 MCP 工具会被 L3 用 asyncio.wait_for 包一层；"
            "超限时 Observation 为 JSON，reason=foreground_sync_budget_exceeded（与 Puppeteer 默认导航 30s 不同层）。",
            flush=True,
        )
        print(
            f"[puppeteer-test] mcp:puppeteer_navigate 豁免前台超时: {nav_bypass}",
            flush=True,
        )
        ap = cfg.get("allow_prefixes") or []
        lr = cfg.get("long_running_tool_ids") or []
        print(f"[puppeteer-test] allow_prefixes: {ap}", flush=True)
        print(f"[puppeteer-test] long_running_tool_ids: {lr}", flush=True)
        if not nav_bypass:
            print(
                "[puppeteer-test] 建议: 在 nexus_config.json 的 foreground_tools 中增加 "
                '"allow_prefixes": ["mcp:puppeteer"] 或提高 sync_timeout_sec，然后重启 L3。',
                file=sys.stderr,
                flush=True,
            )
    except Exception as e:
        print(
            f"[puppeteer-test] 无法导入 L3 foreground 诊断（请在仓库根执行且环境含 l3_node）: {e}",
            flush=True,
        )


def _diagnose_answer_for_debug(answer: str) -> None:
    """从模型回答中提取常见失败模式并给出解释（不改变退出码逻辑）。"""
    a = answer or ""
    print("\n[puppeteer-test] ---------- 自动诊断（关键词） ----------", flush=True)
    if "foreground_sync_budget_exceeded" in a:
        print(
            "[puppeteer-test] · 发现 foreground_sync_budget_exceeded → L3 前台同步预算用尽（默认常 5s），"
            "已在仓库 foreground_tool_policy 默认加入前缀豁免 mcp:puppeteer；若仍出现请重启 L3 并检查 nexus_config 是否覆盖。",
            flush=True,
        )
    if "Navigation timeout" in a or "navigation timeout" in a.lower():
        print(
            "[puppeteer-test] · 发现 Navigation timeout → Puppeteer/Chromium 侧页面加载超时（常见 30000ms），"
            "与 L3 5s 预算不同；可检查代理 PUPPETEER_LAUNCH_OPTIONS、站点首屏速度。",
            flush=True,
        )
    if "Could not find Chrome" in a or "could not find chrome" in a.lower():
        print(
            "[puppeteer-test] · 发现 Could not find Chrome → 浏览器二进制未找到，检查 PUPPETEER_EXECUTABLE_PATH。",
            flush=True,
        )
    if not any(
        k in a.lower()
        for k in (
            "foreground_sync_budget_exceeded",
            "navigation timeout",
            "could not find chrome",
        )
    ):
        print(
            "[puppeteer-test] · 未命中常见失败关键词；若仍 FAIL，请结合完整回答与 L3 日志排查。",
            flush=True,
        )
    if "工具清单" in a and "console" in a.lower():
        print(
            "[puppeteer-test] · 若因「无 Console 工具」判测试7 失败：属陈旧策略；应用 mcp:puppeteer_evaluate + 脚本A/B。",
            flush=True,
        )


def _run_l3_agent_herontest(
    target_url: str,
    l3_base: str,
    max_iterations: int,
    *,
    soft_pass_legacy_test7: bool = False,
) -> int:
    base = l3_base.rstrip("/")
    url = f"{base}/api/v3/agent/run"
    body: dict[str, Any] = {
        "user_input": _build_l3_agent_user_input(target_url),
        "max_iterations": max_iterations,
        "implicit_attribution": {"channel": "http_herontest_puppeteer_script"},
    }
    print(
        f"[puppeteer-test] POST {url}（L3 Agent 调度 MCP 工具）max_iterations={max_iterations}",
        flush=True,
    )
    print(f"[puppeteer-test] target_url={target_url!r}", flush=True)
    _print_l3_foreground_debug()
    t0 = time.perf_counter()
    code, payload = _http_post_json(url, body, timeout=600.0)
    elapsed = time.perf_counter() - t0
    print(f"[puppeteer-test] HTTP 往返耗时: {elapsed:.2f}s (status={code})", flush=True)
    if isinstance(payload, str):
        if payload == "urllib_unavailable":
            print("[puppeteer-test] urllib 不可用", file=sys.stderr)
            return 2
        print(f"[puppeteer-test] 请求失败: {payload}", file=sys.stderr)
        return 1
    if code == 503 or (
        isinstance(payload.get("error"), str) and "尚未就绪" in payload["error"]
    ):
        print(
            "[puppeteer-test] L3 Agent 引擎未就绪。请在本机先启动 L3（例如 "
            "`python -m l3_node --ws-only` 或 `--gateway`），并确保 HTTP 端口与 --l3-base 一致。",
            file=sys.stderr,
        )
        print(f"[puppeteer-test] 详情: {payload}", file=sys.stderr)
        return 1
    if code >= 400:
        print(f"[puppeteer-test] HTTP {code}: {payload}", file=sys.stderr)
        return 1
    err = payload.get("error")
    if err:
        print(f"[puppeteer-test] L3 返回错误: {err}", file=sys.stderr)
        return 1
    answer = (payload.get("answer") or "").strip()
    print("\n[puppeteer-test] ========== L3 原始回答 ==========\n", flush=True)
    print(answer[:24000], flush=True)
    if len(answer) > 24000:
        print("\n…（已截断）", flush=True)

    _diagnose_answer_for_debug(answer)

    report = _parse_report_json(answer)
    print("\n[puppeteer-test] ========== 测试结果汇总 ==========", flush=True)
    if not report:
        print(
            "  （未能从回答中解析 JSON 汇总块；请人工查看上方「L3 原始回答」）",
            flush=True,
        )
        return 1
    _rows: list[tuple[str, str, Any]] = [
        ("测试1 环境访问", "test1_environment_access", report.get("test1_environment_access")),
        ("测试2 首页加载", "test2_first_screen", report.get("test2_first_screen")),
        ("测试3 页面标题 KalaroKo", "test3_page_title_kalaroko", report.get("test3_page_title_kalaroko")),
        ("测试4 Play Now!", "test4_play_now_button", report.get("test4_play_now_button")),
        ("测试5 分类切换", "test5_category_tabs", report.get("test5_category_tabs")),
        ("测试6 游戏卡片", "test6_game_cards", report.get("test6_game_cards")),
        ("测试7 Console 无严重报错", "test7_console_no_severe", report.get("test7_console_no_severe")),
    ]
    passes: list[bool] = []
    for label, _key, node in _rows:
        p = bool(node.get("pass")) if isinstance(node, dict) else False
        d = (node.get("detail") if isinstance(node, dict) else "") or ""
        passes.append(p)
        miss = "（JSON 未返回该项，视为未执行）" if not isinstance(node, dict) else ""
        print(f"  {label}: {'PASS' if p else 'FAIL'} — {d}{miss}", flush=True)
    ev = report.get("evidence")
    if isinstance(ev, list):
        for i, e in enumerate(ev[:24], 1):
            print(f"  证据{i}: {e}", flush=True)
    t7_node = report.get("test7_console_no_severe")
    t7_detail = (t7_node.get("detail") if isinstance(t7_node, dict) else "") or ""
    t7_pass = bool(t7_node.get("pass")) if isinstance(t7_node, dict) else False
    ev_proto = _evidence_mentions_k11_console_protocol(ev)
    if not t7_pass and _is_legacy_test7_excuse(t7_detail):
        print(
            "\n[puppeteer-test] ⚠ 测试7 使用了已禁止的「无 Console 工具」类借口；"
            "这通常表示模型未按脚本A/B 执行，而非页面必有问题。请提高 --max-iterations 或重启 L3 后重试。",
            file=sys.stderr,
            flush=True,
        )
    if not t7_pass and not ev_proto and not _is_legacy_test7_excuse(t7_detail):
        print(
            "\n[puppeteer-test] 提示: evidence 未出现脚本A/B 或 __K11_SMOKTEST_ERR__ 痕迹，无法确认已按规范采集 Console。",
            file=sys.stderr,
            flush=True,
        )
    all_ok = all(passes) if passes else False
    print(f"\n[puppeteer-test] 总评: {'全部通过（7 项）' if all_ok else '存在未通过项'}", flush=True)
    if (
        soft_pass_legacy_test7
        and len(passes) >= 7
        and all(passes[:6])
        and not passes[6]
        and _is_legacy_test7_excuse(t7_detail)
    ):
        print(
            "[puppeteer-test] --soft-pass-legacy-test7：测试1～6 均 PASS，"
            "测试7 仅为陈旧借口 → **退出码 0（软通过，仅建议本地调试用）**",
            file=sys.stderr,
            flush=True,
        )
        return 0
    return 0 if all_ok else 1


def _ensure_mcp_sdk():
    global ClientSession, StdioServerParameters, stdio_client
    try:
        from mcp import ClientSession as CS, StdioServerParameters as SSP
        from mcp.client.stdio import stdio_client as sc

        ClientSession, StdioServerParameters, stdio_client = CS, SSP, sc
    except ImportError:
        print("缺少 mcp SDK，请安装: pip install mcp>=1.0.0", file=sys.stderr)
        raise SystemExit(2)


ClientSession = StdioServerParameters = stdio_client = None  # type: ignore[misc,assignment]


def _text_from_result(result) -> str:
    parts: list[str] = []
    for block in result.content or []:
        if hasattr(block, "text"):
            parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(parts).strip()


def _is_navigation_timeout_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    if "navigation timeout" in s or ("timeout of" in s and "ms exceeded" in s):
        return True
    subs = getattr(exc, "exceptions", None)
    if subs:
        return any(_is_navigation_timeout_error(e) for e in subs)
    if exc.__cause__ is not None:
        return _is_navigation_timeout_error(exc.__cause__)
    return False


def _print_navigation_timeout_help(target_url: str) -> None:
    print(
        f"\n[puppeteer-test] 导航超时（默认 30s）：官方 MCP 内为 page.goto(url)，未暴露更长 timeout。\n"
        f"  常见原因：目标站 {target_url!r} 首屏慢、资源多、或 **Chrome 未走代理**（仅设 HTTP_PROXY 往往不够，需 --proxy-server）。\n"
        "  处理建议：\n"
        "  1) 本脚本已尝试把 HTTP(S)_PROXY 合并进 PUPPETEER_LAUNCH_OPTIONS（见下方启动日志）。\n"
        "  2) 在 mcp_servers.json 的 env 中增加：\n"
        '       "PUPPETEER_LAUNCH_OPTIONS": "{\\"args\\":[\\"--proxy-server=http://127.0.0.1:8800\\"]}"\n'
        "     （端口与 QuickQ「http」一致）\n"
        "  3) 用本机浏览器打开同一 URL，看是否 >30s 才可用。\n"
        "  4) 先跑：`python scripts/test_puppeteer_mcp_herontest.py --smoke-url https://example.com/` 确认 MCP 通路。\n",
        file=sys.stderr,
        flush=True,
    )


def _apply_proxy_to_puppeteer_launch_options(env: dict[str, str]) -> None:
    """Chromium 往往不读 HTTP_PROXY，需通过 launch args 传 --proxy-server。"""
    proxy = (env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or "").strip()
    if not proxy:
        return
    lo_raw = env.get("PUPPETEER_LAUNCH_OPTIONS") or "{}"
    try:
        lo = json.loads(lo_raw)
        if not isinstance(lo, dict):
            lo = {}
    except json.JSONDecodeError:
        lo = {}
    args = list(lo.get("args") or [])
    flag = f"--proxy-server={proxy}"
    if not any(str(a).startswith("--proxy-server=") for a in args):
        args.append(flag)
        lo["args"] = args
        env["PUPPETEER_LAUNCH_OPTIONS"] = json.dumps(lo, ensure_ascii=False, separators=(",", ":"))
        print(f"[puppeteer-test] 已注入 Chrome 代理参数: {flag}", flush=True)


def _is_chrome_not_found_error(exc: BaseException) -> bool:
    """匹配 Puppeteer 未找到 Chromium / Chrome 的典型报错（含 ExceptionGroup 包裹）。"""
    msg = str(exc).lower()
    if "could not find chrome" in msg:
        return True
    subs = getattr(exc, "exceptions", None)
    if subs:
        return any(_is_chrome_not_found_error(e) for e in subs)
    if exc.__cause__ is not None:
        return _is_chrome_not_found_error(exc.__cause__)
    if exc.__context__ is not None and exc.__context__ is not exc:
        return _is_chrome_not_found_error(exc.__context__)
    return False


def _print_chrome_install_help() -> None:
    print(
        "\n[puppeteer-test] Puppeteer 仍找不到可用浏览器。\n"
        "请在本机**任选其一**：\n"
        "  1) 下载官方缓存 Chromium：\n"
        "       npx puppeteer browsers install chrome\n"
        "     或：python scripts/test_puppeteer_mcp_herontest.py --install-chrome\n"
        "  2) 使用已安装的 Chrome：在**本终端**或仓库根 `.env` 设置\n"
        "       PUPPETEER_EXECUTABLE_PATH=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\n"
        "     然后**新开终端**或保存 .env 后重跑本脚本。\n"
        "  3) 缓存目录见报错；详见 https://pptr.dev/guides/configuration\n",
        file=sys.stderr,
        flush=True,
    )


def install_puppeteer_chrome_cache() -> int:
    """
    调用官方 CLI 下载与当前 Puppeteer 匹配的 Chromium（与报错提示一致）。
    等价于：npx puppeteer browsers install chrome
    """
    if sys.platform == "win32":
        cmd = ["cmd.exe", "/c", "npx", "-y", "puppeteer", "browsers", "install", "chrome"]
    else:
        cmd = ["npx", "-y", "puppeteer", "browsers", "install", "chrome"]
    print("[puppeteer-test] 执行: " + " ".join(cmd), flush=True)
    print("[puppeteer-test] 首次下载 Chromium 可能需数分钟，请耐心等待…", flush=True)
    env = dict(os.environ)
    r = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if r.returncode != 0:
        print("[puppeteer-test] 浏览器安装命令失败，请检查网络/代理后重试。", file=sys.stderr)
    return r.returncode


def _stdio_params_puppeteer() -> StdioServerParameters:
    """与 config/mcp_servers.json.example 中 official-mcp-puppeteer 对齐。"""
    env: dict[str, str] = {k: str(v) if v is not None else "" for k, v in os.environ.items()}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    _apply_proxy_to_puppeteer_launch_options(env)
    if sys.platform == "win32":
        return StdioServerParameters(
            command="cmd.exe",
            args=["/c", "npx", "-y", "@modelcontextprotocol/server-puppeteer"],
            env=env,
        )
    return StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-puppeteer"],
        env=env,
    )


async def _navigate_or_bail(session: Any, nav: str, url: str) -> tuple[bool, str]:
    """调用 puppeteer_navigate；失败时分类处理。返回 (ok, text)。"""
    try:
        out = await session.call_tool(nav, {"url": url})
        return True, _text_from_result(out)
    except BaseException as e:
        if _is_chrome_not_found_error(e):
            _print_chrome_install_help()
            return False, ""
        if _is_navigation_timeout_error(e):
            _print_navigation_timeout_help(url)
            return False, ""
        raise


async def _run_stdio_herontest(target_url: str, smoke_url: str | None) -> int:
    _ensure_mcp_sdk()
    params = _stdio_params_puppeteer()
    print("[puppeteer-test] 启动 MCP stdio（npx @modelcontextprotocol/server-puppeteer）…", flush=True)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [t.name for t in listed.tools]
                print("[puppeteer-test] 可用工具:", names, flush=True)

                nav = "puppeteer_navigate"
                if nav not in names:
                    for cand in ("navigate", "mcp_puppeteer_navigate"):
                        if cand in names:
                            nav = cand
                            break
                    else:
                        print("[puppeteer-test] 未找到导航工具，请根据上方列表改脚本中的 nav 名称。", file=sys.stderr)
                        return 2

                if smoke_url:
                    print(f"[puppeteer-test] 探针页（应很快）: {smoke_url}", flush=True)
                    ok_s, t_s = await _navigate_or_bail(session, nav, smoke_url)
                    if not ok_s:
                        return 4
                    print(f"[puppeteer-test] {nav} 探针返回:\n{t_s[:800]}", flush=True)

                print(f"[puppeteer-test] 目标页: {target_url}", flush=True)
                ok1, t1 = await _navigate_or_bail(session, nav, target_url)
                if not ok1:
                    return 4
                print(f"[puppeteer-test] {nav} 返回:\n{t1[:4000]}", flush=True)

                ev = "puppeteer_evaluate"
                if ev in names:
                    try:
                        out2 = await session.call_tool(
                            ev,
                            {"script": "document.title + ' | ' + location.href"},
                        )
                        t2 = _text_from_result(out2)
                        print(f"[puppeteer-test] {ev} (title|href):\n{t2[:2000]}", flush=True)
                    except BaseException as e:
                        if _is_chrome_not_found_error(e):
                            _print_chrome_install_help()
                            return 3
                        if _is_navigation_timeout_error(e):
                            _print_navigation_timeout_help(target_url)
                            return 4
                        print(f"[puppeteer-test] {ev} 失败: {e}", flush=True)
                else:
                    print("[puppeteer-test] 无 puppeteer_evaluate，跳过标题检查。", flush=True)
    except BaseException as e:
        if _is_chrome_not_found_error(e):
            _print_chrome_install_help()
            return 3
        if _is_navigation_timeout_error(e):
            _print_navigation_timeout_help(target_url)
            return 4
        raise

    print("[puppeteer-test] stdio 测试结束（成功）。")
    return 0


def _run_mcp_execute(target_url: str, l3_base: str) -> int:
    base = l3_base.rstrip("/")
    url = f"{base}/api/v3/mcp/execute"
    body = {
        "tool_name": "puppeteer_navigate",
        "arguments": {"url": target_url},
    }
    print(f"[puppeteer-test] POST {url}（单次 MCP，非大模型）", flush=True)
    code, payload = _http_post_json(url, body, timeout=120.0)
    if isinstance(payload, str):
        print(f"[puppeteer-test] 请求失败: {payload}", file=sys.stderr)
        return 1
    if code == 401:
        print(
            "提示：开发环境可设置 JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1 后重启 L3。",
            file=sys.stderr,
        )
    if code >= 400:
        print(f"[puppeteer-test] HTTP {code}: {payload}", file=sys.stderr)
        return 1
    raw_preview = json.dumps(payload, ensure_ascii=False)[:8000]
    print(f"[puppeteer-test] 响应:\n{raw_preview}", flush=True)
    if isinstance(payload, dict) and payload.get("ok") is True:
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Herontest：L3 Agent + Puppeteer MCP 验收，或直连 MCP / stdio"
    )
    ap.add_argument(
        "--target-url",
        default="https://www.herontest.xin/",
        help="要验收的页面 URL",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--direct-stdio",
        action="store_true",
        help="不经 L3：本进程直接 npx 拉起 Puppeteer MCP（需 pip install mcp）",
    )
    mode.add_argument(
        "--mcp-execute",
        action="store_true",
        help="不经大模型：POST /api/v3/mcp/execute 单次 puppeteer_navigate",
    )
    ap.add_argument(
        "--via-http",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--l3-base",
        default=os.environ.get("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991"),
        help="L3 HTTP API 根地址（/api/v3/agent/run；与 l3_node L3_HTTP_PORT=18991 一致）",
    )
    ap.add_argument(
        "--max-iterations",
        type=int,
        default=36,
        help="L3 Agent 模式：run_agent ReAct 最大轮数（K11 七项含脚本A/B，默认 36，上限由服务端裁剪）",
    )
    ap.add_argument(
        "--soft-pass-legacy-test7",
        action="store_true",
        help="仅当测试1～6 均 PASS、测试7 以「无Console工具」等陈旧借口失败时，仍返回退出码 0（本地调试用）",
    )
    ap.add_argument(
        "--install-chrome",
        action="store_true",
        help="先执行 npx puppeteer browsers install chrome（仅 direct-stdio 常用）",
    )
    ap.add_argument(
        "--smoke-url",
        default="https://example.com/",
        help="direct-stdio：先打开的轻量探针页",
    )
    ap.add_argument(
        "--no-smoke",
        action="store_true",
        help="direct-stdio：跳过探针页",
    )
    args = ap.parse_args()

    _load_dotenv_for_test()
    _log_puppeteer_browser_env()

    if args.install_chrome:
        rc = install_puppeteer_chrome_cache()
        if rc != 0:
            return rc

    if args.mcp_execute or args.via_http:
        return _run_mcp_execute(args.target_url, args.l3_base)
    if args.direct_stdio:
        smoke = None if args.no_smoke else (args.smoke_url or "").strip() or None
        return asyncio.run(_run_stdio_herontest(args.target_url, smoke))
    return _run_l3_agent_herontest(
        args.target_url,
        args.l3_base,
        args.max_iterations,
        soft_pass_legacy_test7=args.soft_pass_legacy_test7,
    )


if __name__ == "__main__":
    raise SystemExit(main())
